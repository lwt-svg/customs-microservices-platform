package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.link.ConfirmLinkBookingDocumentImportRequest;
import com.customsaffairs.platform.link.LinkBookingApplicationResponse;
import com.customsaffairs.platform.link.LinkBookingCandidateFieldResponse;
import com.customsaffairs.platform.link.LinkBookingDocumentCandidatesResponse;
import com.customsaffairs.platform.link.LinkBookingDocumentImportController;
import com.customsaffairs.platform.link.LinkBookingDocumentImportResponse;
import com.customsaffairs.platform.link.LinkBookingDocumentImportService;
import com.customsaffairs.platform.link.LinkBookingStatus;
import java.time.LocalDate;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.client.RestClient;

/** 平台 PDF 导入 Controller 的字段校验和统一响应测试。 */
@WebMvcTest(LinkBookingDocumentImportController.class)
@Import(LinkBookingDocumentImportControllerTest.FakeConfiguration.class)
class LinkBookingDocumentImportControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void shouldUploadPdfThroughPlatformBff() throws Exception {
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "demo-booking.pdf",
                MediaType.APPLICATION_PDF_VALUE,
                "%PDF-1.4 DEMO".getBytes());

        mockMvc.perform(multipart("/api/v1/platform/link-booking/document-imports").file(file))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.status").value("PARSED"))
                .andExpect(jsonPath("$.data.candidates.applicationNumber.value")
                        .value("DEMO-PDF-001"));
    }

    @Test
    void shouldConfirmReviewedDocumentFields() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/document-imports/12/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "applicationNumber": "DEMO-PDF-002",
                                  "originPort": "DEMO-NGB",
                                  "destinationPort": "DEMO-LAX",
                                  "transportMode": "SEA",
                                  "containerType": "40HQ",
                                  "requestedEtd": "2026-10-20"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.applicationNumber").value("DEMO-PDF-002"))
                .andExpect(jsonPath("$.data.transportMode").value("SEA"))
                .andExpect(jsonPath("$.data.status").value("DRAFT"));
    }

    @Test
    void shouldRejectNonDemoNumberDuringDocumentConfirmation() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/document-imports/12/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "applicationNumber": "REAL-001",
                                  "originPort": "DEMO-NGB",
                                  "destinationPort": "DEMO-LAX",
                                  "transportMode": "SEA",
                                  "containerType": "40HQ"
                                }
                                """))
                .andExpect(status().isBadRequest());
    }

    @TestConfiguration
    static class FakeConfiguration {

        @Bean
        LinkBookingDocumentImportService linkBookingDocumentImportService() {
            return new LinkBookingDocumentImportService(null, RestClient.builder()) {
                @Override
                public LinkBookingDocumentImportResponse upload(
                        org.springframework.web.multipart.MultipartFile file) {
                    LinkBookingCandidateFieldResponse candidate =
                            new LinkBookingCandidateFieldResponse(
                                    "DEMO-PDF-001", "APPLICATION_NUMBER: DEMO-PDF-001", "HIGH");
                    return new LinkBookingDocumentImportResponse(
                            12,
                            "PARSED",
                            file.getOriginalFilename(),
                            file.getSize(),
                            "RULES_V1",
                            new LinkBookingDocumentCandidatesResponse(
                                    candidate,
                                    new LinkBookingCandidateFieldResponse(
                                            "DEMO-SHA", "ORIGIN_PORT: DEMO-SHA", "HIGH"),
                                    new LinkBookingCandidateFieldResponse(
                                            "DEMO-LAX", "DESTINATION_PORT: DEMO-LAX", "HIGH"),
                                    new LinkBookingCandidateFieldResponse(
                                            "SEA", "TRANSPORT_MODE: SEA", "HIGH"),
                                    new LinkBookingCandidateFieldResponse(
                                            "40HQ", "CONTAINER_TYPE: 40HQ", "HIGH"),
                                    new LinkBookingCandidateFieldResponse(
                                            "2026-10-20", "REQUESTED_ETD: 2026-10-20", "HIGH")),
                            null,
                            "2026-10-09T10:00:00",
                            "2026-10-09T10:00:00");
                }

                @Override
                public LinkBookingApplicationResponse confirm(
                        long documentId, ConfirmLinkBookingDocumentImportRequest request) {
                    return new LinkBookingApplicationResponse(
                            21,
                            request.applicationNumber(),
                            null,
                            request.originPort(),
                            request.destinationPort(),
                            request.transportMode(),
                            request.containerType(),
                            request.requestedEtd(),
                            LinkBookingStatus.DRAFT,
                            "2026-10-09T10:01:00",
                            "2026-10-09T10:01:00");
                }
            };
        }
    }
}
