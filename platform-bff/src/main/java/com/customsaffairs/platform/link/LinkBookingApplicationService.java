package com.customsaffairs.platform.link;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.net.URI;
import java.time.LocalDate;
import java.util.List;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Java BFF 查询 Python LINK 订舱申请的代理服务。
 *
 * <p>每次调用先通过 Nacos 的 {@link DiscoveryClient} 获取 LINK 实例，再发送 HTTP 请求。
 * 浏览器只认识 Java BFF 的地址，不会得到 Python 的主机、端口或注册中心信息。</p>
 */
@Service
public class LinkBookingApplicationService {

    /**
     * 必须与 Python Settings.nacos_service_name 的值完全一致。
     *
     * <p>DiscoveryClient 只查询注册中心并返回实例地址；它不会自动替我们调用 Python。
     * 真正的 HTTP 请求由下方 RestClient 完成。</p>
     */
    private static final String LINK_BOOKING_SERVICE_ID = "link-booking-service";

    private static final ParameterizedTypeReference<List<LinkBookingApplicationRemoteResponse>>
            APPLICATION_LIST_TYPE = new ParameterizedTypeReference<>() {
            };

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    /**
     * @param discoveryClient 通过服务名查找 Python LINK 实例。
     * @param restClientBuilder Spring 管理的 HTTP 客户端构建器。
     */
    public LinkBookingApplicationService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /**
     * 查询 Python LINK 中的全部订舱申请。
     *
     * @return 转换为 Java camelCase 字段的申请列表。
     */
    public List<LinkBookingApplicationResponse> listApplications() {
        URI uri = buildLinkUri("api", "v1", "booking-applications");
        try {
            List<LinkBookingApplicationRemoteResponse> applications = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleListRemoteError)
                    .body(APPLICATION_LIST_TYPE);

            if (applications == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 服务返回了空响应。");
            }
            return applications.stream()
                    .map(LinkBookingApplicationRemoteResponse::toPlatformResponse)
                    .toList();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK 订舱查询接口失败。", exception);
        }
    }

    /**
     * 查询一份申请的当前快照与 Python LINK 保存的审计流水。
     *
     * <p>Java 只做服务发现、JSON 字段适配和错误翻译；事件由 Python 与申请状态在同一
     * 数据库事务中写入，因此 BFF 不会另建“第二份流水表”。</p>
     *
     * @param applicationNumber 申请业务编号。
     * @return 申请当前字段及最新事件在前的历史。
     */
    public LinkBookingApplicationDetailResponse getApplicationDetail(String applicationNumber) {
        String normalizedApplicationNumber = applicationNumber.trim();
        if (normalizedApplicationNumber.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "申请编号不能为空。");
        }

        URI uri = buildLinkUri(
                "api", "v1", "booking-applications", normalizedApplicationNumber, "detail");
        try {
            LinkBookingApplicationDetailRemoteResponse detail = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleDetailRemoteError)
                    .body(LinkBookingApplicationDetailRemoteResponse.class);

            if (detail == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 申请详情响应为空。");
            }
            return detail.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK 订舱详情接口失败。", exception);
        }
    }

    /**
     * 通过 Python LINK 创建一份初始状态为 DRAFT 的订舱草稿。
     *
     * <p>Java 不直接连接 LINK 数据库，也不自己生成主键或状态。真正的保存、唯一性检查和
     * 初始状态规则仍由 Python 完成；Java 只负责服务发现、字段适配和错误翻译。</p>
     *
     * @param request Vue 提交给平台的 camelCase 请求。
     * @return Python 成功保存后返回的完整申请。
     */
    public LinkBookingApplicationResponse createApplication(
            CreateLinkBookingApplicationRequest request) {
        URI uri = buildLinkUri("api", "v1", "booking-applications");

        // Python 的 Pydantic 模型使用 snake_case。单独的远程请求 record 可以保证这种命名
        // 只存在于 Java BFF 内部，不会扩散到 Vue 页面。
        LinkBookingCreateRemoteRequest remoteRequest = new LinkBookingCreateRemoteRequest(
                request.applicationNumber().trim(),
                request.originPort().trim(),
                request.destinationPort().trim(),
                request.transportMode().trim(),
                request.containerType().trim(),
                request.requestedEtd());

        try {
            LinkBookingApplicationRemoteResponse application = restClient.post()
                    .uri(uri)
                    .body(remoteRequest)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleCreateRemoteError)
                    .body(LinkBookingApplicationRemoteResponse.class);

            if (application == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 创建成功响应为空。");
            }
            return application.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            // handleRemoteError 已经给出了稳定的平台 HTTP 语义，不能再统一覆盖成 502。
            throw exception;
        } catch (RestClientException exception) {
            // DNS、连接拒绝、超时和无法解析响应等客户端异常统一视为上游调用失败。
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK 创建订舱草稿失败。", exception);
        }
    }

    /**
     * 改变已有订舱申请的状态。
     *
     * <p>当前流程中，Vue 只会请求 DRAFT → SUBMITTED，或在承运人尚未受理时请求
     * CANCELLED。CONFIRMED / REJECTED 只能由经过校验的模拟承运人回调写入，业务页面
     * 不能通过本接口直接确认。Java 不生成订舱参考号，也不在这里复制 Python 的状态转移
     * 规则。</p>
     *
     * @param applicationNumber Vue 列表中已有的申请编号。
     * @param request Vue 使用 camelCase 发送的目标状态。
     * @return Python 状态机完成操作后返回的权威申请对象。
     */
    public LinkBookingApplicationResponse changeApplicationStatus(
            String applicationNumber, ChangeLinkBookingApplicationStatusRequest request) {
        String normalizedApplicationNumber = applicationNumber.trim();
        if (normalizedApplicationNumber.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "申请编号不能为空。");
        }

        URI uri = buildLinkUri(
                "api", "v1", "booking-applications", normalizedApplicationNumber, "status");
        LinkBookingStatusChangeRemoteRequest remoteRequest =
                new LinkBookingStatusChangeRemoteRequest(request.targetStatus());

        try {
            LinkBookingApplicationRemoteResponse application = restClient.post()
                    .uri(uri)
                    .body(remoteRequest)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleStatusChangeRemoteError)
                    .body(LinkBookingApplicationRemoteResponse.class);

            if (application == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 状态变更响应为空。");
            }
            return application.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK 状态变更接口失败。", exception);
        }
    }

    /**
     * 请求 Python 重新投递一条已经进入 DEAD 的承运人提交任务。
     *
     * <p>Java 不自行重放原请求，也不生成新的幂等键。相同任务和幂等键必须由 Python
     * LINK 继续管理。</p>
     */
    public LinkCarrierSubmissionResponse retryCarrierSubmission(String applicationNumber) {
        String normalizedApplicationNumber = applicationNumber.trim();
        if (normalizedApplicationNumber.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "申请编号不能为空。");
        }
        URI uri = buildLinkUri(
                "api",
                "v1",
                "booking-applications",
                normalizedApplicationNumber,
                "carrier-submission",
                "retry");
        try {
            LinkCarrierSubmissionRemoteResponse response = restClient.post()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleCarrierRetryRemoteError)
                    .body(LinkCarrierSubmissionRemoteResponse.class);
            if (response == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 承运人重试响应为空。");
            }
            return response.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK 承运人重试接口失败。", exception);
        }
    }

    /**
     * 使用服务发现得到的实例地址拼装 Python API URI。
     */
    private URI buildLinkUri(String... pathSegments) {
        ServiceInstance instance = discoveryClient.getInstances(LINK_BOOKING_SERVICE_ID).stream()
                // 当前本地实验只有一个实例。findFirst 不是负载均衡；多实例选择将在
                // 后续服务治理章节使用 Spring Cloud LoadBalancer 单独实现。
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 link-booking-service 实例。"));

        UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
        for (String pathSegment : pathSegments) {
            builder.pathSegment(pathSegment);
        }
        return builder.build().encode().toUri();
    }

    /** 列表接口不应收到业务性 4xx；任意上游错误均隐藏为 502。 */
    private void handleListRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        throw new ResponseStatusException(
                HttpStatus.BAD_GATEWAY, "Python LINK 服务暂时无法完成订舱申请查询。");
    }

    /** 将“创建草稿”特有的 Python 错误翻译成平台语义。 */
    private void handleCreateRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();

        if (statusCode == 409) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "申请编号已经存在，请更换 DEMO 编号。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "订舱草稿数据不符合 Python LINK 接口要求。");
        }
        throw new ResponseStatusException(
                HttpStatus.BAD_GATEWAY,
                "Python LINK 服务暂时无法完成订舱申请操作。");
    }

    /** 详情接口的 404 是明确的业务情况；其他 Python 失败不暴露远端内部细节。 */
    private void handleDetailRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();

        if (statusCode == 404) {
            throw new ResponseStatusException(
                    HttpStatus.NOT_FOUND, "订舱申请不存在或已被移除，请刷新列表。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "订舱申请编号不正确。");
        }
        throw new ResponseStatusException(
                HttpStatus.BAD_GATEWAY, "Python LINK 服务暂时无法读取订舱申请详情。");
    }

    /** 将“状态流转”特有的 Python 错误翻译成平台语义。 */
    private void handleStatusChangeRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();

        if (statusCode == 404) {
            throw new ResponseStatusException(
                    HttpStatus.NOT_FOUND, "订舱申请不存在或已被移除，请刷新列表。");
        }
        if (statusCode == 409) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "当前订舱状态不允许此操作，请刷新列表后重试。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "订舱状态参数不正确。");
        }
        throw new ResponseStatusException(
                HttpStatus.BAD_GATEWAY,
                "Python LINK 服务暂时无法完成订舱状态操作。");
    }

    private void handleCarrierRetryRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();
        if (statusCode == 404) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "申请或承运人提交任务不存在。");
        }
        if (statusCode == 409) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "当前承运人提交状态不允许人工重试，请刷新详情。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "申请编号不正确。");
        }
        throw new ResponseStatusException(
                HttpStatus.BAD_GATEWAY, "Python LINK 暂时无法重试承运人提交。");
    }

    /**
     * Java 发往 Python 的创建请求。
     *
     * <p>{@link JsonProperty} 只控制远程 JSON 字段名称。Java 代码内部仍然使用容易阅读的
     * camelCase 访问器，例如 {@code applicationNumber()}。</p>
     */
    private record LinkBookingCreateRemoteRequest(
            @JsonProperty("application_number") String applicationNumber,
            @JsonProperty("origin_port") String originPort,
            @JsonProperty("destination_port") String destinationPort,
            @JsonProperty("transport_mode") String transportMode,
            @JsonProperty("container_type") String containerType,
            @JsonProperty("requested_etd")
            @JsonFormat(shape = JsonFormat.Shape.STRING)
            LocalDate requestedEtd) {
    }

    /** Java 发往 Python 的状态请求；JSON 字段只在这个 BFF 内部转为 snake_case。 */
    private record LinkBookingStatusChangeRemoteRequest(
            @JsonProperty("target_status") LinkBookingStatus targetStatus) {
    }

    /** Python LINK 的 snake_case 响应，只在 BFF 内部使用。 */
    private record LinkBookingApplicationRemoteResponse(
            long id,
            @JsonProperty("application_number") String applicationNumber,
            @JsonProperty("booking_reference") String bookingReference,
            @JsonProperty("origin_port") String originPort,
            @JsonProperty("destination_port") String destinationPort,
            @JsonProperty("transport_mode") String transportMode,
            @JsonProperty("container_type") String containerType,
            @JsonProperty("requested_etd")
            @JsonFormat(shape = JsonFormat.Shape.STRING)
            LocalDate requestedEtd,
            @JsonProperty("booking_details") LinkBookingDetailsRemote bookingDetails,
            LinkBookingStatus status,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt) {

        private LinkBookingApplicationResponse toPlatformResponse() {
            return new LinkBookingApplicationResponse(
                    id,
                    applicationNumber,
                    bookingReference,
                    originPort,
                    destinationPort,
                    transportMode,
                    containerType,
                    requestedEtd,
                    bookingDetails == null ? null : bookingDetails.toPlatformResponse(),
                    status,
                    createdAt,
                    updatedAt);
        }
    }

    /** Python 详情响应；snake_case 只在 BFF 边界存在，Vue 始终接收 camelCase。 */
    private record LinkBookingApplicationDetailRemoteResponse(
            LinkBookingApplicationRemoteResponse application,
            List<LinkBookingApplicationEventRemoteResponse> events,
            @JsonProperty("carrier_submission")
            LinkCarrierSubmissionRemoteResponse carrierSubmission) {

        private LinkBookingApplicationDetailResponse toPlatformResponse() {
            if (application == null || events == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK 申请详情字段不完整。");
            }
            return new LinkBookingApplicationDetailResponse(
                    application.toPlatformResponse(),
                    events.stream()
                            .map(LinkBookingApplicationEventRemoteResponse::toPlatformResponse)
                            .toList(),
                    carrierSubmission == null ? null : carrierSubmission.toPlatformResponse());
        }
    }

    private record LinkCarrierSubmissionRemoteResponse(
            String status,
            @JsonProperty("attempt_count") int attemptCount,
            @JsonProperty("last_error_message") String lastErrorMessage,
            @JsonProperty("can_retry") boolean canRetry,
            @JsonProperty("can_cancel") boolean canCancel,
            @JsonProperty("carrier_request_id") String carrierRequestId) {

        private LinkCarrierSubmissionResponse toPlatformResponse() {
            return new LinkCarrierSubmissionResponse(
                    status,
                    attemptCount,
                    lastErrorMessage,
                    canRetry,
                    canCancel,
                    carrierRequestId);
        }
    }

    /** Python 事件响应；事件类型保留 String，避免 BFF 复制 Python 的事件机规则。 */
    private record LinkBookingApplicationEventRemoteResponse(
            long id,
            @JsonProperty("event_type") String eventType,
            @JsonProperty("previous_status") LinkBookingStatus previousStatus,
            @JsonProperty("current_status") LinkBookingStatus currentStatus,
            @JsonProperty("booking_reference") String bookingReference,
            @JsonProperty("actor_label") String actorLabel,
            @JsonProperty("occurred_at") String occurredAt) {

        private LinkBookingApplicationEventResponse toPlatformResponse() {
            return new LinkBookingApplicationEventResponse(
                    id,
                    eventType,
                    previousStatus,
                    currentStatus,
                    bookingReference,
                    actorLabel,
                    occurredAt);
        }
    }
}
