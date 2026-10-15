package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.loadplanning.CreateLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.DecideLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.GenerateLoadPlanningPlanInsightRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentEventResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentMetricsResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentPalletResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentPlacementResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentPlanResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentQuestionResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentRunController;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentRunResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningAgentRunService;
import com.customsaffairs.platform.loadplanning.LoadPlanningPlanInsightResponse;
import com.customsaffairs.platform.loadplanning.ReplanLoadPlanningAgentRunRequest;
import com.customsaffairs.platform.loadplanning.ResumeLoadPlanningAgentRunRequest;
import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigDecimal;
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

/** 智能装托 Controller 的平台路径、camelCase JSON 与输入校验测试。 */
@WebMvcTest(LoadPlanningAgentRunController.class)
@Import(LoadPlanningAgentRunControllerTest.FakeAgentRunConfiguration.class)
class LoadPlanningAgentRunControllerTest {

  @Autowired private MockMvc mockMvc;

  @Autowired private CapturedRequests capturedRequests;

  @Test
  void shouldCreateRunAndExposeDeepCamelCaseResponse() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "selectedPalletCode": "PALLET-1200X800",
                                  "strategyCodes": ["STABLE", "BALANCED"]
                                }
                                """))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$.code").value(0))
        .andExpect(jsonPath("$.data.runId").value("RUN-0001"))
        .andExpect(jsonPath("$.data.currentStep").value("WAITING_DECISION"))
        .andExpect(jsonPath("$.data.selectedPalletCode").value("PALLET-1200X800"))
        .andExpect(jsonPath("$.data.questions[0].valueType").value("NUMBER"))
        .andExpect(jsonPath("$.data.events[0].eventType").value("PLAN_GENERATED"))
        .andExpect(jsonPath("$.data.plans[0].planCode").value("PLAN-STABLE-001"))
        .andExpect(jsonPath("$.data.plans[0].metrics.totalPallets").value(1))
        .andExpect(jsonPath("$.data.plans[0].pallets[0].palletIndex").value(1))
        .andExpect(jsonPath("$.data.plans[0].pallets[0].placements[0].itemCode").value("ITEM-001"))
        .andExpect(jsonPath("$.data.plans[0].pallets[0].placements[0].xMm").value(0));

    assertThat(capturedRequests.createRequest.selectedPalletCode()).isEqualTo("PALLET-1200X800");
  }

  @Test
  void shouldAcceptIdempotencyKeyOnCreateBoundary() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs")
                .header("Idempotency-Key", "browser-request-001")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "selectedPalletCode": null,
                                  "strategyCodes": ["STABLE"]
                                }
                                """))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$.data.runId").value("RUN-0001"));

    assertThat(capturedRequests.idempotencyKey).isEqualTo("browser-request-001");
  }

  @Test
  void shouldExposeRunHistoryAndAcceptReplanIdempotencyKey() throws Exception {
    mockMvc
        .perform(
            get(
                "/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs/history"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data[0].runId").value("RUN-0001"));

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/replan")
                .header("Idempotency-Key", "replan-request-001")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "reason": "现场要求降低堆高",
                      "strategyCodes": ["STABLE"]
                    }
                    """))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.runId").value("RUN-0001"));

    assertThat(capturedRequests.replanIdempotencyKey).isEqualTo("replan-request-001");
    assertThat(capturedRequests.replanRequest.reason()).isEqualTo("现场要求降低堆高");
  }

  @Test
  void shouldGetResumeAndDecideThroughPlatformPaths() throws Exception {
    mockMvc
        .perform(get("/api/v1/platform/load-planning/agent-runs/RUN-0001"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.runId").value("RUN-0001"));

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/resume")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "answers": [
                                    {"key": "note", "value": "keep upright"},
                                    {"key": "maxHeightMm", "value": 1500.5},
                                    {"key": "allowRotation", "value": true},
                                    {"key": "stackable", "value": false}
                                  ]
                                }
                                """))
        .andExpect(status().isOk());

    List<JsonNode> values =
        capturedRequests.resumeRequest.answers().stream().map(answer -> answer.value()).toList();
    assertThat(values.get(0).isTextual()).isTrue();
    assertThat(values.get(1).isNumber()).isTrue();
    assertThat(values.get(2).isBoolean()).isTrue();
    assertThat(values.get(3).isBoolean()).isTrue();
    assertThat(values.get(3).booleanValue()).isFalse();

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/decision")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "decision": "APPROVE",
                                  "planCode": "PLAN-STABLE-001",
                                  "comment": "同意执行"
                                }
                                """))
        .andExpect(status().isOk());

    assertThat(capturedRequests.decisionRequest.planCode()).isEqualTo("PLAN-STABLE-001");
  }

  @Test
  void shouldExposePlanInsightAsCamelCaseWithoutChangingRun() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/plan-insights")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"planCode\":\"PLAN-STABLE-001\"}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.code").value(0))
        .andExpect(jsonPath("$.data.runId").value("RUN-0001"))
        .andExpect(jsonPath("$.data.planCode").value("PLAN-STABLE-001"))
        .andExpect(jsonPath("$.data.provider").value("RULE_BASED"))
        .andExpect(jsonPath("$.data.degraded").value(true))
        .andExpect(jsonPath("$.data.generatedAt").value("2026-10-14T03:20:00Z"))
        .andExpect(jsonPath("$.data.tradeoffs[0]").value("稳定性优先"))
        .andExpect(jsonPath("$.data.nextChecks[0]").value("现场复核"));

    assertThat(capturedRequests.planInsightRequest.planCode()).isEqualTo("PLAN-STABLE-001");
  }

  @Test
  void shouldRejectPlanInsightCodeLongerThanPythonContract() throws Exception {
    String oversizedPlanCode = "P".repeat(65);

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/plan-insights")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"planCode\":\"" + oversizedPlanCode + "\"}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void shouldRejectUnknownStrategyAndInvalidAnswerValue() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "strategyCodes": ["FASTEST"]
                                }
                                """))
        .andExpect(status().isBadRequest());

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/resume")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "answers": [{"key": "limits", "value": {"height": 1500}}]
                                }
                                """))
        .andExpect(status().isBadRequest());
  }

  @Test
  void shouldValidateDecisionSpecificRequiredFields() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/decision")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "decision": "APPROVE",
                      "planCode": null
                    }
                    """))
        .andExpect(status().isBadRequest());

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/decision")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "decision": "REJECT",
                      "comment": " "
                    }
                    """))
        .andExpect(status().isBadRequest());
  }

  @Test
  void shouldRejectEmptyOrDuplicateInputCollections() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "strategyCodes": ["STABLE", "STABLE"]
                                }
                                """))
        .andExpect(status().isBadRequest());

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/tasks/LP-DEMO-0001/agent-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {
                      "strategyCodes": [null]
                    }
                    """))
        .andExpect(status().isBadRequest());

    mockMvc
        .perform(
            post("/api/v1/platform/load-planning/agent-runs/RUN-0001/resume")
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                                {
                                  "answers": []
                                }
                                """))
        .andExpect(status().isBadRequest());
  }

  @TestConfiguration
  static class FakeAgentRunConfiguration {

    @Bean
    CapturedRequests capturedRequests() {
      return new CapturedRequests();
    }

    @Bean
    LoadPlanningAgentRunService agentRunService(CapturedRequests captured) {
      return new LoadPlanningAgentRunService(null, RestClient.builder()) {
        @Override
        public LoadPlanningAgentRunResponse createRun(
            String taskNumber, CreateLoadPlanningAgentRunRequest request) {
          captured.createRequest = request;
          return run();
        }

        @Override
        public LoadPlanningAgentRunResponse createRun(
            String taskNumber,
            CreateLoadPlanningAgentRunRequest request,
            String idempotencyKey) {
          captured.createRequest = request;
          captured.idempotencyKey = idempotencyKey;
          return run();
        }

        @Override
        public LoadPlanningAgentRunResponse getRun(String runId) {
          return run();
        }

        @Override
        public List<LoadPlanningAgentRunResponse> listRunHistory(String taskNumber) {
          return List.of(run());
        }

        @Override
        public LoadPlanningAgentRunResponse resumeRun(
            String runId, ResumeLoadPlanningAgentRunRequest request) {
          captured.resumeRequest = request;
          return run();
        }

        @Override
        public LoadPlanningAgentRunResponse decideRun(
            String runId, DecideLoadPlanningAgentRunRequest request) {
          captured.decisionRequest = request;
          return run();
        }

        @Override
        public LoadPlanningAgentRunResponse replanRun(
            String runId,
            ReplanLoadPlanningAgentRunRequest request,
            String idempotencyKey) {
          captured.replanRequest = request;
          captured.replanIdempotencyKey = idempotencyKey;
          return run();
        }

        @Override
        public LoadPlanningPlanInsightResponse generatePlanInsight(
            String runId, GenerateLoadPlanningPlanInsightRequest request) {
          captured.planInsightRequest = request;
          return new LoadPlanningPlanInsightResponse(
              runId,
              request.planCode(),
              "RULE_BASED",
              "rules-v1",
              true,
              "2026-10-14T03:20:00Z",
              "该方案优先满足稳定性约束。",
              List.of("稳定性优先"),
              List.of("需确认包装抗压"),
              List.of("现场复核"),
              "辅助解读不替代人工审批。");
        }
      };
    }
  }

  static class CapturedRequests {
    private CreateLoadPlanningAgentRunRequest createRequest;
    private String idempotencyKey;
    private ResumeLoadPlanningAgentRunRequest resumeRequest;
    private DecideLoadPlanningAgentRunRequest decisionRequest;
    private ReplanLoadPlanningAgentRunRequest replanRequest;
    private String replanIdempotencyKey;
    private GenerateLoadPlanningPlanInsightRequest planInsightRequest;
  }

  private static LoadPlanningAgentRunResponse run() {
    return new LoadPlanningAgentRunResponse(
        "RUN-0001",
        "LP-DEMO-0001",
        "WAITING_DECISION",
        "WAITING_DECISION",
        "PALLET-1200X800",
        null,
        List.of(
            new LoadPlanningAgentQuestionResponse(
                "maxLayers",
                "最大层数",
                "NUMBER",
                "层",
                "需要人工确认",
                true,
                BigDecimal.ONE,
                BigDecimal.TEN)),
        List.of(
            new LoadPlanningAgentEventResponse(
                1L, "PLAN_GENERATED", "方案生成", "候选方案已经生成", "2026-10-09T10:30:00")),
        List.of(
            new LoadPlanningAgentPlanResponse(
                "01K7PLAN001",
                "PLAN-STABLE-001",
                "STABLE",
                "稳定优先",
                true,
                new BigDecimal("91.5"),
                "全部装下",
                new LoadPlanningAgentMetricsResponse(
                    1,
                    1,
                    0,
                    new BigDecimal("0.82"),
                    new BigDecimal("0.61"),
                    new BigDecimal("294"),
                    new BigDecimal("294"),
                    new BigDecimal("27.5")),
                List.of(),
                List.of(
                    new LoadPlanningAgentPalletResponse(
                        1,
                        "PALLET-1200X800",
                        new BigDecimal("294"),
                        new BigDecimal("27.5"),
                        1,
                        List.of(
                            new LoadPlanningAgentPlacementResponse(
                                "ITEM-001",
                                1,
                                BigDecimal.ZERO,
                                BigDecimal.ZERO,
                                new BigDecimal("144"),
                                new BigDecimal("300"),
                                new BigDecimal("200"),
                                new BigDecimal("150"),
                                "DEFAULT")))))));
  }
}
