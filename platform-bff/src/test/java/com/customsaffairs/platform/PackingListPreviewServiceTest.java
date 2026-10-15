package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.loadplanning.PackingListPreviewService;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.client.DefaultServiceInstance;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.mock.http.client.MockClientHttpRequest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/** Packing List 预览代理的文件保护、multipart 转发、字段适配和错误翻译测试。 */
class PackingListPreviewServiceTest {

    private MockRestServiceServer mockServer;
    private PackingListPreviewService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new PackingListPreviewService(discoveryClient(), builder);
    }

    @Test
    void shouldForwardMultipartAndMapSnakeCasePreview() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header(
                        HttpHeaders.CONTENT_TYPE,
                        org.hamcrest.Matchers.startsWith(MediaType.MULTIPART_FORM_DATA_VALUE)))
                .andExpect(request -> {
                    MockClientHttpRequest mockRequest = (MockClientHttpRequest) request;
                    String multipartBody = new String(
                            mockRequest.getBodyAsBytes(), StandardCharsets.ISO_8859_1);
                    assertThat(multipartBody)
                            .contains("filename=\"DEMO-PACKING-LIST-A.pdf\"");
                    assertThat(multipartBody).contains("%PDF-1.4 SYNTHETIC");
                })
                .andRespond(withSuccess(previewJson(), MediaType.APPLICATION_JSON));

        var preview = service.preview(pdfFile());

        assertThat(preview.parserVersion()).isEqualTo("RULES_V3");
        assertThat(preview.declaredTotalPallets()).isEqualTo(6);
        assertThat(preview.declaredTotalCbm()).isEqualByComparingTo("8.40");
        assertThat(preview.cargoCandidates()).hasSize(1);
        assertThat(preview.cargoCandidates().getFirst().itemCodes())
                .containsExactly("DEMO-ITEM-A", "DEMO-ITEM-B");
        assertThat(preview.cargoCandidates().getFirst().cartonGrossWeightKg())
                .isEqualByComparingTo("4.00");
        assertThat(preview.historicalPalletPatterns()).hasSize(1);
        assertThat(preview.historicalPalletPatterns().getFirst().loadedHeightMm())
                .isEqualByComparingTo("1050");
        assertThat(preview.checks()).containsExactly("声明总托数与历史模式需要人工复核");
        mockServer.verify();
    }

    @Test
    void shouldRejectMoreThanFiveMibBeforeDiscoveryOrRemoteCall() {
        AtomicInteger discoveryCalls = new AtomicInteger();
        DiscoveryClient countingDiscoveryClient = new DiscoveryClient() {
            @Override
            public String description() {
                return "不应执行的服务发现";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                discoveryCalls.incrementAndGet();
                return List.of();
            }

            @Override
            public List<String> getServices() {
                return List.of();
            }
        };
        PackingListPreviewService locallyProtected =
                new PackingListPreviewService(countingDiscoveryClient, RestClient.builder());
        MockMultipartFile oversized = new MockMultipartFile(
                "file",
                "oversized.pdf",
                MediaType.APPLICATION_PDF_VALUE,
                new byte[5 * 1024 * 1024 + 1]);

        assertThatThrownBy(() -> locallyProtected.preview(oversized))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception -> {
                    assertThat(exception.getStatusCode().value()).isEqualTo(413);
                    assertThat(exception.getReason()).contains("5 MiB");
                });
        assertThat(discoveryCalls).hasValue(0);
    }

    @Test
    void shouldKeepUnsupportedPdfAs415() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andRespond(withStatus(HttpStatus.UNSUPPORTED_MEDIA_TYPE));

        assertStatus(service, HttpStatus.UNSUPPORTED_MEDIA_TYPE);
        mockServer.verify();
    }

    @Test
    void shouldKeepUnreadablePdfAs422() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andRespond(withStatus(HttpStatus.UNPROCESSABLE_ENTITY));

        assertThatThrownBy(() -> service.preview(pdfFile()))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception -> {
                    assertThat(exception.getStatusCode())
                            .isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
                    assertThat(exception.getReason()).contains("不支持 OCR");
                });
        mockServer.verify();
    }

    @Test
    void shouldKeepRemoteSizeLimitAs413() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andRespond(withStatus(HttpStatus.PAYLOAD_TOO_LARGE));

        assertStatus(service, HttpStatus.PAYLOAD_TOO_LARGE);
        mockServer.verify();
    }

    @Test
    void shouldTranslatePythonFailureTo502() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andRespond(withServerError());

        assertStatus(service, HttpStatus.BAD_GATEWAY);
        mockServer.verify();
    }

    @Test
    void shouldTranslateEmptyPythonResponseTo502() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8010/api/v1/packing-list-imports/preview"))
                .andRespond(withSuccess());

        assertStatus(service, HttpStatus.BAD_GATEWAY);
        mockServer.verify();
    }

    @Test
    void shouldReturn503WhenNacosHasNoLoadPlanningInstance() {
        PackingListPreviewService unavailable =
                new PackingListPreviewService(emptyDiscoveryClient(), RestClient.builder());

        assertThatThrownBy(() -> unavailable.preview(pdfFile()))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception -> {
                    assertThat(exception.getStatusCode())
                            .isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(exception.getReason()).contains("load-planning-service");
                });
    }

    private static void assertStatus(
            PackingListPreviewService target, HttpStatus expectedStatus) {
        assertThatThrownBy(() -> target.preview(pdfFile()))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode()).isEqualTo(expectedStatus));
    }

    private static MockMultipartFile pdfFile() {
        return new MockMultipartFile(
                "file",
                "DEMO-PACKING-LIST-A.pdf",
                MediaType.APPLICATION_PDF_VALUE,
                "%PDF-1.4 SYNTHETIC".getBytes(StandardCharsets.UTF_8));
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "Packing List 预览测试服务发现";
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
                return "空 Packing List 预览服务发现";
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

    private static String previewJson() {
        return """
                {
                  "parser_version": "RULES_V3",
                  "declared_total_pallets": 6,
                  "declared_total_cbm": 8.40,
                  "cargo_candidates": [{
                    "cargo_key": "300x200x150-4.00",
                    "item_code": "DEMO-ITEM-A",
                    "item_codes": ["DEMO-ITEM-A", "DEMO-ITEM-B"],
                    "description": "演示产品外箱",
                    "carton_quantity": 120,
                    "units_per_carton": 10,
                    "carton_net_weight_kg": 3.5,
                    "carton_gross_weight_kg": 4.00,
                    "length_mm": 300,
                    "width_mm": 200,
                    "height_mm": 150,
                    "source_locator": "page=1,y=312.4",
                    "confidence": 0.96
                  }],
                  "historical_pallet_patterns": [{
                    "cargo_key": "300x200x150-4.00",
                    "cartons_per_layer": 8,
                    "pattern_carton_count": 48,
                    "pallet_quantity": 3,
                    "pallet_tare_weight_kg": 20,
                    "loaded_length_mm": 1200,
                    "loaded_width_mm": 800,
                    "loaded_height_mm": 1050,
                    "cbm": 1.008,
                    "source_locator": "page=2,y=418.7"
                  }],
                  "checks": ["声明总托数与历史模式需要人工复核"]
                }
                """;
    }
}
