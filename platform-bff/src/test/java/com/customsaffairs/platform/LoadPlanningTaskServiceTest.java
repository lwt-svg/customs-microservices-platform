package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.loadplanning.CreateLoadPlanningTaskRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningCargoItemRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningPalletSpecRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningTaskService;
import java.math.BigDecimal;
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

/** 装载规划 BFF 的服务发现、JSON 适配与错误翻译测试。 */
class LoadPlanningTaskServiceTest {

    private MockRestServiceServer mockServer;
    private LoadPlanningTaskService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new LoadPlanningTaskService(discoveryClient(), builder);
    }

    @Test
    void shouldMapSnakeCaseTaskSummariesToPlatformResponse() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("""
                        [{
                          "id": 1,
                          "task_number": "LP-DEMO-0001",
                          "task_name": "演示装托任务",
                          "status": "DRAFT",
                          "cargo_item_count": 2,
                          "cargo_box_count": 30,
                          "pallet_spec_count": 1,
                          "created_at": "2026-10-09T10:00:00",
                          "updated_at": "2026-10-09T10:00:00"
                        }]
                        """, MediaType.APPLICATION_JSON));

        var tasks = service.listTasks();

        assertThat(tasks).hasSize(1);
        assertThat(tasks.getFirst().taskNumber()).isEqualTo("LP-DEMO-0001");
        assertThat(tasks.getFirst().cargoBoxCount()).isEqualTo(30);
        mockServer.verify();
    }

    @Test
    void shouldConvertNestedCreateRequestToPythonSnakeCase() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(content().json("""
                        {
                          "task_number": "LP-DEMO-0001",
                          "task_name": "演示装托任务",
                          "description": null,
                          "cargo_items": [{
                            "item_code": "ITEM-001",
                            "sku_code": "SKU-A",
                            "cargo_name": "产品 A 外箱",
                            "quantity": 12,
                            "length_mm": 300,
                            "width_mm": 200,
                            "height_mm": 150,
                            "gross_weight_kg": 4.5,
                            "max_stack_layers": null,
                            "max_top_load_kg": null,
                            "stackable": null,
                            "keep_upright": null,
                            "fragile": null,
                            "source_type": "SYNTHETIC_DEMO",
                            "source_reference": null,
                            "source_locator": null,
                            "confidence": null,
                            "verified": false
                          }],
                          "pallet_specs": [{
                            "pallet_code": "PALLET-1200X800",
                            "pallet_name": "1200×800 演示托盘",
                            "quantity": 2,
                            "length_mm": 1200,
                            "width_mm": 800,
                            "height_mm": 144,
                            "tare_weight_kg": 23,
                            "max_load_kg": null,
                            "max_total_height_mm": null,
                            "source_type": "MANUAL",
                            "source_reference": null,
                            "source_locator": null,
                            "confidence": null,
                            "verified": false
                          }]
                        }
                        """))
                .andRespond(withSuccess(taskDetailJson(), MediaType.APPLICATION_JSON));

        var created = service.createTask(createRequest());

        assertThat(created.taskNumber()).isEqualTo("LP-DEMO-0001");
        assertThat(created.units().dimension()).isEqualTo("mm");
        assertThat(created.cargoItems().getFirst().maxStackLayers()).isNull();
        assertThat(created.palletSpecs().getFirst().maxLoadKg()).isNull();
        mockServer.verify();
    }

    @Test
    void shouldReadCompleteTaskDetail() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks/LP-DEMO-0001"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(taskDetailJson(), MediaType.APPLICATION_JSON));

        var detail = service.getTask("LP-DEMO-0001");

        assertThat(detail.cargoItems()).hasSize(1);
        assertThat(detail.cargoItems().getFirst().lengthMm())
                .isEqualByComparingTo("300");
        assertThat(detail.palletSpecs()).hasSize(1);
        mockServer.verify();
    }

    @Test
    void shouldKeepDuplicateTaskAsConflict() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks"))
                .andRespond(withStatus(HttpStatus.CONFLICT));

        assertThatThrownBy(() -> service.createTask(createRequest()))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(409));
        mockServer.verify();
    }

    @Test
    void shouldTranslatePythonValidationFailureToBadRequest() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks"))
                .andRespond(withStatus(HttpStatus.UNPROCESSABLE_ENTITY));

        assertThatThrownBy(() -> service.createTask(createRequest()))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(400));
        mockServer.verify();
    }

    @Test
    void shouldKeepMissingTaskAsNotFound() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks/LP-MISSING"))
                .andRespond(withStatus(HttpStatus.NOT_FOUND));

        assertThatThrownBy(() -> service.getTask("LP-MISSING"))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(404));
        mockServer.verify();
    }

    @Test
    void shouldTranslateRemoteFailureToBadGateway() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/load-planning-tasks"))
                .andRespond(withServerError());

        assertThatThrownBy(service::listTasks)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(502));
        mockServer.verify();
    }

    @Test
    void shouldReturnServiceUnavailableWhenNacosHasNoInstance() {
        var unavailableService =
                new LoadPlanningTaskService(emptyDiscoveryClient(), RestClient.builder());

        assertThatThrownBy(unavailableService::listTasks)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(503));
    }

    private static CreateLoadPlanningTaskRequest createRequest() {
        return new CreateLoadPlanningTaskRequest(
                "LP-DEMO-0001",
                "演示装托任务",
                " ",
                List.of(new LoadPlanningCargoItemRequest(
                        "ITEM-001",
                        "SKU-A",
                        "产品 A 外箱",
                        12,
                        new BigDecimal("300"),
                        new BigDecimal("200"),
                        new BigDecimal("150"),
                        new BigDecimal("4.5"),
                        null,
                        null,
                        null,
                        null,
                        null,
                        "SYNTHETIC_DEMO",
                        null,
                        null,
                        null,
                        false)),
                List.of(new LoadPlanningPalletSpecRequest(
                        "PALLET-1200X800",
                        "1200×800 演示托盘",
                        2,
                        new BigDecimal("1200"),
                        new BigDecimal("800"),
                        new BigDecimal("144"),
                        new BigDecimal("23"),
                        null,
                        null,
                        "MANUAL",
                        null,
                        null,
                        null,
                        false)));
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "装载规划代理测试服务发现客户端";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                if (!"load-planning-service".equals(serviceId)) {
                    return List.of();
                }
                return List.of(new DefaultServiceInstance(
                        "synthetic-load-planning-instance",
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
                return "空装载规划测试服务发现客户端";
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

    private static String taskDetailJson() {
        return """
                {
                  "id": 1,
                  "task_number": "LP-DEMO-0001",
                  "task_name": "演示装托任务",
                  "description": null,
                  "status": "DRAFT",
                  "units": {"dimension": "mm", "weight": "kg"},
                  "cargo_items": [{
                    "id": 11,
                    "item_code": "ITEM-001",
                    "sku_code": "SKU-A",
                    "cargo_name": "产品 A 外箱",
                    "quantity": 12,
                    "length_mm": 300,
                    "width_mm": 200,
                    "height_mm": 150,
                    "gross_weight_kg": 4.5,
                    "max_stack_layers": null,
                    "max_top_load_kg": null,
                    "stackable": null,
                    "keep_upright": null,
                    "fragile": null,
                    "source_type": "SYNTHETIC_DEMO",
                    "source_reference": null,
                    "source_locator": null,
                    "confidence": null,
                    "verified": false
                  }],
                  "pallet_specs": [{
                    "id": 21,
                    "pallet_code": "PALLET-1200X800",
                    "pallet_name": "1200×800 演示托盘",
                    "quantity": 2,
                    "length_mm": 1200,
                    "width_mm": 800,
                    "height_mm": 144,
                    "tare_weight_kg": 23,
                    "max_load_kg": null,
                    "max_total_height_mm": null,
                    "source_type": "MANUAL",
                    "source_reference": null,
                    "source_locator": null,
                    "confidence": null,
                    "verified": false
                  }],
                  "created_at": "2026-10-09T10:00:00",
                  "updated_at": "2026-10-09T10:00:00"
                }
                """;
    }
}
