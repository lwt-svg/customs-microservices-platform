package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.loadplanning.CreateLoadPlanningTaskRequest;
import com.customsaffairs.platform.loadplanning.LoadPlanningCargoItemResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningPalletSpecResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningTaskController;
import com.customsaffairs.platform.loadplanning.LoadPlanningTaskResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningTaskService;
import com.customsaffairs.platform.loadplanning.LoadPlanningTaskSummaryResponse;
import com.customsaffairs.platform.loadplanning.LoadPlanningUnitsResponse;
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

/** 装载规划控制器的 camelCase 响应和嵌套请求校验测试。 */
@WebMvcTest(LoadPlanningTaskController.class)
@Import(LoadPlanningTaskControllerTest.FakeLoadPlanningConfiguration.class)
class LoadPlanningTaskControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void shouldListTaskSummariesThroughBff() throws Exception {
        mockMvc.perform(get("/api/v1/platform/load-planning/tasks"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].taskNumber").value("LP-DEMO-0001"))
                .andExpect(jsonPath("$.data[0].cargoBoxCount").value(12));
    }

    @Test
    void shouldCreateTaskWithUnknownSafetyFieldsLeftNull() throws Exception {
        mockMvc.perform(post("/api/v1/platform/load-planning/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validCreateJson()))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.taskNumber").value("LP-DEMO-0002"))
                .andExpect(jsonPath("$.data.units.dimension").value("mm"))
                .andExpect(jsonPath("$.data.units.weight").value("kg"))
                .andExpect(jsonPath("$.data.cargoItems[0].maxStackLayers").isEmpty())
                .andExpect(jsonPath("$.data.palletSpecs[0].maxLoadKg").isEmpty());
    }

    @Test
    void shouldReadTaskDetailThroughBff() throws Exception {
        mockMvc.perform(get("/api/v1/platform/load-planning/tasks/LP-DEMO-0001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.taskNumber").value("LP-DEMO-0001"))
                .andExpect(jsonPath("$.data.cargoItems[0].lengthMm").value(300))
                .andExpect(jsonPath("$.data.palletSpecs[0].lengthMm").value(1200));
    }

    @Test
    void shouldRejectInvalidNestedCargoDimensions() throws Exception {
        mockMvc.perform(post("/api/v1/platform/load-planning/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validCreateJson().replace("\"lengthMm\": 300", "\"lengthMm\": 0")))
                .andExpect(status().isBadRequest());
    }

    @Test
    void shouldRequireTraceForEnterpriseHistoricalData() throws Exception {
        mockMvc.perform(post("/api/v1/platform/load-planning/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validCreateJson()
                                .replace("\"sourceType\": \"MANUAL\"",
                                        "\"sourceType\": \"ENTERPRISE_HISTORICAL\"")))
                .andExpect(status().isBadRequest());
    }

    @Test
    void shouldRejectVerifiedSyntheticDemoData() throws Exception {
        mockMvc.perform(post("/api/v1/platform/load-planning/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validCreateJson()
                                .replaceFirst("\"verified\": false", "\"verified\": true")))
                .andExpect(status().isBadRequest());
    }

    private static String validCreateJson() {
        return """
                {
                  "taskNumber": "LP-DEMO-0002",
                  "taskName": "BFF 创建测试",
                  "cargoItems": [{
                    "itemCode": "ITEM-001",
                    "cargoName": "测试外箱",
                    "quantity": 12,
                    "lengthMm": 300,
                    "widthMm": 200,
                    "heightMm": 150,
                    "grossWeightKg": 4.5,
                    "sourceType": "SYNTHETIC_DEMO",
                    "verified": false
                  }],
                  "palletSpecs": [{
                    "palletCode": "PALLET-1200X800",
                    "palletName": "测试托盘",
                    "quantity": 2,
                    "lengthMm": 1200,
                    "widthMm": 800,
                    "heightMm": 144,
                    "tareWeightKg": 23,
                    "sourceType": "MANUAL",
                    "verified": false
                  }]
                }
                """;
    }

    @TestConfiguration
    static class FakeLoadPlanningConfiguration {

        @Bean
        LoadPlanningTaskService loadPlanningTaskService() {
            return new LoadPlanningTaskService(null, RestClient.builder()) {
                @Override
                public List<LoadPlanningTaskSummaryResponse> listTasks() {
                    return List.of(new LoadPlanningTaskSummaryResponse(
                            1L,
                            "LP-DEMO-0001",
                            "演示装托任务",
                            "DRAFT",
                            1,
                            12,
                            1,
                            "2026-10-09T10:00:00",
                            "2026-10-09T10:00:00"));
                }

                @Override
                public LoadPlanningTaskResponse createTask(CreateLoadPlanningTaskRequest request) {
                    return detail(request.taskNumber(), request.taskName());
                }

                @Override
                public LoadPlanningTaskResponse getTask(String taskNumber) {
                    return detail(taskNumber, "演示装托任务");
                }
            };
        }

        private static LoadPlanningTaskResponse detail(String taskNumber, String taskName) {
            return new LoadPlanningTaskResponse(
                    1L,
                    taskNumber,
                    taskName,
                    null,
                    "DRAFT",
                    new LoadPlanningUnitsResponse("mm", "kg"),
                    List.of(new LoadPlanningCargoItemResponse(
                            11L,
                            "ITEM-001",
                            null,
                            "测试外箱",
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
                    List.of(new LoadPlanningPalletSpecResponse(
                            21L,
                            "PALLET-1200X800",
                            "测试托盘",
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
                            false)),
                    "2026-10-09T10:00:00",
                    "2026-10-09T10:00:00");
        }
    }
}
