package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.loadplanning.HistoricalPalletPatternResponse;
import com.customsaffairs.platform.loadplanning.PackingListCargoCandidateResponse;
import com.customsaffairs.platform.loadplanning.PackingListPreviewController;
import com.customsaffairs.platform.loadplanning.PackingListPreviewResponse;
import com.customsaffairs.platform.loadplanning.PackingListPreviewService;
import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.client.RestClient;

/** Packing List 预览控制器的统一响应和 camelCase JSON 测试。 */
@WebMvcTest(PackingListPreviewController.class)
@Import(PackingListPreviewControllerTest.FakeConfiguration.class)
class PackingListPreviewControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void shouldUploadPdfAndReturnCamelCasePreview() throws Exception {
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "DEMO-PACKING-LIST-A.pdf",
                MediaType.APPLICATION_PDF_VALUE,
                "%PDF-1.4 SYNTHETIC".getBytes());

        mockMvc.perform(multipart(
                                "/api/v1/platform/load-planning/"
                                        + "packing-list-imports/preview")
                        .file(file))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.message").value("success"))
                .andExpect(jsonPath("$.data.parserVersion").value("RULES_V3"))
                .andExpect(jsonPath("$.data.declaredTotalPallets").value(6))
                .andExpect(jsonPath("$.data.cargoCandidates[0].cartonGrossWeightKg")
                        .value(4.0))
                .andExpect(jsonPath("$.data.historicalPalletPatterns[0].loadedHeightMm")
                        .value(1050))
                .andExpect(jsonPath("$.data.cargo_candidates").doesNotExist());
    }

    @TestConfiguration
    static class FakeConfiguration {

        @Bean
        PackingListPreviewService packingListPreviewService() {
            return new PackingListPreviewService(null, RestClient.builder()) {
                @Override
                public PackingListPreviewResponse preview(
                        org.springframework.web.multipart.MultipartFile file) {
                    return previewResponse();
                }
            };
        }

        private static PackingListPreviewResponse previewResponse() {
            return new PackingListPreviewResponse(
                    "RULES_V3",
                    6,
                    new BigDecimal("8.40"),
                    List.of(new PackingListCargoCandidateResponse(
                            "300x200x150-4.00",
                            "DEMO-ITEM-A",
                            List.of("DEMO-ITEM-A", "DEMO-ITEM-B"),
                            "演示产品外箱",
                            120,
                            10,
                            new BigDecimal("3.5"),
                            new BigDecimal("4.00"),
                            new BigDecimal("300"),
                            new BigDecimal("200"),
                            new BigDecimal("150"),
                            "page=1,y=312.4",
                            new BigDecimal("0.96"))),
                    List.of(new HistoricalPalletPatternResponse(
                            "300x200x150-4.00",
                            8,
                            48,
                            3,
                            new BigDecimal("20"),
                            new BigDecimal("1200"),
                            new BigDecimal("800"),
                            new BigDecimal("1050"),
                            new BigDecimal("1.008"),
                            "page=2,y=418.7")),
                    List.of("声明总托数与历史模式需要人工复核"));
        }
    }
}
