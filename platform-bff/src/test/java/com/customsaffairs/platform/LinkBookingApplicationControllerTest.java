package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.link.ChangeLinkBookingApplicationStatusRequest;
import com.customsaffairs.platform.link.CreateLinkBookingApplicationRequest;
import com.customsaffairs.platform.link.LinkBookingApplicationDetailResponse;
import com.customsaffairs.platform.link.LinkBookingApplicationEventResponse;
import com.customsaffairs.platform.link.LinkBookingApplicationController;
import com.customsaffairs.platform.link.LinkBookingApplicationResponse;
import com.customsaffairs.platform.link.LinkBookingApplicationService;
import com.customsaffairs.platform.link.LinkBookingStatus;
import com.customsaffairs.platform.link.LinkCarrierSubmissionResponse;
import java.time.LocalDate;
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

/**
 * LINK 订舱代理控制器测试。
 *
 * <p>测试使用手写假服务，不连接真实 Nacos、Python、MySQL 或企业环境。</p>
 */
@WebMvcTest(LinkBookingApplicationController.class)
@Import(LinkBookingApplicationControllerTest.FakeLinkBookingApplicationConfiguration.class)
class LinkBookingApplicationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    /** 验证列表使用 Java BFF 统一响应，并对 Vue 暴露 camelCase 字段。 */
    @Test
    void shouldListLinkBookingApplicationsThroughBff() throws Exception {
        mockMvc.perform(get("/api/v1/platform/link-booking/applications"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].applicationNumber")
                        .value("DEMO-BOOKING-0161"))
                .andExpect(jsonPath("$.data[0].originPort").value("DEMO-SHA"))
                .andExpect(jsonPath("$.data[0].status").value("DRAFT"));
    }

    /** 验证 Vue 使用 camelCase 创建草稿，Java 返回统一外壳和 HTTP 201。 */
    @Test
    void shouldCreateLinkBookingDraftThroughBff() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/applications")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "applicationNumber": "DEMO-BOOKING-0212",
                                  "originPort": "DEMO-SHA",
                                  "destinationPort": "DEMO-LAX",
                                  "transportMode": "SEA",
                                  "containerType": "40HQ",
                                  "requestedEtd": "2026-10-20"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.applicationNumber")
                        .value("DEMO-BOOKING-0212"))
                .andExpect(jsonPath("$.data.status").value("DRAFT"));
    }

    /** 详情页经由 BFF 返回申请快照与审计事件，字段对浏览器保持 camelCase。 */
    @Test
    void shouldReadLinkBookingApplicationDetailThroughBff() throws Exception {
        mockMvc.perform(get("/api/v1/platform/link-booking/applications/DEMO-BOOKING-0161/detail"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.application.applicationNumber")
                        .value("DEMO-BOOKING-0161"))
                .andExpect(jsonPath("$.data.events[0].eventType").value("DRAFT_CREATED"))
                .andExpect(jsonPath("$.data.events[0].actorLabel").value("SYSTEM_DEMO"));
    }

    /** 非 DEMO 编号应在 Java 参数校验阶段被拒绝，不能继续发送给 Python。 */
    @Test
    void shouldRejectNonDemoApplicationNumber() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/applications")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "applicationNumber": "REAL-ORDER-001",
                                  "originPort": "DEMO-SHA",
                                  "destinationPort": "DEMO-LAX",
                                  "transportMode": "SEA",
                                  "containerType": "40HQ"
                                }
                                """))
                .andExpect(status().isBadRequest());
    }

    /** 草稿提交动作通过 Java BFF 发送给 Python，并把更新后的状态返给 Vue。 */
    @Test
    void shouldChangeLinkBookingStatusThroughBff() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/applications/DEMO-BOOKING-0161/status")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "targetStatus": "SUBMITTED"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.applicationNumber")
                        .value("DEMO-BOOKING-0161"))
                .andExpect(jsonPath("$.data.status").value("SUBMITTED"));
    }

    /** 缺少目标状态时由 Java 的 @NotNull 校验拒绝，不应发出远程调用。 */
    @Test
    void shouldRejectStatusChangeWithoutTargetStatus() throws Exception {
        mockMvc.perform(post("/api/v1/platform/link-booking/applications/DEMO-BOOKING-0161/status")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void shouldRetryDeadCarrierSubmissionThroughBff() throws Exception {
        mockMvc.perform(post(
                        "/api/v1/platform/link-booking/applications/"
                                + "DEMO-BOOKING-0161/carrier-submission/retry"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("PENDING"))
                .andExpect(jsonPath("$.data.attemptCount").value(2));
    }

    @TestConfiguration
    static class FakeLinkBookingApplicationConfiguration {

        @Bean
        LinkBookingApplicationService linkBookingApplicationService() {
            return new LinkBookingApplicationService(null, RestClient.builder()) {
                @Override
                public List<LinkBookingApplicationResponse> listApplications() {
                    return List.of(application(LinkBookingStatus.DRAFT));
                }

                @Override
                public LinkBookingApplicationResponse createApplication(
                        CreateLinkBookingApplicationRequest request) {
                    return new LinkBookingApplicationResponse(
                            2L,
                            request.applicationNumber(),
                            null,
                            request.originPort(),
                            request.destinationPort(),
                            request.transportMode(),
                            request.containerType(),
                            request.requestedEtd(),
                            LinkBookingStatus.DRAFT,
                            "2026-10-08T16:00:00",
                            "2026-10-08T16:00:00");
                }

                @Override
                public LinkBookingApplicationDetailResponse getApplicationDetail(
                        String applicationNumber) {
                    LinkBookingApplicationResponse application = application(LinkBookingStatus.DRAFT);
                    return new LinkBookingApplicationDetailResponse(
                            new LinkBookingApplicationResponse(
                                    application.id(),
                                    applicationNumber,
                                    application.bookingReference(),
                                    application.originPort(),
                                    application.destinationPort(),
                                    application.transportMode(),
                                    application.containerType(),
                                    application.requestedEtd(),
                                    application.status(),
                                    application.createdAt(),
                                    application.updatedAt()),
                            List.of(new LinkBookingApplicationEventResponse(
                                    1L,
                                    "DRAFT_CREATED",
                                    null,
                                    LinkBookingStatus.DRAFT,
                                    null,
                                    "SYSTEM_DEMO",
                                    "2026-10-08T10:00:00")),
                            null);
                }

                @Override
                public LinkBookingApplicationResponse changeApplicationStatus(
                        String applicationNumber, ChangeLinkBookingApplicationStatusRequest request) {
                    return new LinkBookingApplicationResponse(
                            1L,
                            applicationNumber,
                            request.targetStatus() == LinkBookingStatus.CONFIRMED
                                    ? "AUTO-LINK-000001"
                                    : null,
                            "DEMO-SHA",
                            "DEMO-LAX",
                            "SEA",
                            "40HQ",
                            LocalDate.of(2026, 10, 20),
                            request.targetStatus(),
                            "2026-10-08T10:00:00",
                            "2026-10-08T16:05:00");
                }

                @Override
                public LinkCarrierSubmissionResponse retryCarrierSubmission(
                        String applicationNumber) {
                    return new LinkCarrierSubmissionResponse(
                            "PENDING", 2, null, false, true, null);
                }
            };
        }

        private static LinkBookingApplicationResponse application(LinkBookingStatus status) {
            return new LinkBookingApplicationResponse(
                    1L,
                    "DEMO-BOOKING-0161",
                    null,
                    "DEMO-SHA",
                    "DEMO-LAX",
                    "SEA",
                    "40HQ",
                    LocalDate.of(2026, 10, 20),
                    status,
                    "2026-10-08T10:00:00",
                    "2026-10-08T10:00:00");
        }
    }
}
