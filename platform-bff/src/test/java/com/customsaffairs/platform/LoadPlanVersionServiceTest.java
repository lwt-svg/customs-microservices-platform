package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.loadplanning.LoadPlanVersionService;
import com.customsaffairs.platform.loadplanning.LockLoadPlanVersionRequest;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.client.DefaultServiceInstance;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/** 正式装载方案版本的 Nacos、snake_case 适配、快照和错误映射测试。 */
class LoadPlanVersionServiceTest {

  private MockRestServiceServer mockServer;
  private LoadPlanVersionService service;

  @BeforeEach
  void setUp() {
    RestClient.Builder builder = RestClient.builder();
    mockServer = MockRestServiceServer.bindTo(builder).build();
    service = new LoadPlanVersionService(discoveryClient(), builder);
  }

  @Test
  void shouldLockApprovedRunAndMapImmutableSnapshots() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/plan-versions"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(
            content()
                .json(
                    """
                    {
                      "expected_run_version": 2,
                      "expected_current_version_number": 0,
                      "lock_reason": "仓库负责人确认"
                    }
                    """))
        .andRespond(withSuccess(versionJson(), MediaType.APPLICATION_JSON));

    var response =
        service.lock(
            " RUN-0001 ",
            new LockLoadPlanVersionRequest(2, 0, " 仓库负责人确认 "));

    assertThat(response.versionId()).isEqualTo("VERSION-0001");
    assertThat(response.versionNumber()).isEqualTo(1);
    assertThat(response.status()).isEqualTo("LOCKED");
    assertThat(response.planSnapshotHash()).isEqualTo("plan-hash");
    assertThat(response.planSnapshot().path("plan_code").asText())
        .isEqualTo("BALANCED-001");
    assertThat(response.events()).hasSize(1);
    assertThat(response.events().getFirst().eventType()).isEqualTo("PLAN_VERSION_LOCKED");
    assertThat(response.events().getFirst().detail()).contains("正式版本 V1");
    mockServer.verify();
  }

  @Test
  void shouldListTaskVersionHistory() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/tasks/LP-DEMO-0001/plan-versions"))
        .andExpect(method(HttpMethod.GET))
        .andRespond(
            withSuccess("[" + versionJson() + "]", MediaType.APPLICATION_JSON));

    var versions = service.list("LP-DEMO-0001");

    assertThat(versions).singleElement().extracting(value -> value.versionNumber()).isEqualTo(1);
    mockServer.verify();
  }

  @Test
  void shouldKeepPermissionAndVersionConflictsDistinct() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/plan-versions"))
        .andRespond(withStatus(HttpStatus.FORBIDDEN));
    assertStatus(
        403,
        () ->
            service.lock(
                "RUN-0001",
                new LockLoadPlanVersionRequest(2, 0, "发布")));
    mockServer.verify();

    RestClient.Builder conflictBuilder = RestClient.builder();
    mockServer = MockRestServiceServer.bindTo(conflictBuilder).build();
    service = new LoadPlanVersionService(discoveryClient(), conflictBuilder);
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/plan-versions"))
        .andRespond(withStatus(HttpStatus.CONFLICT));
    assertStatus(
        409,
        () ->
            service.lock(
                "RUN-0001",
                new LockLoadPlanVersionRequest(2, 0, "发布")));
    mockServer.verify();
  }

  @Test
  void shouldReturn503WhenNacosHasNoLoadPlanningInstance() {
    var unavailableService =
        new LoadPlanVersionService(emptyDiscoveryClient(), RestClient.builder());

    assertStatus(503, () -> unavailableService.current("LP-DEMO-0001"));
  }

  private static void assertStatus(int expectedStatus, Runnable invocation) {
    assertThatThrownBy(invocation::run)
        .isInstanceOfSatisfying(
            ResponseStatusException.class,
            exception -> assertThat(exception.getStatusCode().value()).isEqualTo(expectedStatus));
  }

  private static DiscoveryClient discoveryClient() {
    return new DiscoveryClient() {
      @Override
      public String description() {
        return "正式装载方案版本测试服务发现客户端";
      }

      @Override
      public List<ServiceInstance> getInstances(String serviceId) {
        if (!"load-planning-service".equals(serviceId)) {
          return List.of();
        }
        return List.of(
            new DefaultServiceInstance(
                "synthetic-load-planning-instance", serviceId, "127.0.0.1", 8010, false));
      }

      @Override
      public List<String> getServices() {
        return List.of("load-planning-service");
      }
    };
  }

  private static DiscoveryClient emptyDiscoveryClient() {
    return new DiscoveryClient() {
      @Override
      public String description() {
        return "空正式装载方案版本测试服务发现客户端";
      }

      @Override
      public List<ServiceInstance> getInstances(String serviceId) {
        return List.of();
      }

      @Override
      public List<String> getServices() {
        return List.of();
      }
    };
  }

  private static String versionJson() {
    return """
        {
          "version_id": "VERSION-0001",
          "status": "LOCKED",
          "task_number": "LP-DEMO-0001",
          "version_number": 1,
          "source_run_id": "RUN-0001",
          "source_plan_id": "PLAN-0001",
          "plan_code": "BALANCED-001",
          "selected_pallet_code": "PALLET-1200X1000",
          "supersedes_version_id": null,
          "source_run_version": 2,
          "snapshot_schema_version": "LOAD_PLAN_SNAPSHOT_V1",
          "input_snapshot_hash": "input-hash",
          "constraint_snapshot_hash": "constraint-hash",
          "plan_snapshot_hash": "plan-hash",
          "algorithm_version": "DETERMINISTIC_GRID_STRIP_V1",
          "score_version": "PALLET_PLAN_SCORE_V1",
          "approval_actor_subject": "system:local-demo",
          "approval_actor_display_name": "本地演示审批人",
          "approval_actor_source": "SYSTEM_DEMO",
          "approved_at": "2026-10-12T08:00:00Z",
          "approval_comment": "候选方案复核通过",
          "locked_by_subject": "system:local-demo",
          "locked_by_display_name": "本地演示发布人",
          "locked_by_source": "SYSTEM_DEMO",
          "lock_reason": "仓库负责人确认",
          "locked_at": "2026-10-12T08:10:00Z",
          "input_snapshot": {"task_number": "LP-DEMO-0001"},
          "constraint_snapshot": {"selected_pallet_code": "PALLET-1200X1000"},
          "plan_snapshot": {"plan_code": "BALANCED-001", "pallets": []},
          "events": [{
            "sequence": 1,
            "event_type": "PLAN_VERSION_LOCKED",
            "actor_subject": "system:local-demo",
            "actor_display_name": "本地演示发布人",
            "actor_source": "SYSTEM_DEMO",
            "request_id": null,
            "detail": "已锁定为正式版本 V1。",
            "occurred_at": "2026-10-12T08:10:00Z"
          }]
        }
        """;
  }
}
