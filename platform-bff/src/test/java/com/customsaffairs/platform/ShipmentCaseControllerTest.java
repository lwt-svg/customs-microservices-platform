package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.shipmentcase.BindShipmentCaseResourceRequest;
import com.customsaffairs.platform.shipmentcase.ChangeShipmentCaseStatusRequest;
import com.customsaffairs.platform.shipmentcase.CreateShipmentCaseRequest;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseController;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseEventResponse;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseResourceBindingResponse;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseResponse;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseService;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseStatus;
import com.customsaffairs.platform.shipmentcase.ShipmentResourceType;
import com.customsaffairs.platform.shipmentcase.ShipmentTransportScenario;
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

/** Shipment Case Controller 的平台路径、camelCase、校验与 header 透传测试。 */
@WebMvcTest(ShipmentCaseController.class)
@Import(ShipmentCaseControllerTest.FakeShipmentCaseConfiguration.class)
class ShipmentCaseControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private CapturedCommands capturedCommands;

    @Test
    void shouldExposeCompleteCaseUsingPlatformCamelCase() throws Exception {
        mockMvc.perform(get("/api/v1/platform/shipment-cases"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].caseNumber").value("SHP-DEMO-FCL-001"))
                .andExpect(jsonPath("$.data[0].responsibleOrganizationCode")
                        .value("DEMO_ORG"))
                .andExpect(jsonPath("$.data[0].transportScenario").value("SEA_FCL"))
                .andExpect(jsonPath("$.data[0].status").value("ACTIVE"))
                .andExpect(jsonPath("$.data[0].revision").value(3))
                .andExpect(jsonPath("$.data[0].createdAt")
                        .value("2026-10-15T02:00:00Z"))
                .andExpect(jsonPath(
                        "$.data[0].resourceBindings[0].resourceStatusAtVerification")
                        .value("DRAFT"))
                .andExpect(jsonPath("$.data[0].events[1].actorDisplayName")
                        .value("SYSTEM_DEMO"))
                .andExpect(jsonPath("$.data[0].events[1].occurredAt")
                        .value("2026-10-15T02:10:00Z"));
    }

    @Test
    void shouldCreateCaseWith201AndForwardIdempotencyHeader() throws Exception {
        mockMvc.perform(post("/api/v1/platform/shipment-cases")
                        .header("Idempotency-Key", "browser-create-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "caseNumber": "SHP-DEMO-FCL-001",
                                  "caseName": "上海至洛杉矶演示发运",
                                  "transportScenario": "SEA_FCL",
                                  "description": "合成数据演示案件",
                                  "actorSubject": "MUST_NOT_REACH_PYTHON",
                                  "responsibleOrganizationCode": "FORGED_ORG"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.caseNumber").value("SHP-DEMO-FCL-001"))
                .andExpect(jsonPath("$.data.revision").value(3));

        assertThat(capturedCommands.createIdempotencyKey)
                .isEqualTo("browser-create-001");
        assertThat(capturedCommands.createRequest.caseName())
                .isEqualTo("上海至洛杉矶演示发运");
        // 请求 DTO 根本没有 actor/organization 字段，多余 JSON 即使出现也无法进入 Service。
        assertThat(CreateShipmentCaseRequest.class.getRecordComponents())
                .extracting(component -> component.getName())
                .doesNotContain(
                        "actorSubject",
                        "actorDisplayName",
                        "actorSource",
                        "responsibleOrganizationCode");
    }

    @Test
    void shouldForwardResourceCommandAndItsIdempotencyHeader() throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-cases/SHP-DEMO-FCL-001/resources")
                        .header("Idempotency-Key", "browser-bind-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "resourceType": "LOAD_PLANNING_TASK",
                                  "resourceNumber": "LP-DEMO-0001",
                                  "expectedRevision": 2
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.resourceBindings[0].resourceNumber")
                        .value("LP-DEMO-0001"));

        assertThat(capturedCommands.bindCaseNumber).isEqualTo("SHP-DEMO-FCL-001");
        assertThat(capturedCommands.bindRequest.expectedRevision()).isEqualTo(2);
        assertThat(capturedCommands.bindIdempotencyKey).isEqualTo("browser-bind-001");
    }

    @Test
    void shouldForwardStatusCommandAndItsIdempotencyHeader() throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-cases/SHP-DEMO-FCL-001/status")
                        .header("Idempotency-Key", "browser-status-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "targetStatus": "ACTIVE",
                                  "expectedRevision": 2,
                                  "reason": "资料核对完成"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("ACTIVE"));

        assertThat(capturedCommands.statusCaseNumber).isEqualTo("SHP-DEMO-FCL-001");
        assertThat(capturedCommands.statusRequest.targetStatus())
                .isEqualTo(ShipmentCaseStatus.ACTIVE);
        assertThat(capturedCommands.statusIdempotencyKey)
                .isEqualTo("browser-status-001");
    }

    @Test
    void shouldRejectWriteWithoutIdempotencyHeader() throws Exception {
        mockMvc.perform(post("/api/v1/platform/shipment-cases")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "caseNumber": "SHP-DEMO-FCL-001",
                                  "caseName": "上海至洛杉矶演示发运",
                                  "transportScenario": "SEA_FCL"
                                }
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    void shouldRejectNonPositiveExpectedRevisionBeforeCallingPython() throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/shipment-cases/SHP-DEMO-FCL-001/resources")
                        .header("Idempotency-Key", "browser-invalid-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "resourceType": "LOAD_PLANNING_TASK",
                                  "resourceNumber": "LP-DEMO-0001",
                                  "expectedRevision": 0
                                }
                                """))
                .andExpect(status().isBadRequest());

        assertThat(capturedCommands.bindRequest).isNull();
    }

    @TestConfiguration
    static class FakeShipmentCaseConfiguration {

        @Bean
        CapturedCommands capturedCommands() {
            return new CapturedCommands();
        }

        @Bean
        ShipmentCaseService shipmentCaseService(CapturedCommands captured) {
            return new ShipmentCaseService(null, RestClient.create()) {
                @Override
                public List<ShipmentCaseResponse> listCases() {
                    return List.of(caseResponse());
                }

                @Override
                public ShipmentCaseResponse getCase(String caseNumber) {
                    return caseResponse();
                }

                @Override
                public ShipmentCaseResponse createCase(
                        CreateShipmentCaseRequest request, String idempotencyKey) {
                    captured.createRequest = request;
                    captured.createIdempotencyKey = idempotencyKey;
                    return caseResponse();
                }

                @Override
                public ShipmentCaseResponse bindResource(
                        String caseNumber,
                        BindShipmentCaseResourceRequest request,
                        String idempotencyKey) {
                    captured.bindCaseNumber = caseNumber;
                    captured.bindRequest = request;
                    captured.bindIdempotencyKey = idempotencyKey;
                    return caseResponse();
                }

                @Override
                public ShipmentCaseResponse changeStatus(
                        String caseNumber,
                        ChangeShipmentCaseStatusRequest request,
                        String idempotencyKey) {
                    captured.statusCaseNumber = caseNumber;
                    captured.statusRequest = request;
                    captured.statusIdempotencyKey = idempotencyKey;
                    return caseResponse();
                }
            };
        }

        private static ShipmentCaseResponse caseResponse() {
            return new ShipmentCaseResponse(
                    "11111111-1111-1111-1111-111111111111",
                    "SHP-DEMO-FCL-001",
                    "上海至洛杉矶演示发运",
                    "DEMO_ORG",
                    ShipmentTransportScenario.SEA_FCL,
                    ShipmentCaseStatus.ACTIVE,
                    3,
                    "合成数据演示案件",
                    "2026-10-15T02:00:00Z",
                    "2026-10-15T02:10:00Z",
                    List.of(new ShipmentCaseResourceBindingResponse(
                            21L,
                            ShipmentResourceType.LOAD_PLANNING_TASK,
                            "LP-DEMO-0001",
                            "DRAFT",
                            "LOCAL_LOAD_PLANNING_DATABASE",
                            "2026-10-15T02:09:59Z",
                            "2026-10-15T02:10:00Z")),
                    List.of(
                            new ShipmentCaseEventResponse(
                                    31L,
                                    1,
                                    "CASE_CREATED",
                                    null,
                                    ShipmentCaseStatus.DRAFT,
                                    null,
                                    null,
                                    "SYSTEM_DEMO",
                                    "SYSTEM_DEMO",
                                    "SYSTEM_DEMO",
                                    "22222222-2222-2222-2222-222222222222",
                                    "发运案件已创建",
                                    "2026-10-15T02:00:00Z"),
                            new ShipmentCaseEventResponse(
                                    32L,
                                    2,
                                    "RESOURCE_BOUND",
                                    null,
                                    null,
                                    ShipmentResourceType.LOAD_PLANNING_TASK,
                                    "LP-DEMO-0001",
                                    "SYSTEM_DEMO",
                                    "SYSTEM_DEMO",
                                    "SYSTEM_DEMO",
                                    "33333333-3333-3333-3333-333333333333",
                                    "已绑定装载任务",
                                    "2026-10-15T02:10:00Z")));
        }
    }

    static class CapturedCommands {
        private CreateShipmentCaseRequest createRequest;
        private String createIdempotencyKey;
        private String bindCaseNumber;
        private BindShipmentCaseResourceRequest bindRequest;
        private String bindIdempotencyKey;
        private String statusCaseNumber;
        private ChangeShipmentCaseStatusRequest statusRequest;
        private String statusIdempotencyKey;
    }
}
