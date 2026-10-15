package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.ExpectedCount.once;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import com.customsaffairs.platform.link.ConfirmLinkBookingDocumentImportRequest;
import com.customsaffairs.platform.link.LinkBookingApplicationResponse;
import com.customsaffairs.platform.link.LinkBookingDetailsResponse;
import com.customsaffairs.platform.link.LinkBookingDocumentImportResponse;
import com.customsaffairs.platform.link.LinkBookingDocumentImportService;
import com.customsaffairs.platform.link.LinkBookingItemResponse;
import com.customsaffairs.platform.link.LinkBookingPackageResponse;
import com.customsaffairs.platform.link.LinkBookingPoAllocationResponse;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.client.DefaultServiceInstance;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/** PDF 导入代理测试；全部文件内容和编号均为构造数据。 */
class LinkBookingDocumentImportServiceTest {

    private MockRestServiceServer mockServer;
    private LinkBookingDocumentImportService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new LinkBookingDocumentImportService(discoveryClient(), builder);
    }

    @Test
    void shouldUploadMultipartAndMapCandidates() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-document-imports"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess(documentJson(), MediaType.APPLICATION_JSON));

        LinkBookingDocumentImportResponse result = service.upload(new MockMultipartFile(
                "file",
                "demo-booking.pdf",
                MediaType.APPLICATION_PDF_VALUE,
                "%PDF-1.4 DEMO".getBytes()));

        assertThat(result.status()).isEqualTo("PARSED");
        assertThat(result.candidates().applicationNumber().value()).isEqualTo("DEMO-PDF-001");
        assertThat(result.candidates().originPort().evidence()).contains("DEMO-SHA");
        assertThat(result.candidates().bookingDetails().invoiceNumber())
                .isEqualTo("DEMO-INVOICE-A");
        assertThat(result.candidates().bookingDetails().packages()).hasSize(1);
        assertThat(result.candidates().bookingDetails().packages().getFirst().poAllocations())
                .extracting(LinkBookingPoAllocationResponse::poNumber)
                .containsExactly("DEMO-PO-001");
        mockServer.verify();
    }

    @Test
    void shouldConfirmReviewedFieldsAndMapCreatedDraft() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-document-imports/12/confirm"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(content().json("""
                        {
                          "application_number": "DEMO-PDF-002",
                          "origin_port": "DEMO-NGB",
                          "destination_port": "DEMO-LAX",
                          "transport_mode": "SEA",
                          "container_type": "40HQ",
                          "requested_etd": "2026-10-20",
                          "booking_details": {
                            "invoice_number": "DEMO-INVOICE-A",
                            "packages": [{
                              "description": "DEMO PRODUCT A",
                              "pallets": 2,
                              "po_allocations": [{
                                "po_number": "DEMO-PO-001",
                                "quantity": 200
                              }]
                            }]
                          }
                        }
                        """))
                .andRespond(withSuccess(applicationJson(), MediaType.APPLICATION_JSON));

        LinkBookingApplicationResponse result = service.confirm(
                12,
                new ConfirmLinkBookingDocumentImportRequest(
                        "DEMO-PDF-002",
                        "DEMO-NGB",
                        "DEMO-LAX",
                        "SEA",
                        "40HQ",
                        LocalDate.of(2026, 10, 20),
                        bookingDetails()));

        assertThat(result.applicationNumber()).isEqualTo("DEMO-PDF-002");
        assertThat(result.transportMode()).isEqualTo("SEA");
        assertThat(result.status().name()).isEqualTo("DRAFT");
        assertThat(result.bookingDetails().totalCbm()).isEqualByComparingTo("5.00");
        mockServer.verify();
    }

    @Test
    void shouldTranslateUnsupportedFileType() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-document-imports"))
                .andRespond(withStatus(HttpStatus.UNSUPPORTED_MEDIA_TYPE));

        assertThatThrownBy(() -> service.upload(new MockMultipartFile(
                        "file", "demo.txt", MediaType.TEXT_PLAIN_VALUE, "DEMO".getBytes())))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode()).isEqualTo(
                                HttpStatus.UNSUPPORTED_MEDIA_TYPE));
        mockServer.verify();
    }

    @Test
    void shouldReturnServiceUnavailableBeforeReadingFileWhenDiscoveryIsEmpty() {
        LinkBookingDocumentImportService unavailable =
                new LinkBookingDocumentImportService(emptyDiscoveryClient(), RestClient.builder());

        assertThatThrownBy(() -> unavailable.upload(new MockMultipartFile(
                        "file",
                        "demo.pdf",
                        MediaType.APPLICATION_PDF_VALUE,
                        "%PDF DEMO".getBytes())))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode()).isEqualTo(
                                HttpStatus.SERVICE_UNAVAILABLE));
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "PDF 导入测试服务发现";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                return List.of(new DefaultServiceInstance(
                        "synthetic-link-instance", serviceId, "127.0.0.1", 8000, false));
            }

            @Override
            public List<String> getServices() {
                return List.of("link-booking-service");
            }
        };
    }

    private static DiscoveryClient emptyDiscoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "空 PDF 导入测试服务发现";
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

    private static String documentJson() {
        return """
                {
                  "id": 12,
                  "status": "PARSED",
                  "original_filename": "demo-booking.pdf",
                  "file_size_bytes": 1024,
                  "parser_version": "RULES_V1",
                  "candidates": {
                    "application_number": {
                      "value": "DEMO-PDF-001",
                      "evidence": "APPLICATION_NUMBER: DEMO-PDF-001",
                      "confidence": "HIGH"
                    },
                    "origin_port": {
                      "value": "DEMO-SHA",
                      "evidence": "ORIGIN_PORT: DEMO-SHA",
                      "confidence": "HIGH"
                    },
                    "destination_port": {
                      "value": "DEMO-LAX",
                      "evidence": "DESTINATION_PORT: DEMO-LAX",
                      "confidence": "HIGH"
                    },
                    "transport_mode": {
                      "value": "SEA",
                      "evidence": "TRANSPORT_MODE: SEA",
                      "confidence": "HIGH"
                    },
                    "container_type": {
                      "value": "40HQ",
                      "evidence": "CONTAINER_TYPE: 40HQ",
                      "confidence": "HIGH"
                    },
                    "requested_etd": {
                      "value": "2026-10-20",
                      "evidence": "REQUESTED_ETD: 2026-10-20",
                      "confidence": "HIGH"
                    },
                    "booking_details": {
                      "invoice_number": "DEMO-INVOICE-A",
                      "document_date": "2026-10-01",
                      "ciq_number": "DEMO-CIQ-A",
                      "shipper": "DEMO SHIPPER",
                      "consignee": "DEMO CONSIGNEE",
                      "notify_party": "DEMO NOTIFY",
                      "marks": "DEMO MARKS",
                      "total_pallets": 4,
                      "total_cartons": 40,
                      "total_quantity": 400,
                      "total_cbm": 5.00,
                      "total_net_weight_kg": 1000,
                      "total_gross_weight_kg": 1100,
                      "items": [],
                      "packages": [{
                        "description": "DEMO PRODUCT A",
                        "pallets": 2,
                        "cartons": 20,
                        "quantity": 200,
                        "net_weight_kg": 500,
                        "gross_weight_kg": 550,
                        "cbm": 2.50,
                        "po_allocations": [{
                          "po_number": "DEMO-PO-001",
                          "quantity": 200
                        }]
                      }],
                      "checks": ["Total all 与包组合计一致"]
                    }
                  },
                  "created_application_number": null,
                  "created_at": "2026-10-09T10:00:00",
                  "updated_at": "2026-10-09T10:00:00"
                }
                """;
    }

    private static String applicationJson() {
        return """
                {
                  "id": 21,
                  "application_number": "DEMO-PDF-002",
                  "booking_reference": null,
                  "origin_port": "DEMO-NGB",
                  "destination_port": "DEMO-LAX",
                  "transport_mode": "SEA",
                  "container_type": "40HQ",
                  "requested_etd": "2026-10-20",
                  "booking_details": {
                    "invoice_number": "DEMO-INVOICE-A",
                    "document_date": "2026-10-01",
                    "ciq_number": "DEMO-CIQ-A",
                    "shipper": "DEMO SHIPPER",
                    "consignee": "DEMO CONSIGNEE",
                    "notify_party": "DEMO NOTIFY",
                    "marks": "DEMO MARKS",
                    "total_pallets": 4,
                    "total_cartons": 40,
                    "total_quantity": 400,
                    "total_cbm": 5.00,
                    "total_net_weight_kg": 1000,
                    "total_gross_weight_kg": 1100,
                    "items": [],
                    "packages": [],
                    "checks": []
                  },
                  "status": "DRAFT",
                  "created_at": "2026-10-09T10:01:00",
                  "updated_at": "2026-10-09T10:01:00"
                }
                """;
    }

    private static LinkBookingDetailsResponse bookingDetails() {
        return new LinkBookingDetailsResponse(
                "DEMO-INVOICE-A",
                "2026-10-01",
                "DEMO-CIQ-A",
                "DEMO SHIPPER",
                "DEMO CONSIGNEE",
                "DEMO NOTIFY",
                "DEMO MARKS",
                new BigDecimal("4"),
                new BigDecimal("40"),
                new BigDecimal("400"),
                new BigDecimal("5.00"),
                new BigDecimal("1000"),
                new BigDecimal("1100"),
                List.of(new LinkBookingItemResponse(
                        "ITEM-1",
                        "LOT-1",
                        "DEMO-PO-001",
                        "DEMO PRODUCT A",
                        new BigDecimal("200"),
                        new BigDecimal("20"),
                        new BigDecimal("500"),
                        new BigDecimal("550"))),
                List.of(new LinkBookingPackageResponse(
                        "DEMO PRODUCT A",
                        new BigDecimal("2"),
                        new BigDecimal("20"),
                        new BigDecimal("200"),
                        new BigDecimal("500"),
                        new BigDecimal("550"),
                        new BigDecimal("2.50"),
                        List.of(new LinkBookingPoAllocationResponse(
                                "DEMO-PO-001", new BigDecimal("200"))))),
                List.of("Total all 与包组合计一致"));
    }
}
