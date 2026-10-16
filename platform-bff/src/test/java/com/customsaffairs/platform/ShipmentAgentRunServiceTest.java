package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.shipmentcaseagent.ReconcileShipmentAgentRunRequest;
import com.customsaffairs.platform.shipmentcaseagent.ResumeShipmentAgentRunRequest;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentAction;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentInterruptType;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentAgentRunService;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataFactRequest;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataFactType;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataSourceType;
import com.customsaffairs.platform.shipmentcaseagent.ShipmentDataSubjectType;
import com.customsaffairs.platform.shipmentcaseagent.StartShipmentAgentRunRequest;
import com.fasterxml.jackson.databind.node.BooleanNode;
import com.fasterxml.jackson.databind.node.IntNode;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.cloud.client.DefaultServiceInstance;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/** Shipment Agent BFF 的服务发现、幂等头、字段适配和安全错误翻译测试。 */
class ShipmentAgentRunServiceTest {

    private MockRestServiceServer mockServer;
    private ShipmentAgentRunService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new ShipmentAgentRunService(discoveryClient(), builder);
    }

    @Test
    void shouldStartRunWithRevisionAndForwardIdempotencyKeyUnchanged() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-start-001"))
                .andExpect(content().json("""
                        {
                          "expected_case_revision": 3
                        }
                        """))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        var response = service.startRun(
                " SHP-DEMO-FCL-001 ",
                new StartShipmentAgentRunRequest(3),
                "browser-start-001");

        assertThat(response.runId()).isEqualTo("SHIP-RUN-0001");
        assertThat(response.caseNumber()).isEqualTo("SHP-DEMO-FCL-001");
        assertThat(response.caseName()).isEqualTo("上海至洛杉矶演示发运");
        assertThat(response.transportScenario()).isEqualTo("SEA_FCL");
        assertThat(response.caseStatus()).isEqualTo("ACTIVE");
        assertThat(response.currentStage())
                .isEqualTo("AWAITING_BOOKING_SUBMIT_APPROVAL");
        assertThat(response.runVersion()).isEqualTo(4);
        assertThat(response.startedCaseRevision()).isEqualTo(3);
        assertThat(response.lastObservedCaseRevision()).isEqualTo(4);
        assertThat(response.requiredCapabilities())
                .containsExactly("LOAD_PLANNING", "LINK_BOOKING");
        assertThat(response.scenarioPolicyConfirmed()).isTrue();
        assertThat(response.lockedLoadVersionNumber()).isEqualTo(2);
        assertThat(response.bookingSubmissionUncertain()).isFalse();
        assertThat(response.dataReadiness().status())
                .isEqualTo("ACTION_REQUIRED");
        assertThat(response.dataReadiness().revision()).isEqualTo(2);
        assertThat(response.dataReadiness().assessedAt())
                .isEqualTo("2026-10-16T02:00:00Z");
        assertThat(response.dataReadiness().issueCount()).isEqualTo(1);
        assertThat(response.dataReadiness().blockingIssueCount()).isEqualTo(1);
        assertThat(response.dataReadiness().issueCounts().total()).isEqualTo(1);
        assertThat(response.dataReadiness().issueCounts().warning()).isZero();
        assertThat(response.dataReadiness().issues().getFirst().actualValue()
                .intValue()).isEqualTo(8);
        assertThat(response.dataReadiness().issues().getFirst().ownerRole())
                .isEqualTo("WAREHOUSE_OPERATOR");
        assertThat(response.pendingInterrupt().type())
                .isEqualTo(ShipmentAgentInterruptType.BOOKING_SUBMIT_APPROVAL);
        assertThat(response.pendingInterrupt().allowedActions())
                .containsExactly(
                        ShipmentAgentAction.APPROVE,
                        ShipmentAgentAction.STOP);
        assertThat(response.pendingInterrupt().details())
                .containsEntry("riskLevel", "MEDIUM");
        assertThat(response.allowedActions())
                .containsExactly(
                        ShipmentAgentAction.STOP,
                        ShipmentAgentAction.RECHECK);
        assertThat(response.childAgent().runId()).isEqualTo("LOAD-RUN-0001");
        assertThat(response.childAgent().nextAction())
                .isEqualTo("OPEN_LOAD_WORKBENCH");
        assertThat(response.events().getFirst().eventType())
                .isEqualTo("HUMAN_APPROVAL_REQUIRED");
        assertThat(response.events().getFirst().data())
                .containsEntry("tool", "LINK_BOOKING");
        assertThat(response.events().getFirst().actorDisplayName())
                .isEqualTo("演示操作员");
        assertThat(response.events().getFirst().resourceNumber())
                .isEqualTo("LINK-DEMO-0001");
        mockServer.verify();
    }

    @Test
    void shouldProxyActiveHistoryGlobalListAndDetailPaths() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs/active"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-001/agent-runs/history"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "[" + runJson() + "]", MediaType.APPLICATION_JSON));

        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "[" + runJson() + "]", MediaType.APPLICATION_JSON));

        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-0001"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        assertThat(service.getActiveRun("SHP-DEMO-FCL-001").runId())
                .isEqualTo("SHIP-RUN-0001");
        assertThat(service.listRunHistory("SHP-DEMO-FCL-001"))
                .extracting(run -> run.runId())
                .containsExactly("SHIP-RUN-0001");
        assertThat(service.listRuns())
                .extracting(run -> run.caseNumber())
                .containsExactly("SHP-DEMO-FCL-001");
        assertThat(service.getRun(" SHIP-RUN-0001 ").bookingReference())
                .isEqualTo("BK-2026-0001");
        mockServer.verify();
    }

    @Test
    void shouldKeepNoActiveRunAsSuccessfulNullInsteadOfGatewayFailure() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-EMPTY/agent-runs/active"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("null", MediaType.APPLICATION_JSON));

        assertThat(service.getActiveRun("SHP-DEMO-FCL-EMPTY")).isNull();
        mockServer.verify();
    }

    @Test
    void shouldResumeWithStructuredDecisionAndReconcileWithRunVersion() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-resume-001"))
                .andExpect(content().json("""
                        {
                          "expected_run_version": 4,
                          "interrupt_type": "BOOKING_SUBMIT_APPROVAL",
                          "action": "APPROVE",
                          "comment": "确认提交",
                          "selected_resource_number": "LINK-DEMO-0001",
                          "required_capabilities": [
                            "LOAD_PLANNING",
                            "LINK_BOOKING"
                          ]
                        }
                        """))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-0001/reconcile"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-reconcile-001"))
                .andExpect(content().json("""
                        {
                          "expected_run_version": 5
                        }
                        """, true))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        service.resumeRun(
                "SHIP-RUN-0001",
                new ResumeShipmentAgentRunRequest(
                        4,
                        ShipmentAgentInterruptType.BOOKING_SUBMIT_APPROVAL,
                        ShipmentAgentAction.APPROVE,
                        " 确认提交 ",
                        " LINK-DEMO-0001 ",
                        List.of(" LOAD_PLANNING ", "LINK_BOOKING"),
                        null),
                "browser-resume-001");

        service.reconcileRun(
                "SHIP-RUN-0001",
                new ReconcileShipmentAgentRunRequest(5),
                "browser-reconcile-001");

        mockServer.verify();
    }

    @Test
    void shouldResumeDataReadinessWithSnakeCaseFactsAndJsonScalarValues() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-0001/resume"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-supply-001"))
                .andExpect(content().json("""
                        {
                          "expected_run_version": 8,
                          "interrupt_type": "DATA_READINESS_REMEDIATION",
                          "action": "SUPPLY_DATA",
                          "comment": "补充仓库事实",
                          "selected_resource_number": null,
                          "required_capabilities": null,
                          "data_facts": [
                            {
                              "subject_type": "CARGO_ITEM",
                              "subject_code": "AGENT-E2E-ITEM-A",
                              "fact_type": "AVAILABLE_QUANTITY",
                              "value": 8,
                              "source_type": "SYNTHETIC_DEMO",
                              "source_reference": "shipment-agent-e2e-first",
                              "source_locator": "generated:inventory-A",
                              "observed_at": "2026-10-16T02:00:00Z",
                              "expires_at": null
                            },
                            {
                              "subject_type": "PALLET_SPEC",
                              "subject_code": "AGENT-E2E-PALLET",
                              "fact_type": "MASTER_DATA_CONFIRMED",
                              "value": true,
                              "source_type": "MANUAL_CONFIRMATION",
                              "source_reference": null,
                              "source_locator": null,
                              "observed_at": null,
                              "expires_at": null
                            }
                          ]
                        }
                        """))
                .andRespond(withSuccess(runJson(), MediaType.APPLICATION_JSON));

        service.resumeRun(
                "SHIP-RUN-0001",
                new ResumeShipmentAgentRunRequest(
                        8,
                        ShipmentAgentInterruptType.DATA_READINESS_REMEDIATION,
                        ShipmentAgentAction.SUPPLY_DATA,
                        " 补充仓库事实 ",
                        null,
                        null,
                        List.of(
                                new ShipmentDataFactRequest(
                                        ShipmentDataSubjectType.CARGO_ITEM,
                                        " AGENT-E2E-ITEM-A ",
                                        ShipmentDataFactType.AVAILABLE_QUANTITY,
                                        IntNode.valueOf(8),
                                        ShipmentDataSourceType.SYNTHETIC_DEMO,
                                        " shipment-agent-e2e-first ",
                                        " generated:inventory-A ",
                                        " 2026-10-16T02:00:00Z ",
                                        null),
                                new ShipmentDataFactRequest(
                                        ShipmentDataSubjectType.PALLET_SPEC,
                                        "AGENT-E2E-PALLET",
                                        ShipmentDataFactType
                                                .MASTER_DATA_CONFIRMED,
                                        BooleanNode.TRUE,
                                        ShipmentDataSourceType
                                                .MANUAL_CONFIRMATION,
                                        null,
                                        null,
                                        null,
                                        null))),
                "browser-supply-001");

        mockServer.verify();
    }

    @ParameterizedTest
    @CsvSource({
            "404,404",
            "409,409",
            "400,400",
            "422,400",
            "503,503",
            "500,502"
    })
    void shouldTranslateRemoteErrorsWithoutLeakingPythonBody(
            int remoteStatus, int expectedPlatformStatus) {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-ERROR"))
                .andRespond(withStatus(HttpStatus.valueOf(remoteStatus))
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"detail\":\"PYTHON_SECRET_DATABASE_DETAIL\"}"));

        assertThatThrownBy(() -> service.getRun("SHIP-RUN-ERROR"))
                .isInstanceOfSatisfying(
                        ResponseStatusException.class,
                        exception -> {
                            assertThat(exception.getStatusCode().value())
                                    .isEqualTo(expectedPlatformStatus);
                            assertThat(exception.getReason())
                                    .doesNotContain(
                                            "PYTHON_SECRET_DATABASE_DETAIL");
                        });
        mockServer.verify();
    }

    @Test
    void shouldRejectBlankIdempotencyKeyBeforeCallingPython() {
        assertThatThrownBy(() -> service.startRun(
                        "SHP-DEMO-FCL-001",
                        new StartShipmentAgentRunRequest(3),
                        "   "))
                .isInstanceOfSatisfying(
                        ResponseStatusException.class,
                        exception -> assertThat(
                                exception.getStatusCode().value())
                                .isEqualTo(400));
        mockServer.verify();
    }

    @Test
    void shouldTreatSuccessfulButEmptyPythonBodyAsBadGateway() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-EMPTY"))
                .andRespond(withSuccess());

        assertThatThrownBy(() -> service.getRun("SHIP-RUN-EMPTY"))
                .isInstanceOfSatisfying(
                        ResponseStatusException.class,
                        exception -> assertThat(
                                exception.getStatusCode().value())
                                .isEqualTo(502));
        mockServer.verify();
    }

    @Test
    void shouldRejectIncompleteNonNullDataReadinessAsBadGateway() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-BAD-READINESS"))
                .andRespond(withSuccess(
                        runJson().replace(
                                "\"issues\": [{",
                                "\"missing_issues\": [{"),
                        MediaType.APPLICATION_JSON));

        assertThatThrownBy(() -> service.getRun(
                        "SHIP-RUN-BAD-READINESS"))
                .isInstanceOfSatisfying(
                        ResponseStatusException.class,
                        exception -> assertThat(
                                exception.getStatusCode().value())
                                .isEqualTo(502));
        mockServer.verify();
    }

    @Test
    void shouldAllowReadinessWithoutSnapshotBeforeFirstAssessment() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-agent-runs/"
                                + "SHIP-RUN-NOT-ASSESSED"))
                .andRespond(withSuccess(
                        runJson().replace(
                                "\"snapshot_hash\": "
                                        + "\"64-char-synthetic-hash\"",
                                "\"snapshot_hash\": null"),
                        MediaType.APPLICATION_JSON));

        assertThat(service.getRun("SHIP-RUN-NOT-ASSESSED")
                .dataReadiness()
                .snapshotHash()).isNull();
        mockServer.verify();
    }

    @Test
    void shouldReturnServiceUnavailableWhenNacosHasNoPythonInstance() {
        ShipmentAgentRunService unavailableService =
                new ShipmentAgentRunService(
                        emptyDiscoveryClient(), RestClient.builder());

        assertThatThrownBy(unavailableService::listRuns)
                .isInstanceOfSatisfying(
                        ResponseStatusException.class,
                        exception -> assertThat(
                                exception.getStatusCode().value())
                                .isEqualTo(503));
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "Shipment Agent BFF 测试服务发现客户端";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                if (!"load-planning-service".equals(serviceId)) {
                    return List.of();
                }
                return List.of(new DefaultServiceInstance(
                        "synthetic-shipment-agent-instance",
                        serviceId,
                        "127.0.0.1",
                        8010,
                        false));
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
                return "空 Shipment Agent 测试服务发现客户端";
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
                  "run_id": "SHIP-RUN-0001",
                  "case_number": "SHP-DEMO-FCL-001",
                  "case_name": "上海至洛杉矶演示发运",
                  "transport_scenario": "SEA_FCL",
                  "case_status": "ACTIVE",
                  "workflow_version": "shipment-coordinator-v1",
                  "policy_version": "shipment-policy-v1",
                  "status": "WAITING_HUMAN",
                  "current_stage": "AWAITING_BOOKING_SUBMIT_APPROVAL",
                  "run_version": 4,
                  "started_case_revision": 3,
                  "last_observed_case_revision": 4,
                  "required_capabilities": [
                    "LOAD_PLANNING",
                    "LINK_BOOKING"
                  ],
                  "scenario_policy_confirmed": true,
                  "selected_load_task_number": "LP-DEMO-0001",
                  "selected_load_run_id": "LOAD-RUN-0001",
                  "locked_load_version_id": "LOAD-VERSION-0002",
                  "locked_load_version_number": 2,
                  "selected_booking_application_number": "LINK-DEMO-0001",
                  "booking_status": "DRAFT",
                  "carrier_submission_status": "NOT_SUBMITTED",
                  "booking_reference": "BK-2026-0001",
                  "booking_submission_uncertain": false,
                  "data_readiness": {
                    "status": "ACTION_REQUIRED",
                    "revision": 2,
                    "policy_version": "SHIPMENT_DATA_READINESS_V1",
                    "source_mode": "DEMO",
                    "snapshot_hash": "64-char-synthetic-hash",
                    "assessed_at": "2026-10-16T02:00:00Z",
                    "ready_at": null,
                    "issue_count": 1,
                    "blocking_issue_count": 1,
                    "issues": [{
                      "issue_id": "INVENTORY_SHORTAGE:CARGO_ITEM:AGENT-E2E-ITEM-A",
                      "issue_code": "INVENTORY_SHORTAGE",
                      "severity": "BLOCKING",
                      "subject_type": "CARGO_ITEM",
                      "subject_code": "AGENT-E2E-ITEM-A",
                      "fact_type": "AVAILABLE_QUANTITY",
                      "required_value": 24,
                      "actual_value": 8,
                      "unit": "CARTON",
                      "message": "库存不足",
                      "resolution": "SUPPLY_DATA",
                      "owner_role": "WAREHOUSE_OPERATOR"
                    }]
                  },
                  "next_wake_at": null,
                  "failure_code": null,
                  "failure_message": null,
                  "pending_interrupt": {
                    "type": "BOOKING_SUBMIT_APPROVAL",
                    "title": "确认提交 LINK 订舱",
                    "description": "提交后将进入异步承运人处理。",
                    "allowed_actions": ["APPROVE", "STOP"],
                    "details": {
                      "riskLevel": "MEDIUM",
                      "applicationNumber": "LINK-DEMO-0001"
                    }
                  },
                  "allowed_actions": ["STOP", "RECHECK"],
                  "child_agent": {
                    "run_id": "LOAD-RUN-0001",
                    "task_number": "LP-DEMO-0001",
                    "status": "WAITING_DECISION",
                    "current_step": "WAITING_DECISION",
                    "run_version": 2,
                    "next_action": "OPEN_LOAD_WORKBENCH"
                  },
                  "created_at": "2026-10-16T02:00:00Z",
                  "updated_at": "2026-10-16T02:10:00Z",
                  "completed_at": null,
                  "events": [{
                    "id": 71,
                    "sequence": 7,
                    "event_type": "HUMAN_APPROVAL_REQUIRED",
                    "stage": "AWAITING_BOOKING_SUBMIT_APPROVAL",
                    "title": "等待订舱提交审批",
                    "detail": "LINK 草稿已准备完成。",
                    "data": {
                      "tool": "LINK_BOOKING"
                    },
                    "resource_type": "LINK_BOOKING_APPLICATION",
                    "resource_number": "LINK-DEMO-0001",
                    "actor_subject": "demo-operator",
                    "actor_display_name": "演示操作员",
                    "actor_source": "LOCAL_DEMO",
                    "request_id": "11111111-1111-1111-1111-111111111111",
                    "occurred_at": "2026-10-16T02:10:00Z"
                  }],
                  "future_python_field": "BFF safely ignores this"
                }
                """;
    }
}
