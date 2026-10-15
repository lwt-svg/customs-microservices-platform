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

import com.customsaffairs.platform.link.ChangeLinkBookingApplicationStatusRequest;
import com.customsaffairs.platform.link.CreateLinkBookingApplicationRequest;
import com.customsaffairs.platform.link.LinkBookingApplicationDetailResponse;
import com.customsaffairs.platform.link.LinkBookingApplicationResponse;
import com.customsaffairs.platform.link.LinkBookingApplicationService;
import com.customsaffairs.platform.link.LinkBookingStatus;
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
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

/**
 * LINK 订舱代理服务测试。
 *
 * <p>MockRestServiceServer 只模拟本进程内的 HTTP 交换，验证服务发现地址、字段转换和请求方法；
 * 不会访问真实 Python 或 Nacos。</p>
 */
class LinkBookingApplicationServiceTest {

    private MockRestServiceServer mockServer;
    private LinkBookingApplicationService service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new LinkBookingApplicationService(discoveryClient(), builder);
    }

    /** 验证 Python snake_case 列表响应会转换为 Java camelCase 对象。 */
    @Test
    void shouldDiscoverLinkServiceAndMapApplicationList() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("""
                        [
                          {
                            "id": 1,
                            "application_number": "DEMO-BOOKING-0161",
                            "booking_reference": null,
                            "origin_port": "DEMO-SHA",
                            "destination_port": "DEMO-LAX",
                            "transport_mode": "SEA",
                            "container_type": "40HQ",
                            "requested_etd": "2026-10-20",
                            "status": "DRAFT",
                            "created_at": "2026-10-08T10:00:00",
                            "updated_at": "2026-10-08T10:00:00"
                          }
                        ]
                        """, MediaType.APPLICATION_JSON));

        List<LinkBookingApplicationResponse> result = service.listApplications();

        assertThat(result).hasSize(1);
        assertThat(result.getFirst().applicationNumber()).isEqualTo("DEMO-BOOKING-0161");
        assertThat(result.getFirst().requestedEtd()).isEqualTo(LocalDate.of(2026, 10, 20));
        mockServer.verify();
    }

    /** 详情必须映射当前申请与倒序操作流水，不能让 Vue 根据状态自己猜历史。 */
    @Test
    void shouldDiscoverLinkServiceAndMapApplicationDetail() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/DEMO-BOOKING-0161/detail"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("""
                        {
                          "application": {
                            "id": 1,
                            "application_number": "DEMO-BOOKING-0161",
                            "booking_reference": null,
                            "origin_port": "DEMO-SHA",
                            "destination_port": "DEMO-LAX",
                            "transport_mode": "SEA",
                            "container_type": "40HQ",
                            "requested_etd": "2026-10-20",
                            "status": "SUBMITTED",
                            "created_at": "2026-10-08T10:00:00",
                            "updated_at": "2026-10-08T10:01:00"
                          },
                          "events": [
                            {
                              "id": 2,
                              "event_type": "SUBMITTED",
                              "previous_status": "DRAFT",
                              "current_status": "SUBMITTED",
                              "booking_reference": null,
                              "actor_label": "SYSTEM_DEMO",
                              "occurred_at": "2026-10-08T10:01:00"
                            },
                            {
                              "id": 1,
                              "event_type": "DRAFT_CREATED",
                              "previous_status": null,
                              "current_status": "DRAFT",
                              "booking_reference": null,
                              "actor_label": "SYSTEM_DEMO",
                              "occurred_at": "2026-10-08T10:00:00"
                            }
                          ],
                          "carrier_submission": {
                            "status": "ACCEPTED",
                            "attempt_count": 1,
                            "last_error_message": null,
                            "can_retry": false,
                            "can_cancel": false,
                            "carrier_request_id": "DEMO-CARRIER-001"
                          }
                        }
                        """, MediaType.APPLICATION_JSON));

        LinkBookingApplicationDetailResponse detail =
                service.getApplicationDetail("DEMO-BOOKING-0161");

        assertThat(detail.application().status()).isEqualTo(LinkBookingStatus.SUBMITTED);
        assertThat(detail.events()).hasSize(2);
        assertThat(detail.events().getFirst().eventType()).isEqualTo("SUBMITTED");
        assertThat(detail.events().getFirst().previousStatus()).isEqualTo(LinkBookingStatus.DRAFT);
        assertThat(detail.carrierSubmission().status()).isEqualTo("ACCEPTED");
        assertThat(detail.carrierSubmission().carrierRequestId()).isEqualTo("DEMO-CARRIER-001");
        mockServer.verify();
    }

    /** DEAD 技术投递任务由 Python 使用原幂等键重置，Java 只代理该动作。 */
    @Test
    void shouldRetryCarrierSubmissionThroughPython() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/"
                                + "DEMO-BOOKING-0213/carrier-submission/retry"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("""
                        {
                          "status": "PENDING",
                          "attempt_count": 3,
                          "last_error_message": null,
                          "can_retry": false,
                          "can_cancel": true,
                          "carrier_request_id": null
                        }
                        """, MediaType.APPLICATION_JSON));

        var result = service.retryCarrierSubmission("DEMO-BOOKING-0213");

        assertThat(result.status()).isEqualTo("PENDING");
        assertThat(result.attemptCount()).isEqualTo(3);
        assertThat(result.canRetry()).isFalse();
        mockServer.verify();
    }

    /** 验证 Java camelCase 请求会转换为 Python Pydantic 模型要求的 snake_case JSON。 */
    @Test
    void shouldConvertCreateRequestToPythonJson() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(content().json("""
                        {
                          "application_number": "DEMO-BOOKING-0212",
                          "origin_port": "DEMO-SHA",
                          "destination_port": "DEMO-LAX",
                          "transport_mode": "SEA",
                          "container_type": "40HQ",
                          "requested_etd": "2026-10-20"
                        }
                        """))
                .andRespond(withSuccess(
                        applicationJson("DEMO-BOOKING-0212"),
                        MediaType.APPLICATION_JSON));

        LinkBookingApplicationResponse created = service.createApplication(
                new CreateLinkBookingApplicationRequest(
                        "DEMO-BOOKING-0212",
                        "DEMO-SHA",
                        "DEMO-LAX",
                        "SEA",
                        "40HQ",
                        LocalDate.of(2026, 10, 20)));

        assertThat(created.applicationNumber()).isEqualTo("DEMO-BOOKING-0212");
        assertThat(created.status()).isEqualTo(LinkBookingStatus.DRAFT);
        mockServer.verify();
    }

    /** Python 的重复编号 409 必须保留为平台 409，页面才能给出准确提示。 */
    @Test
    void shouldKeepDuplicateApplicationAsConflict() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.CONFLICT)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"detail\":\"申请编号已存在\"}"));

        assertThatThrownBy(() -> service.createApplication(
                        new CreateLinkBookingApplicationRequest(
                                "DEMO-BOOKING-0212",
                                "DEMO-SHA",
                                "DEMO-LAX",
                                "SEA",
                                "40HQ",
                                null)))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(409));
        mockServer.verify();
    }

    /** Java 必须把状态请求转换为 Python 使用的 target_status，且采用 Nacos 返回的实例地址。 */
    @Test
    void shouldConvertStatusChangeToPythonJsonAndMapAutomaticBookingResult() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/DEMO-BOOKING-0213/status"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(content().json("""
                        {
                          "target_status": "CONFIRMED"
                        }
                        """))
                .andRespond(withSuccess("""
                        {
                          "id": 3,
                          "application_number": "DEMO-BOOKING-0213",
                          "booking_reference": "AUTO-LINK-000003",
                          "origin_port": "DEMO-SHA",
                          "destination_port": "DEMO-LAX",
                          "transport_mode": "SEA",
                          "container_type": "40HQ",
                          "requested_etd": "2026-10-20",
                          "status": "CONFIRMED",
                          "created_at": "2026-10-08T16:00:00",
                          "updated_at": "2026-10-08T16:01:00"
                        }
                        """, MediaType.APPLICATION_JSON));

        LinkBookingApplicationResponse changed = service.changeApplicationStatus(
                "DEMO-BOOKING-0213",
                new ChangeLinkBookingApplicationStatusRequest(LinkBookingStatus.CONFIRMED));

        assertThat(changed.status()).isEqualTo(LinkBookingStatus.CONFIRMED);
        assertThat(changed.bookingReference()).isEqualTo("AUTO-LINK-000003");
        mockServer.verify();
    }

    /** 状态机拒绝转换时必须保留 409，而不是误提示为“申请编号重复”。 */
    @Test
    void shouldKeepInvalidStatusTransitionAsConflict() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/DEMO-BOOKING-0213/status"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.CONFLICT)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"detail\":\"不允许的订舱状态转换\"}"));

        assertThatThrownBy(() -> service.changeApplicationStatus(
                        "DEMO-BOOKING-0213",
                        new ChangeLinkBookingApplicationStatusRequest(LinkBookingStatus.CONFIRMED)))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(409));
        mockServer.verify();
    }

    /** Python 找不到申请时，平台应返回 404，供 Vue 提示用户刷新列表。 */
    @Test
    void shouldMapUnknownApplicationToNotFoundWhenChangingStatus() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/DEMO-MISSING-001/status"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.NOT_FOUND));

        assertThatThrownBy(() -> service.changeApplicationStatus(
                        "DEMO-MISSING-001",
                        new ChangeLinkBookingApplicationStatusRequest(LinkBookingStatus.SUBMITTED)))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(404));
        mockServer.verify();
    }

    /** Python 找不到详情申请时，BFF 必须保持 404，不能伪造成上游系统崩溃。 */
    @Test
    void shouldMapUnknownApplicationDetailToNotFound() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications/DEMO-MISSING-001/detail"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withStatus(HttpStatus.NOT_FOUND));

        assertThatThrownBy(() -> service.getApplicationDetail("DEMO-MISSING-001"))
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(404));
        mockServer.verify();
    }

    /** Nacos 没有返回 LINK 实例时，应明确返回 503，而不是空指针或写死地址。 */
    @Test
    void shouldReturnServiceUnavailableWhenNacosHasNoLinkInstance() {
        LinkBookingApplicationService unavailableService =
                new LinkBookingApplicationService(emptyDiscoveryClient(), RestClient.builder());

        assertThatThrownBy(unavailableService::listApplications)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(503));
    }

    /** Python 返回 5xx 时，BFF 应转换为 502 并隐藏远端内部正文。 */
    @Test
    void shouldTranslatePythonFailureToBadGateway() {
        mockServer.expect(once(), requestTo(
                        "http://127.0.0.1:8000/api/v1/booking-applications"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withServerError());

        assertThatThrownBy(service::listApplications)
                .isInstanceOfSatisfying(ResponseStatusException.class, exception ->
                        assertThat(exception.getStatusCode().value()).isEqualTo(502));
        mockServer.verify();
    }

    private static DiscoveryClient discoveryClient() {
        return new DiscoveryClient() {
            @Override
            public String description() {
                return "LINK 订舱代理测试服务发现客户端";
            }

            @Override
            public List<ServiceInstance> getInstances(String serviceId) {
                if (!"link-booking-service".equals(serviceId)) {
                    return List.of();
                }
                return List.of(new DefaultServiceInstance(
                        "synthetic-link-instance",
                        serviceId,
                        "127.0.0.1",
                        8000,
                        false));
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
                return "不返回任何实例的测试客户端";
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

    private static String applicationJson(String applicationNumber) {
        return """
                {
                  "id": 2,
                  "application_number": "%s",
                  "booking_reference": null,
                  "origin_port": "DEMO-SHA",
                  "destination_port": "DEMO-LAX",
                  "transport_mode": "SEA",
                  "container_type": "40HQ",
                  "requested_etd": "2026-10-20",
                  "status": "DRAFT",
                  "created_at": "2026-10-08T16:00:00",
                  "updated_at": "2026-10-08T16:00:00"
                }
                """.formatted(applicationNumber);
    }
}
