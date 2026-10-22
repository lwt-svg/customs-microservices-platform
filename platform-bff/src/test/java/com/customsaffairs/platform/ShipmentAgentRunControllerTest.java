package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.shipmentcaseagent.ReconcileShipmentAgentRunRequest;
import com.customsaffairs.platform.shipmentcaseagent.ResumeShipmentAgentRunRequest;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentAction;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentChildResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentEventResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentInterruptType;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentPendingInterruptResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentRpaJobResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentRunController;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentRunResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentRunService;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataFactType;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataReadinessIssueCountsResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataReadinessIssueResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataReadinessResponse;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataSubjectType;
import com.customsaffairs.platform.shipmentcaseagent.StartShipmentAgentRunRequest;
import com.fasterxml.jackson.databind.node.IntNode;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.client.RestClient;

/** Shipment Agent Controller 的平台路径、camelCase JSON、校验和命令透传测试。 */
@WebMvcTest(ShipmentAgentRunController.class)
@Import(ShipmentAgentRunControllerTest.FakeShipmentAgentConfiguration.class)
class ShipmentAgentRunControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private CapturedCommands capturedCommands;

    @BeforeEach
    void resetCapturedCommands() {
        capturedCommands.clear();
    }

    @Test
    void shouldStartRunWith201AndExposeDeepCamelCaseResponse() throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs")
                        .header("Idempotency-Key", "browser-start-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedCaseRevision": 3
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.runId").value("SHIP-RUN-0001"))
                .andExpect(jsonPath("$.data.caseNumber")
                        .value("SHP-DEMO-FCL-001"))
                .andExpect(jsonPath("$.data.caseName")
                        .value("上海至洛杉矶演示发运"))
                .andExpect(jsonPath("$.data.currentStage")
                        .value("AWAITING_BOOKING_SUBMIT_APPROVAL"))
                .andExpect(jsonPath("$.data.runVersion").value(4))
                .andExpect(jsonPath("$.data.startedCaseRevision").value(3))
                .andExpect(jsonPath("$.data.lastObservedCaseRevision").value(4))
                .andExpect(jsonPath("$.data.lockedLoadVersionNumber")
                        .value(2))
                .andExpect(jsonPath("$.data.dataReadiness.status")
                        .value("ACTION_REQUIRED"))
                .andExpect(jsonPath("$.data.dataReadiness.assessedAt")
                        .value("2026-10-16T02:00:00Z"))
                .andExpect(jsonPath("$.data.dataReadiness.issueCount")
                        .value(1))
                .andExpect(jsonPath(
                        "$.data.dataReadiness.blockingIssueCount")
                        .value(1))
                .andExpect(jsonPath(
                        "$.data.dataReadiness.issueCounts.total")
                        .value(1))
                .andExpect(jsonPath(
                        "$.data.dataReadiness.issues[0].issueId")
                        .value(
                                "INVENTORY_SHORTAGE:CARGO_ITEM:"
                                        + "AGENT-E2E-ITEM-A"))
                .andExpect(jsonPath(
                        "$.data.dataReadiness.issues[0].actualValue")
                        .value(8))
                .andExpect(jsonPath(
                        "$.data.dataReadiness.issues[0].ownerRole")
                        .value("WAREHOUSE_OPERATOR"))
                .andExpect(jsonPath("$.data.pendingInterrupt.type")
                        .value("BOOKING_SUBMIT_APPROVAL"))
                .andExpect(jsonPath(
                        "$.data.pendingInterrupt.allowedActions[0]")
                        .value("APPROVE"))
                .andExpect(jsonPath(
                        "$.data.pendingInterrupt.details.riskLevel")
                        .value("MEDIUM"))
                .andExpect(jsonPath("$.data.allowedActions[1]")
                        .value("RECHECK"))
                .andExpect(jsonPath("$.data.childAgent.nextAction")
                        .value("OPEN_LOAD_WORKBENCH"))
                .andExpect(jsonPath("$.data.rpaJobs[0].jobId")
                        .value("RPA-JOB-0001"))
                .andExpect(jsonPath("$.data.rpaJobs[0].operation")
                        .value("LINK_BOOKING_STAGE"))
                .andExpect(jsonPath("$.data.rpaJobs[0].environment")
                        .value("SANDBOX"))
                .andExpect(jsonPath("$.data.rpaJobs[0].sideEffectPhase")
                        .value("EXTERNAL_EFFECT_CONFIRMED"))
                .andExpect(jsonPath("$.data.rpaJobs[0].businessOutcome")
                        .value("LINK_DRAFT_SAVED"))
                .andExpect(jsonPath("$.data.events[0].eventType")
                        .value("HUMAN_APPROVAL_REQUIRED"))
                .andExpect(jsonPath("$.data.events[0].data.tool")
                        .value("LINK_BOOKING"))
                .andExpect(jsonPath("$.data.events[0].actorDisplayName")
                        .value("演示操作员"))
                .andExpect(jsonPath("$.data.events[0].occurredAt")
                        .value("2026-10-16T02:10:00Z"));

        assertThat(capturedCommands.startCaseNumber)
                .isEqualTo("SHP-DEMO-FCL-001");
        assertThat(capturedCommands.startRequest.expectedCaseRevision())
                .isEqualTo(3);
        assertThat(capturedCommands.startIdempotencyKey)
                .isEqualTo("browser-start-001");
    }

    @Test
    void shouldExposeActiveHistoryGlobalListAndDetailRoutes()
            throws Exception {
        mockMvc.perform(get(
                        "/api/v1/platform/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs/active"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.runId").value("SHIP-RUN-0001"));

        mockMvc.perform(get(
                        "/api/v1/platform/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs/history"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].runId")
                        .value("SHIP-RUN-0001"));

        mockMvc.perform(get("/api/v1/platform/shipment-agent-runs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].caseNumber")
                        .value("SHP-DEMO-FCL-001"));

        mockMvc.perform(get(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.bookingReference")
                        .value("BK-2026-0001"));
    }

    @Test
    void shouldForwardResumeAndReconcileCommandsWithIdempotencyKeys()
            throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume")
                        .header("Idempotency-Key", "browser-resume-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedRunVersion": 4,
                                  "interruptType": "BOOKING_SUBMIT_APPROVAL",
                                  "action": "APPROVE",
                                  "comment": "确认提交",
                                  "selectedResourceNumber": "LINK-DEMO-0001",
                                  "requiredCapabilities": [
                                    "LOAD_PLANNING",
                                    "LINK_BOOKING"
                                  ]
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.runId")
                        .value("SHIP-RUN-0001"));

        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/reconcile")
                        .header("Idempotency-Key", "browser-reconcile-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedRunVersion": 5
                                }
                                """))
                .andExpect(status().isOk());

        assertThat(capturedCommands.resumeRunId)
                .isEqualTo("SHIP-RUN-0001");
        assertThat(capturedCommands.resumeRequest.interruptType())
                .isEqualTo(
                        ShipmentAgentInterruptType.BOOKING_SUBMIT_APPROVAL);
        assertThat(capturedCommands.resumeRequest.action())
                .isEqualTo(ShipmentAgentAction.APPROVE);
        assertThat(capturedCommands.resumeIdempotencyKey)
                .isEqualTo("browser-resume-001");
        assertThat(capturedCommands.reconcileRequest.expectedRunVersion())
                .isEqualTo(5);
        assertThat(capturedCommands.reconcileIdempotencyKey)
                .isEqualTo("browser-reconcile-001");
    }

    @Test
    void shouldAcceptDataReadinessFactsAndKeepNumberAsNumber()
            throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume")
                        .header("Idempotency-Key", "browser-supply-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedRunVersion": 8,
                                  "interruptType":
                                    "DATA_READINESS_REMEDIATION",
                                  "action": "SUPPLY_DATA",
                                  "comment": "补充仓库库存",
                                  "dataFacts": [{
                                    "subjectType": "CARGO_ITEM",
                                    "subjectCode": "AGENT-E2E-ITEM-A",
                                    "factType": "AVAILABLE_QUANTITY",
                                    "value": 8,
                                    "sourceType": "SYNTHETIC_DEMO",
                                    "sourceReference":
                                      "shipment-agent-e2e-first",
                                    "sourceLocator":
                                      "generated:inventory-A",
                                    "observedAt":
                                      "2026-10-16T02:00:00Z",
                                    "expiresAt": null
                                  }]
                                }
                                """))
                .andExpect(status().isOk());

        assertThat(capturedCommands.resumeRequest.interruptType())
                .isEqualTo(
                        ShipmentAgentInterruptType
                                .DATA_READINESS_REMEDIATION);
        assertThat(capturedCommands.resumeRequest.action())
                .isEqualTo(ShipmentAgentAction.SUPPLY_DATA);
        assertThat(capturedCommands.resumeRequest.dataFacts())
                .hasSize(1);
        assertThat(capturedCommands.resumeRequest.dataFacts()
                .getFirst()
                .subjectType())
                .isEqualTo(ShipmentDataSubjectType.CARGO_ITEM);
        assertThat(capturedCommands.resumeRequest.dataFacts()
                .getFirst()
                .factType())
                .isEqualTo(ShipmentDataFactType.AVAILABLE_QUANTITY);
        assertThat(capturedCommands.resumeRequest.dataFacts()
                .getFirst()
                .value()
                .isIntegralNumber()).isTrue();
        assertThat(capturedCommands.resumeRequest.dataFacts()
                .getFirst()
                .value()
                .intValue()).isEqualTo(8);
    }

    @Test
    void shouldRejectMissingHeaderInvalidVersionAndUnknownInterruptType()
            throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedCaseRevision\":3}"))
                .andExpect(status().isBadRequest());

        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/reconcile")
                        .header("Idempotency-Key", "invalid-version")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"expectedRunVersion\":0}"))
                .andExpect(status().isBadRequest());

        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume")
                        .header("Idempotency-Key", "invalid-type")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedRunVersion": 4,
                                  "interruptType": "FREE_FORM_CHAT",
                                  "action": "APPROVE"
                                }
                                """))
                .andExpect(status().isBadRequest());

        mockMvc.perform(post(
                        "/api/v1/platform/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume")
                        .header("Idempotency-Key", "invalid-data-fact")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "expectedRunVersion": 4,
                                  "interruptType":
                                    "DATA_READINESS_REMEDIATION",
                                  "action": "SUPPLY_DATA",
                                  "dataFacts": [{
                                    "subjectType": "CARGO_ITEM",
                                    "factType": "AVAILABLE_QUANTITY",
                                    "value": 8,
                                    "sourceType": "SYNTHETIC_DEMO"
                                  }]
                                }
                                """))
                .andExpect(status().isBadRequest());

        assertThat(capturedCommands.reconcileRequest).isNull();
        assertThat(capturedCommands.resumeRequest).isNull();
    }

    @TestConfiguration
    static class FakeShipmentAgentConfiguration {

        @Bean
        CapturedCommands capturedCommands() {
            return new CapturedCommands();
        }

        @Bean
        ShipmentAgentRunService shipmentAgentRunService(
                CapturedCommands captured) {
            return new ShipmentAgentRunService(
                    null, RestClient.builder()) {
                @Override
                public ShipmentAgentRunResponse startRun(
                        String caseNumber,
                        StartShipmentAgentRunRequest request,
                        String idempotencyKey) {
                    captured.startCaseNumber = caseNumber;
                    captured.startRequest = request;
                    captured.startIdempotencyKey = idempotencyKey;
                    return runResponse();
                }

                @Override
                public ShipmentAgentRunResponse getActiveRun(
                        String caseNumber) {
                    return runResponse();
                }

                @Override
                public List<ShipmentAgentRunResponse> listRunHistory(
                        String caseNumber) {
                    return List.of(runResponse());
                }

                @Override
                public List<ShipmentAgentRunResponse> listRuns() {
                    return List.of(runResponse());
                }

                @Override
                public ShipmentAgentRunResponse getRun(String runId) {
                    return runResponse();
                }

                @Override
                public ShipmentAgentRunResponse resumeRun(
                        String runId,
                        ResumeShipmentAgentRunRequest request,
                        String idempotencyKey) {
                    captured.resumeRunId = runId;
                    captured.resumeRequest = request;
                    captured.resumeIdempotencyKey = idempotencyKey;
                    return runResponse();
                }

                @Override
                public ShipmentAgentRunResponse reconcileRun(
                        String runId,
                        ReconcileShipmentAgentRunRequest request,
                        String idempotencyKey) {
                    captured.reconcileRunId = runId;
                    captured.reconcileRequest = request;
                    captured.reconcileIdempotencyKey = idempotencyKey;
                    return runResponse();
                }
            };
        }

        private static ShipmentAgentRunResponse runResponse() {
            return new ShipmentAgentRunResponse(
                    "SHIP-RUN-0001",
                    "SHP-DEMO-FCL-001",
                    "上海至洛杉矶演示发运",
                    "SEA_FCL",
                    "ACTIVE",
                    "shipment-coordinator-v1",
                    "shipment-policy-v1",
                    "WAITING_HUMAN",
                    "AWAITING_BOOKING_SUBMIT_APPROVAL",
                    4,
                    3,
                    4,
                    List.of("LOAD_PLANNING", "LINK_BOOKING"),
                    true,
                    "LP-DEMO-0001",
                    "LOAD-RUN-0001",
                    "LOAD-VERSION-0002",
                    2,
                    "LINK-DEMO-0001",
                    "DRAFT",
                    "NOT_SUBMITTED",
                    "BK-2026-0001",
                    false,
                    new ShipmentDataReadinessResponse(
                            "ACTION_REQUIRED",
                            2,
                            "SHIPMENT_DATA_READINESS_V1",
                            "DEMO",
                            "64-char-synthetic-hash",
                            "2026-10-16T02:00:00Z",
                            null,
                            null,
                            1,
                            1,
                            new ShipmentDataReadinessIssueCountsResponse(
                                    1, 1, 0),
                            List.of(
                                    new ShipmentDataReadinessIssueResponse(
                                            "INVENTORY_SHORTAGE:CARGO_ITEM:"
                                                    + "AGENT-E2E-ITEM-A",
                                            "INVENTORY_SHORTAGE",
                                            "BLOCKING",
                                            ShipmentDataSubjectType.CARGO_ITEM,
                                            "AGENT-E2E-ITEM-A",
                                            ShipmentDataFactType
                                                    .AVAILABLE_QUANTITY,
                                            IntNode.valueOf(24),
                                            IntNode.valueOf(8),
                                            "CARTON",
                                            "库存不足",
                                            ShipmentAgentAction.SUPPLY_DATA,
                                            "WAREHOUSE_OPERATOR"))),
                    null,
                    null,
                    List.of(),
                    null,
                    null,
                    new ShipmentAgentPendingInterruptResponse(
                            ShipmentAgentInterruptType
                                    .BOOKING_SUBMIT_APPROVAL,
                            "确认提交 LINK 订舱",
                            "提交后将进入异步承运人处理。",
                            List.of(
                                    ShipmentAgentAction.APPROVE,
                                    ShipmentAgentAction.STOP),
                            Map.of("riskLevel", "MEDIUM")),
                    List.of(
                            ShipmentAgentAction.STOP,
                            ShipmentAgentAction.RECHECK),
                    new ShipmentAgentChildResponse(
                            "LOAD-RUN-0001",
                            "LP-DEMO-0001",
                            "WAITING_DECISION",
                            "WAITING_DECISION",
                            2,
                            "OPEN_LOAD_WORKBENCH"),
                    List.of(new ShipmentAgentRpaJobResponse(
                            "RPA-JOB-0001",
                            1,
                            "LINK",
                            "LINK_BOOKING_STAGE",
                            "SANDBOX",
                            "SUCCEEDED",
                            "草稿已保存并完成对账",
                            "EXTERNAL_EFFECT_CONFIRMED",
                            "LINK_DRAFT_SAVED",
                            "LINK-DEMO-0001",
                            null,
                            "2026-10-16T02:09:00Z")),
                    "2026-10-16T02:00:00Z",
                    "2026-10-16T02:10:00Z",
                    null,
                    List.of(new ShipmentAgentEventResponse(
                            71L,
                            7,
                            "HUMAN_APPROVAL_REQUIRED",
                            "AWAITING_BOOKING_SUBMIT_APPROVAL",
                            "等待订舱提交审批",
                            "LINK 草稿已准备完成。",
                            Map.of("tool", "LINK_BOOKING"),
                            "LINK_BOOKING_APPLICATION",
                            "LINK-DEMO-0001",
                            "demo-operator",
                            "演示操作员",
                            "LOCAL_DEMO",
                            "11111111-1111-1111-1111-111111111111",
                            "2026-10-16T02:10:00Z")));
        }
    }

    static class CapturedCommands {
        private String startCaseNumber;
        private StartShipmentAgentRunRequest startRequest;
        private String startIdempotencyKey;
        private String resumeRunId;
        private ResumeShipmentAgentRunRequest resumeRequest;
        private String resumeIdempotencyKey;
        private String reconcileRunId;
        private ReconcileShipmentAgentRunRequest reconcileRequest;
        private String reconcileIdempotencyKey;

        private void clear() {
            startCaseNumber = null;
            startRequest = null;
            startIdempotencyKey = null;
            resumeRunId = null;
            resumeRequest = null;
            resumeIdempotencyKey = null;
            reconcileRunId = null;
            reconcileRequest = null;
            reconcileIdempotencyKey = null;
        }
    }
}
