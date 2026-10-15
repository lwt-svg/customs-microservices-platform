package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.loadplanning.LoadPlanVersionController;
import com.customsaffairs.platform.loadplanning.LoadPlanVersionEventResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanVersionResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanVersionService;
import com.customsaffairs.platform.loadplanning.LockLoadPlanVersionRequest;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.client.RestClient;

/** 正式方案锁定、版本历史和 camelCase 平台契约测试。 */
@WebMvcTest(LoadPlanVersionController.class)
@Import(LoadPlanVersionControllerTest.FakePlanVersionConfiguration.class)
class LoadPlanVersionControllerTest {

  @Autowired private MockMvc mockMvc;

  @Autowired private CapturedRequests captured;

  @Test
  void shouldLockApprovedRunThroughPlatformBoundary() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/plan-versions")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "expectedRunVersion": 2,
                      "expectedCurrentVersionNumber": 0,
                      "lockReason": "仓库负责人确认"
                    }
                    """))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$.code").value(0))
        .andExpect(jsonPath("$.data.status").value("LOCKED"))
        .andExpect(jsonPath("$.data.versionNumber").value(1))
        .andExpect(jsonPath("$.data.planSnapshotHash").value("plan-hash"))
        .andExpect(jsonPath("$.data.events[0].eventType").value("PLAN_VERSION_LOCKED"));

    assertThat(captured.runId).isEqualTo("RUN-0001");
    assertThat(captured.lockRequest.expectedRunVersion()).isEqualTo(2);
    assertThat(captured.lockRequest.lockReason()).isEqualTo("仓库负责人确认");
  }

  @Test
  void shouldExposeHistoryCurrentAndDetailPaths() throws Exception {
    mockMvc
        .perform(get("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/plan-versions"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data[0].versionId").value("VERSION-0001"));

    mockMvc
        .perform(get("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/plan-versions/current"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.versionNumber").value(1));

    mockMvc
        .perform(get("/api/v1/platform/load-planning/plan-versions/VERSION-0001"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.planCode").value("BALANCED-001"));
  }

  @Test
  void shouldRejectMissingVersionsOrBlankReason() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/plan-versions")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "expectedRunVersion": 0,
                      "expectedCurrentVersionNumber": -1,
                      "lockReason": " "
                    }
                    """))
        .andExpect(status().isBadRequest());
  }

  @TestConfiguration
  static class FakePlanVersionConfiguration {

    @Bean
    CapturedRequests capturedRequests() {
      return new CapturedRequests();
    }

    @Bean
    LoadPlanVersionService loadPlanVersionService(CapturedRequests captured) {
      return new LoadPlanVersionService(null, RestClient.builder()) {
        @Override
        public LoadPlanVersionResponse lock(
            String runId, LockLoadPlanVersionRequest request) {
          captured.runId = runId;
          captured.lockRequest = request;
          return version();
        }

        @Override
        public List<LoadPlanVersionResponse> list(String taskNumber) {
          return List.of(version());
        }

        @Override
        public LoadPlanVersionResponse current(String taskNumber) {
          return version();
        }

        @Override
        public LoadPlanVersionResponse get(String versionId) {
          return version();
        }
      };
    }
  }

  static class CapturedRequests {
    private String runId;
    private LockLoadPlanVersionRequest lockRequest;
  }

  private static LoadPlanVersionResponse version() {
    ObjectMapper mapper = new ObjectMapper();
    return new LoadPlanVersionResponse(
        "VERSION-0001",
        "LOCKED",
        "LP-DEMO-0001",
        1,
        "RUN-0001",
        "PLAN-0001",
        "BALANCED-001",
        "PALLET-1200X1000",
        null,
        2,
        "LOAD_PLAN_SNAPSHOT_V1",
        "input-hash",
        "constraint-hash",
        "plan-hash",
        "DETERMINISTIC_GRID_STRIP_V1",
        "PALLET_PLAN_SCORE_V1",
        "system:local-demo",
        "本地演示审批人",
        "SYSTEM_DEMO",
        "2026-10-12T08:00:00Z",
        "候选方案复核通过",
        "system:local-demo",
        "本地演示发布人",
        "SYSTEM_DEMO",
        "仓库负责人确认",
        "2026-10-12T08:10:00Z",
        mapper.createObjectNode().put("task_number", "LP-DEMO-0001"),
        mapper.createObjectNode().put("selected_pallet_code", "PALLET-1200X1000"),
        mapper.createObjectNode().put("plan_code", "BALANCED-001"),
        List.of(
            new LoadPlanVersionEventResponse(
                1,
                "PLAN_VERSION_LOCKED",
                "system:local-demo",
                "本地演示发布人",
                "SYSTEM_DEMO",
                null,
                "已锁定为正式版本 V1。",
                "2026-10-12T08:10:00Z")));
  }
}
