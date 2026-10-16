package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.loadplanning.CreateLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.DecideLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.GenerateLoadPlanningPlanInsightRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentAnswerRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentDecision;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentRunService;
import com.customsaffairs.platform.loadplanning.LoadPlanningStrategyCode;
import com.customsaffairs.platform.loadplanning.ReplanLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.ResumeLoadPlanningAgentRunRequest;
import com.fasterxml.jackson.databind.ObjectMapper;
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

/** 状态化智能装托代理的服务发现、字段适配、嵌套响应和错误翻译测试。 */
class LoadPlanningAgentRunServiceTest {

  private final ObjectMapper objectMapper = new ObjectMapper();

  private MockRestServiceServer mockServer;
  private LoadPlanningAgentRunService service;

  @BeforeEach
  void setUp() {
    RestClient.Builder builder = RestClient.builder();
    mockServer = MockRestServiceServer.bindTo(builder).build();
    service = new LoadPlanningAgentRunService(discoveryClient(), builder);
  }

  @Test
  void shouldCreateRunWithPythonSnakeCaseAndMapNestedPlan() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/tasks/LP-DEMO-0001/agent-runs"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(
            content()
                .json(
                    """
                        {
                          "selected_pallet_code": "PALLET-1200X800",
                          "strategy_codes": ["STABLE", "BALANCED"]
                        }
                        """))
        .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

    var response =
        service.createRun(
            " LP-DEMO-0001 ",
            new CreateLoadPlanningAgentRunRequest(
                " PALLET-1200X800 ",
                List.of(LoadPlanningStrategyCode.STABLE, LoadPlanningStrategyCode.BALANCED)));

    assertThat(response.runId()).isEqualTo("RUN-0001");
    assertThat(response.currentStep()).isEqualTo("WAITING_DECISION");
    assertThat(response.questions().getFirst().minValue()).isEqualByComparingTo("1");
    assertThat(response.events().getFirst().eventType()).isEqualTo("PLAN_GENERATED");
    assertThat(response.plans().getFirst().metrics().volumeUtilization())
        .isEqualByComparingTo("0.82");
    assertThat(response.plans().getFirst().metrics().layerAreaUtilization())
        .isEqualByComparingTo("0.82");
    assertThat(response.plans().getFirst().pallets().getFirst().layerCount()).isEqualTo(2);
    assertThat(response.plans().getFirst().pallets().getFirst().balanceOffset())
        .isEqualByComparingTo("0.04");
    assertThat(response.plans().getFirst().pallets().getFirst().placements()).hasSize(2);
    assertThat(response.plans().getFirst().pallets().getFirst().placements().getFirst().layer())
        .isEqualTo(1);
    assertThat(response.plans().getFirst().pallets().getFirst().placements().get(1).orientation())
        .isEqualTo("ROTATED_90");
    mockServer.verify();
  }

  @Test
  void shouldForwardOptionalIdempotencyKeyForCreateRetry() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/tasks/LP-DEMO-0001/agent-runs"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(header("Idempotency-Key", "browser-request-001"))
        .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

    service.createRun(
        "LP-DEMO-0001",
        new CreateLoadPlanningAgentRunRequest(
            null, List.of(LoadPlanningStrategyCode.STABLE)),
        " browser-request-001 ");

    mockServer.verify();
  }

  @Test
  void shouldFetchCompleteRunHistory() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/tasks/LP-DEMO-0001/agent-runs/history"))
        .andExpect(method(HttpMethod.GET))
        .andRespond(withSuccess("[" + runJson() + "]", MediaType.APPLICATION_JSON));

    var history = service.listRunHistory(" LP-DEMO-0001 ");

    assertThat(history).hasSize(1);
    assertThat(history.getFirst().runId()).isEqualTo("RUN-0001");
    assertThat(history.getFirst().plans()).hasSize(1);
    mockServer.verify();
  }

  @Test
  void shouldForwardReplanIdempotencyKeyAndSnakeCasePayload() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/replan"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(header("Idempotency-Key", "replan-request-001"))
        .andExpect(
            content()
                .json(
                    """
                    {
                      "reason": "现场要求降低堆高",
                      "strategy_codes": ["STABLE"]
                    }
                    """))
        .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

    service.replanRun(
        "RUN-0001",
        new ReplanLoadPlanningAgentRunRequest(
            " 现场要求降低堆高 ",
            null,
            List.of(LoadPlanningStrategyCode.STABLE)),
        " replan-request-001 ");

    mockServer.verify();
  }

  @Test
  void shouldGeneratePlanInsightAndMapSnakeCaseResponse() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/plan-insights"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(content().json("{\"plan_code\":\"PLAN-STABLE-001\"}"))
        .andRespond(withSuccess(planInsightJson(), MediaType.APPLICATION_JSON));

    var response =
        service.generatePlanInsight(
            " RUN-0001 ", new GenerateLoadPlanningPlanInsightRequest(" PLAN-STABLE-001 "));

    assertThat(response.runId()).isEqualTo("RUN-0001");
    assertThat(response.planCode()).isEqualTo("PLAN-STABLE-001");
    assertThat(response.provider()).isEqualTo("OPENAI");
    assertThat(response.degraded()).isFalse();
    assertThat(response.generatedAt()).isEqualTo("2026-10-14T03:20:00Z");
    assertThat(response.tradeoffs()).containsExactly("稳定优先会牺牲少量体积利用率。");
    assertThat(response.risks()).containsExactly("现场仍需确认外包装抗压能力。");
    assertThat(response.nextChecks()).containsExactly("核对托盘额定载荷标识。");
    mockServer.verify();
  }

  @Test
  void shouldKeepPlanInsightDisabledAs503() {
    mockServer
        .expect(
            once(),
            requestTo(
                "http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/plan-insights"))
        .andRespond(withStatus(HttpStatus.SERVICE_UNAVAILABLE));

    assertStatus(
        503,
        () ->
            service.generatePlanInsight(
                "RUN-0001", new GenerateLoadPlanningPlanInsightRequest(null)));
    mockServer.verify();
  }

  @Test
  void shouldResumeWithoutLosingStringNumberOrBooleanAnswerTypes() throws Exception {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/resume"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(
            content()
                .json(
                    """
                        {
                          "answers": [
                            {"key": "note", "value": "keep upright"},
                            {"key": "max_height_mm", "value": 1500.5},
                            {"key": "allow_rotation", "value": true},
                            {"key": "stackable", "value": false}
                          ]
                        }
                        """,
                    true))
        .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

    var request =
        new ResumeLoadPlanningAgentRunRequest(
            List.of(
                new LoadPlanningAgentAnswerRequest(
                    "note", objectMapper.readTree("\"keep upright\"")),
                new LoadPlanningAgentAnswerRequest(
                    "max_height_mm", objectMapper.readTree("1500.5")),
                new LoadPlanningAgentAnswerRequest("allow_rotation", objectMapper.readTree("true")),
                new LoadPlanningAgentAnswerRequest("stackable", objectMapper.readTree("false"))));

    var response = service.resumeRun("RUN-0001", request);

    assertThat(response.status()).isEqualTo("WAITING_DECISION");
    mockServer.verify();
  }

  @Test
  void shouldSendDecisionUsingPythonPlanCodeName() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/decision"))
        .andExpect(method(HttpMethod.POST))
        .andExpect(
            content()
                .json(
                    """
                        {
                          "decision": "APPROVE",
                          "plan_code": "PLAN-STABLE-001",
                          "comment": null
                        }
                        """))
        .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

    service.decideRun(
        "RUN-0001",
        new DecideLoadPlanningAgentRunRequest(
            LoadPlanningAgentDecision.APPROVE, " PLAN-STABLE-001 ", " "));

    mockServer.verify();
  }

  @Test
  void shouldGetRunAndKeepPythonNotFoundAs404() {
    mockServer
        .expect(
            once(), requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-MISSING"))
        .andRespond(withStatus(HttpStatus.NOT_FOUND));

    assertStatus(404, () -> service.getRun("RUN-MISSING"));
    mockServer.verify();
  }

  @Test
  void shouldKeepPythonConflictAs409() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/tasks/LP-DEMO-0001/agent-runs"))
        .andRespond(withStatus(HttpStatus.CONFLICT));

    assertStatus(
        409,
        () ->
            service.createRun(
                "LP-DEMO-0001",
                new CreateLoadPlanningAgentRunRequest(
                    null, List.of(LoadPlanningStrategyCode.STABLE))));
    mockServer.verify();
  }

  @Test
  void shouldKeepPythonUnprocessableEntityAs422() throws Exception {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/resume"))
        .andRespond(withStatus(HttpStatus.UNPROCESSABLE_ENTITY));

    var request =
        new ResumeLoadPlanningAgentRunRequest(
            List.of(new LoadPlanningAgentAnswerRequest("height", objectMapper.readTree("1200"))));
    assertStatus(422, () -> service.resumeRun("RUN-0001", request));
    mockServer.verify();
  }

  @Test
  void shouldTranslateOtherPythonFailureTo502() {
    mockServer
        .expect(
            once(),
            requestTo("http://127.0.0.1:8010/api/v1/load-planning/agent-runs/RUN-0001/decision"))
        .andRespond(withServerError());

    assertStatus(
        502,
        () ->
            service.decideRun(
                "RUN-0001",
                new DecideLoadPlanningAgentRunRequest(
                    LoadPlanningAgentDecision.REJECT, null, "重新规划")));
    mockServer.verify();
  }

  @Test
  void shouldReturn503WhenNacosHasNoLoadPlanningInstance() {
    var unavailableService =
        new LoadPlanningAgentRunService(emptyDiscoveryClient(), RestClient.builder());

    assertStatus(503, () -> unavailableService.getRun("RUN-0001"));
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
        return "智能装托代理测试服务发现客户端";
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
        return "空智能装托代理测试服务发现客户端";
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

  private static String runJson() {
    return """
                {
                  "run_id": "RUN-0001",
                  "task_number": "LP-DEMO-0001",
                  "status": "WAITING_DECISION",
                  "current_step": "WAITING_DECISION",
                  "selected_pallet_code": "PALLET-1200X800",
                  "selected_plan_code": null,
                  "questions": [{
                    "key": "max_layers",
                    "label": "最大层数",
                    "value_type": "NUMBER",
                    "unit": "层",
                    "reason": "历史数据没有给出安全层数",
                    "required": true,
                    "min_value": 1,
                    "max_value": 10
                  }],
                  "events": [{
                    "sequence": 3,
                    "event_type": "PLAN_GENERATED",
                    "title": "候选方案已生成",
                    "detail": "稳定优先方案可行",
                    "created_at": "2026-10-09T10:30:00"
                  }],
                  "plans": [{
                    "plan_id": "01K7PLAN001",
                    "plan_code": "PLAN-STABLE-001",
                    "strategy_code": "STABLE",
                    "strategy_name": "稳定优先",
                    "feasible": true,
                    "score": 91.5,
                    "summary": "使用两块托盘，全部装下",
                    "metrics": {
                      "total_pallets": 2,
                      "total_boxes": 12,
                      "unplaced_boxes": 0,
                      "layer_area_utilization": 0.82,
                      "volume_utilization": 0.82,
                      "weight_utilization": 0.61,
                      "average_loaded_height_mm": 1044,
                      "max_loaded_height_mm": 1144,
                      "total_gross_weight_kg": 100
                    },
                    "warnings": ["重箱置底"],
                    "pallets": [{
                      "pallet_index": 1,
                      "pallet_code": "PALLET-1200X800",
                      "loaded_height_mm": 1144,
                      "payload_weight_kg": 45,
                      "gross_weight_kg": 50,
                      "box_count": 2,
                      "layer_count": 2,
                      "capacity_box_count": 16,
                      "layer_area_utilization": 0.82,
                      "payload_weight_utilization": 0.09,
                      "height_utilization": 0.9152,
                      "balance_offset": 0.04,
                      "pattern_type": "GRID",
                      "boxes_per_full_layer": 8,
                      "placements": [{
                        "placement_id": "PALLET-001-BOX-00001",
                        "item_code": "ITEM-001",
                        "box_index": 1,
                        "layer": 1,
                        "x_mm": 0,
                        "y_mm": 0,
                        "z_mm": 144,
                        "length_mm": 300,
                        "width_mm": 200,
                        "height_mm": 150,
                        "rotation_deg": 0,
                        "gross_weight_kg": 22.5,
                        "orientation": "DEFAULT"
                      }, {
                        "placement_id": "PALLET-001-BOX-00002",
                        "item_code": "ITEM-001",
                        "box_index": 2,
                        "layer": 1,
                        "x_mm": 300,
                        "y_mm": 0,
                        "z_mm": 144,
                        "length_mm": 200,
                        "width_mm": 300,
                        "height_mm": 150,
                        "rotation_deg": 90,
                        "gross_weight_kg": 22.5,
                        "orientation": "ROTATED_90"
                      }]
                    }]
                  }]
                }
                """;
  }

  private static String planInsightJson() {
    return """
        {
          "run_id": "RUN-0001",
          "plan_code": "PLAN-STABLE-001",
          "provider": "OPENAI",
          "model": "gpt-5-mini",
          "degraded": false,
          "generated_at": "2026-10-14T03:20:00Z",
          "summary": "该方案优先控制堆高与重心，全部货箱均已放置。",
          "tradeoffs": ["稳定优先会牺牲少量体积利用率。"],
          "risks": ["现场仍需确认外包装抗压能力。"],
          "next_checks": ["核对托盘额定载荷标识。"],
          "disclaimer": "本解读仅辅助人工复核，不替代现场安全确认。"
        }
        """;
  }
}
