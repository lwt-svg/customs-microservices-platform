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

import com.customsaffairs.platform.shipmentcase.BindShipmentCaseResourceRequest;
import com.customsaffairs.platform.shipmentcase.ChangeShipmentCaseStatusRequest;
import com.customsaffairs.platform.shipmentcase.CreateShipmentCaseRequest;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseService;
import com.customsaffairs.platform.shipmentcase.ShipmentCaseStatus;
import com.customsaffairs.platform.shipmentcase.ShipmentResourceType;
import com.customsaffairs.platform.shipmentcase.ShipmentTransportScenario;
import java.net.SocketTimeoutException;
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
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/** Shipment Case BFF 的 Nacos 路由、字段适配、幂等头和错误翻译测试。 */
class ShipmentCaseServiceTest {

    private MockRestServiceServer mockServer;
    private ShipmentCaseService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new ShipmentCaseService(discoveryClient(), builder.build());
    }

    @Test
    void shouldDiscoverPythonServiceAndMapCompleteSnakeCaseCaseList() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("[" + caseJson() + "]", MediaType.APPLICATION_JSON));

        var cases = service.listCases();

        assertThat(cases).hasSize(1);
        var shipmentCase = cases.getFirst();
        assertThat(shipmentCase.caseNumber()).isEqualTo("SHP-DEMO-FCL-001");
        assertThat(shipmentCase.responsibleOrganizationCode()).isEqualTo("DEMO_ORG");
        assertThat(shipmentCase.transportScenario())
                .isEqualTo(ShipmentTransportScenario.SEA_FCL);
        assertThat(shipmentCase.status()).isEqualTo(ShipmentCaseStatus.ACTIVE);
        assertThat(shipmentCase.revision()).isEqualTo(3);
        assertThat(shipmentCase.createdAt()).isEqualTo("2026-10-15T02:00:00Z");
        assertThat(shipmentCase.resourceBindings()).hasSize(1);
        assertThat(shipmentCase.resourceBindings().getFirst().resourceType())
                .isEqualTo(ShipmentResourceType.LOAD_PLANNING_TASK);
        assertThat(shipmentCase.resourceBindings().getFirst().resourceStatusAtVerification())
                .isEqualTo("DRAFT");
        assertThat(shipmentCase.events()).hasSize(2);
        assertThat(shipmentCase.events().get(1).actorDisplayName())
                .isEqualTo("SYSTEM_DEMO");
        assertThat(shipmentCase.events().get(1).occurredAt())
                .isEqualTo("2026-10-15T02:10:00Z");
        mockServer.verify();
    }

    @Test
    void shouldCreateCaseWithSnakeCaseAndForwardIdempotencyKeyUnchanged() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-create-001"))
                .andExpect(content().json("""
                        {
                          "case_number": "SHP-DEMO-FCL-001",
                          "case_name": "上海至洛杉矶演示发运",
                          "transport_scenario": "SEA_FCL",
                          "description": null
                        }
                        """))
                .andRespond(withSuccess(caseJson(), MediaType.APPLICATION_JSON));

        var response = service.createCase(
                new CreateShipmentCaseRequest(
                        " SHP-DEMO-FCL-001 ",
                        " 上海至洛杉矶演示发运 ",
                        ShipmentTransportScenario.SEA_FCL,
                        " "),
                "browser-create-001");

        assertThat(response.caseNumber()).isEqualTo("SHP-DEMO-FCL-001");
        assertThat(response.revision()).isEqualTo(3);
        mockServer.verify();
    }

    @Test
    void shouldBindResourceWithRevisionAndForwardIdempotencyKey() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-001/resources"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-bind-001"))
                .andExpect(content().json("""
                        {
                          "resource_type": "LOAD_PLANNING_TASK",
                          "resource_number": "LP-DEMO-0001",
                          "expected_revision": 2
                        }
                        """))
                .andRespond(withSuccess(caseJson(), MediaType.APPLICATION_JSON));

        var response = service.bindResource(
                " SHP-DEMO-FCL-001 ",
                new BindShipmentCaseResourceRequest(
                        ShipmentResourceType.LOAD_PLANNING_TASK,
                        " LP-DEMO-0001 ",
                        2),
                "browser-bind-001");

        assertThat(response.resourceBindings().getFirst().resourceNumber())
                .isEqualTo("LP-DEMO-0001");
        mockServer.verify();
    }

    @Test
    void shouldChangeStatusWithReasonAndForwardIdempotencyKey() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/"
                                + "SHP-DEMO-FCL-001/status"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("Idempotency-Key", "browser-status-001"))
                .andExpect(content().json("""
                        {
                          "target_status": "ACTIVE",
                          "expected_revision": 2,
                          "reason": "资料核对完成"
                        }
                        """))
                .andRespond(withSuccess(caseJson(), MediaType.APPLICATION_JSON));

        var response = service.changeStatus(
                "SHP-DEMO-FCL-001",
                new ChangeShipmentCaseStatusRequest(
                        ShipmentCaseStatus.ACTIVE,
                        2,
                        " 资料核对完成 "),
                "browser-status-001");

        assertThat(response.status()).isEqualTo(ShipmentCaseStatus.ACTIVE);
        mockServer.verify();
    }

    @ParameterizedTest
    @CsvSource({
            "404,404",
            "409,409",
            "400,400",
            "422,400",
            "502,502",
            "503,503",
            "500,502"
    })
    void shouldTranslateRemoteErrorsWithoutLeakingPythonBody(
            int remoteStatus, int expectedPlatformStatus) {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/SHP-DEMO-ERROR"))
                .andRespond(withStatus(HttpStatus.valueOf(remoteStatus))
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"detail\":\"PYTHON_SECRET_DATABASE_DETAIL\"}"));

        assertThatThrownBy(() -> service.getCase("SHP-DEMO-ERROR"))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception -> {
                    assertThat(exception.getStatusCode().value())
                            .isEqualTo(expectedPlatformStatus);
                    assertThat(exception.getReason())
                            .doesNotContain("PYTHON_SECRET_DATABASE_DETAIL");
                });
        mockServer.verify();
    }

    @Test
    void shouldTreatSuccessfulButEmptyPythonBodyAsBadGateway() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/shipment-cases/SHP-DEMO-EMPTY"))
                .andRespond(withSuccess());

        assertThatThrownBy(() -> service.getCase("SHP-DEMO-EMPTY"))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(502));
        mockServer.verify();
    }

    @Test
    void shouldReturnServiceUnavailableWhenNacosHasNoPythonInstance() {
        ShipmentCaseService unavailableService =
                new ShipmentCaseService(emptyDiscoveryClient(), RestClient.create());

        assertThatThrownBy(unavailableService::listCases)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(503));
    }

    @Test
    void shouldTranslateTransportTimeoutToStableServiceUnavailableWithoutLeakingCause() {
        ClientHttpRequestFactory timeoutFactory = (uri, method) -> {
            throw new SocketTimeoutException(
                    "PYTHON_INTERNAL_ADDRESS_AND_TIMEOUT_DETAIL");
        };
        ShipmentCaseService timeoutService = new ShipmentCaseService(
                discoveryClient(),
                RestClient.builder().requestFactory(timeoutFactory).build());

        assertThatThrownBy(timeoutService::listCases)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception -> {
                    assertThat(exception.getStatusCode().value()).isEqualTo(503);
                    assertThat(exception.getReason())
                            .isEqualTo(
                                    "Python Agent Runtime 在查询发运案件列表"
                                            + "时连接失败或响应超时。")
                            .doesNotContain("PYTHON_INTERNAL_ADDRESS_AND_TIMEOUT_DETAIL");
                });
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "Shipment Case BFF 测试服务发现客户端";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                if (!"load-planning-service".equals(serviceId)) {
                    return List.of();
                }
                return List.of(new DefaultServiceInstance(
                        "synthetic-agent-runtime-instance",
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
                return "空 Shipment Case 测试服务发现客户端";
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

    private static String caseJson() {
        return """
                {
                  "id": "11111111-1111-1111-1111-111111111111",
                  "case_number": "SHP-DEMO-FCL-001",
                  "case_name": "上海至洛杉矶演示发运",
                  "responsible_organization_code": "DEMO_ORG",
                  "transport_scenario": "SEA_FCL",
                  "status": "ACTIVE",
                  "revision": 3,
                  "description": "合成数据演示案件",
                  "created_at": "2026-10-15T02:00:00Z",
                  "updated_at": "2026-10-15T02:10:00Z",
                  "resource_bindings": [{
                    "id": 21,
                    "resource_type": "LOAD_PLANNING_TASK",
                    "resource_number": "LP-DEMO-0001",
                    "resource_status_at_verification": "DRAFT",
                    "verification_source": "LOCAL_LOAD_PLANNING_DATABASE",
                    "verified_at": "2026-10-15T02:09:59Z",
                    "bound_at": "2026-10-15T02:10:00Z"
                  }],
                  "events": [{
                    "id": 31,
                    "sequence": 1,
                    "event_type": "CASE_CREATED",
                    "previous_status": null,
                    "current_status": "DRAFT",
                    "resource_type": null,
                    "resource_number": null,
                    "actor_subject": "SYSTEM_DEMO",
                    "actor_display_name": "SYSTEM_DEMO",
                    "actor_source": "SYSTEM_DEMO",
                    "request_id": "22222222-2222-2222-2222-222222222222",
                    "detail": "发运案件已创建",
                    "occurred_at": "2026-10-15T02:00:00Z"
                  }, {
                    "id": 32,
                    "sequence": 2,
                    "event_type": "RESOURCE_BOUND",
                    "previous_status": null,
                    "current_status": null,
                    "resource_type": "LOAD_PLANNING_TASK",
                    "resource_number": "LP-DEMO-0001",
                    "actor_subject": "SYSTEM_DEMO",
                    "actor_display_name": "SYSTEM_DEMO",
                    "actor_source": "SYSTEM_DEMO",
                    "request_id": "33333333-3333-3333-3333-333333333333",
                    "detail": "已绑定装载任务",
                    "occurred_at": "2026-10-15T02:10:00Z"
                  }]
                }
                """;
    }
}
