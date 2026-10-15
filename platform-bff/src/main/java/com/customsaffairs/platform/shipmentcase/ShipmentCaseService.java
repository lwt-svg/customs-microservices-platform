package com.customsaffairs.platform.shipmentcase;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.net.URI;
import java.util.List;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Shipment Case 的 Java BFF 薄适配层。
 *
 * <p>本类只负责四件事：通过 Nacos 找到现有 {@code load-planning-service}、调用 Python
 * HTTP API、转换 camelCase/snake_case、把上游错误翻译成稳定的平台状态码。案件状态机、
 * 幂等记录、revision、资源验证和事件持久化均由 Python Agent Runtime 拥有。</p>
 */
@Service
public class ShipmentCaseService {

    private static final String SERVICE_ID = "load-planning-service";
    private static final ParameterizedTypeReference<List<RemoteShipmentCase>>
            CASE_LIST_TYPE = new ParameterizedTypeReference<>() {
            };

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    public ShipmentCaseService(
            DiscoveryClient discoveryClient,
            @Qualifier(ShipmentCaseRestClientConfiguration.REST_CLIENT_BEAN_NAME)
            RestClient restClient) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClient;
    }

    /** 查询案件中心列表。第一阶段按 Python 当前契约返回完整案件快照。 */
    public List<ShipmentCaseResponse> listCases() {
        URI uri = buildUri("api", "v1", "shipment-cases");
        try {
            List<RemoteShipmentCase> response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(CASE_LIST_TYPE);
            if (response == null) {
                throw badGateway("Python Agent Runtime 返回了空案件列表响应。");
            }
            return response.stream().map(RemoteShipmentCase::toPlatformResponse).toList();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable("查询发运案件列表", exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python 发运案件列表接口失败。", exception);
        }
    }

    /** 按稳定案件编号读取当前快照、资源绑定和完整事件时间线。 */
    public ShipmentCaseResponse getCase(String caseNumber) {
        String normalizedCaseNumber = requiredPathValue(caseNumber);
        URI uri = buildUri("api", "v1", "shipment-cases", normalizedCaseNumber);
        return get(uri, "读取发运案件详情");
    }

    /**
     * 创建案件，并原样转发浏览器生成的幂等键。
     *
     * <p>幂等键描述的是 HTTP 命令而非案件字段，因此它只能放在 header 中，不能塞进
     * JSON DTO。BFF 不修改这个值，网络重试时 Python 才能识别为同一条命令。</p>
     */
    public ShipmentCaseResponse createCase(
            CreateShipmentCaseRequest request, String idempotencyKey) {
        URI uri = buildUri("api", "v1", "shipment-cases");
        return post(
                uri,
                RemoteCreateCaseRequest.from(request),
                idempotencyKey,
                "创建发运案件");
    }

    /** 验证并绑定装载任务或 LINK 申请，成功后返回最新案件快照。 */
    public ShipmentCaseResponse bindResource(
            String caseNumber,
            BindShipmentCaseResourceRequest request,
            String idempotencyKey) {
        String normalizedCaseNumber = requiredPathValue(caseNumber);
        URI uri = buildUri(
                "api", "v1", "shipment-cases", normalizedCaseNumber, "resources");
        return post(
                uri,
                RemoteBindResourceRequest.from(request),
                idempotencyKey,
                "绑定发运案件资源");
    }

    /** 把目标状态、revision 和原因交给 Python 状态机处理。 */
    public ShipmentCaseResponse changeStatus(
            String caseNumber,
            ChangeShipmentCaseStatusRequest request,
            String idempotencyKey) {
        String normalizedCaseNumber = requiredPathValue(caseNumber);
        URI uri = buildUri(
                "api", "v1", "shipment-cases", normalizedCaseNumber, "status");
        return post(
                uri,
                RemoteChangeStatusRequest.from(request),
                idempotencyKey,
                "变更发运案件状态");
    }

    private ShipmentCaseResponse get(URI uri, String operation) {
        try {
            RemoteShipmentCase response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemoteShipmentCase.class);
            return requireBody(response, operation);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    private ShipmentCaseResponse post(
            URI uri, Object body, String idempotencyKey, String operation) {
        String requiredKey = requiredIdempotencyKey(idempotencyKey);
        try {
            RemoteShipmentCase response = restClient.post()
                    .uri(uri)
                    // 不 trim、不重新生成：同一浏览器命令重试时必须穿透为同一个 key。
                    .header("Idempotency-Key", requiredKey)
                    .body(body)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemoteShipmentCase.class);
            return requireBody(response, operation);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    private ShipmentCaseResponse requireBody(
            RemoteShipmentCase response, String operation) {
        if (response == null) {
            throw badGateway("Python Agent Runtime 在" + operation + "时返回了空响应。");
        }
        return response.toPlatformResponse();
    }

    /** 每次调用都通过服务名发现实例，不把 Python 主机和端口写死在 Java 代码中。 */
    private URI buildUri(String... pathSegments) {
        ServiceInstance instance = discoveryClient.getInstances(SERVICE_ID).stream()
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 load-planning-service 实例。"));

        UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
        for (String pathSegment : pathSegments) {
            builder.pathSegment(pathSegment);
        }
        return builder.build().encode().toUri();
    }

    /**
     * 翻译 Python 状态，但从不读取或回显远端响应正文。
     *
     * <p>这样 Vue 能得到稳定的 HTTP 语义，同时不会看到 Python 堆栈、数据库细节或内部
     * 校验文本。400 与 FastAPI/Pydantic 常用的 422 都归一为平台 400；未知错误归一为
     * 502。</p>
     */
    private void handleRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            ClientHttpResponse response) throws IOException {
        HttpStatus targetStatus = switch (response.getStatusCode().value()) {
            case 404 -> HttpStatus.NOT_FOUND;
            case 409 -> HttpStatus.CONFLICT;
            case 400, 422 -> HttpStatus.BAD_REQUEST;
            case 502 -> HttpStatus.BAD_GATEWAY;
            case 503 -> HttpStatus.SERVICE_UNAVAILABLE;
            default -> HttpStatus.BAD_GATEWAY;
        };

        String reason = switch (targetStatus) {
            case NOT_FOUND -> "发运案件或待绑定资源不存在。";
            case CONFLICT -> "发运案件已发生变化或当前状态不允许该操作。";
            case BAD_REQUEST -> "发运案件请求不符合 Python 接口要求。";
            case SERVICE_UNAVAILABLE -> "发运案件依赖服务暂时不可用。";
            default -> "Python Agent Runtime 暂时无法处理发运案件请求。";
        };
        throw new ResponseStatusException(targetStatus, reason);
    }

    private static String requiredPathValue(String caseNumber) {
        if (caseNumber == null || caseNumber.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "发运案件编号不能为空。");
        }
        return caseNumber.trim();
    }

    private static String requiredIdempotencyKey(String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "Idempotency-Key 请求头不能为空。");
        }
        return idempotencyKey;
    }

    private static String nullableTrim(String value) {
        if (value == null || value.trim().isEmpty()) {
            return null;
        }
        return value.trim();
    }

    private static ResponseStatusException badGateway(String reason) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason);
    }

    private static ResponseStatusException badGateway(String reason, Throwable cause) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason, cause);
    }

    /**
     * 连接失败、DNS/端口不可达和连接/读取超时都属于当前依赖不可用，平台统一返回 503。
     *
     * <p>对外 reason 是固定文本，不拼接 {@code exception.getMessage()}，因此不会把
     * Python 地址、底层 Socket 文本或其他内部细节返回给浏览器。cause 仅保留在 Java
     * 异常链中，供服务端诊断。</p>
     */
    private static ResponseStatusException dependencyUnavailable(
            String operation, Throwable cause) {
        return new ResponseStatusException(
                HttpStatus.SERVICE_UNAVAILABLE,
                "Python Agent Runtime 在" + operation + "时连接失败或响应超时。",
                cause);
    }

    /** Java 发往 Python 的创建命令，snake_case 被限制在这个远程边界内。 */
    private record RemoteCreateCaseRequest(
            @JsonProperty("case_number") String caseNumber,
            @JsonProperty("case_name") String caseName,
            @JsonProperty("transport_scenario") ShipmentTransportScenario transportScenario,
            String description) {

        private static RemoteCreateCaseRequest from(CreateShipmentCaseRequest request) {
            return new RemoteCreateCaseRequest(
                    request.caseNumber().trim(),
                    request.caseName().trim(),
                    request.transportScenario(),
                    nullableTrim(request.description()));
        }
    }

    /** Java 发往 Python 的资源绑定命令。 */
    private record RemoteBindResourceRequest(
            @JsonProperty("resource_type") ShipmentResourceType resourceType,
            @JsonProperty("resource_number") String resourceNumber,
            @JsonProperty("expected_revision") int expectedRevision) {

        private static RemoteBindResourceRequest from(
                BindShipmentCaseResourceRequest request) {
            return new RemoteBindResourceRequest(
                    request.resourceType(),
                    request.resourceNumber().trim(),
                    request.expectedRevision());
        }
    }

    /** Java 发往 Python 的状态流转命令。 */
    private record RemoteChangeStatusRequest(
            @JsonProperty("target_status") ShipmentCaseStatus targetStatus,
            @JsonProperty("expected_revision") int expectedRevision,
            String reason) {

        private static RemoteChangeStatusRequest from(
                ChangeShipmentCaseStatusRequest request) {
            return new RemoteChangeStatusRequest(
                    request.targetStatus(),
                    request.expectedRevision(),
                    request.reason().trim());
        }
    }

    /** Python 完整案件响应；所有 snake_case 注解只在 BFF 内部可见。 */
    private record RemoteShipmentCase(
            String id,
            @JsonProperty("case_number") String caseNumber,
            @JsonProperty("case_name") String caseName,
            @JsonProperty("responsible_organization_code")
            String responsibleOrganizationCode,
            @JsonProperty("transport_scenario")
            ShipmentTransportScenario transportScenario,
            ShipmentCaseStatus status,
            int revision,
            String description,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt,
            @JsonProperty("resource_bindings")
            List<RemoteResourceBinding> resourceBindings,
            List<RemoteCaseEvent> events) {

        private ShipmentCaseResponse toPlatformResponse() {
            if (resourceBindings == null || events == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的案件缺少资源绑定或事件集合。");
            }
            return new ShipmentCaseResponse(
                    id,
                    caseNumber,
                    caseName,
                    responsibleOrganizationCode,
                    transportScenario,
                    status,
                    revision,
                    description,
                    createdAt,
                    updatedAt,
                    resourceBindings.stream()
                            .map(RemoteResourceBinding::toPlatformResponse)
                            .toList(),
                    events.stream().map(RemoteCaseEvent::toPlatformResponse).toList());
        }
    }

    /** Python 资源绑定响应。 */
    private record RemoteResourceBinding(
            long id,
            @JsonProperty("resource_type") ShipmentResourceType resourceType,
            @JsonProperty("resource_number") String resourceNumber,
            @JsonProperty("resource_status_at_verification")
            String resourceStatusAtVerification,
            @JsonProperty("verification_source") String verificationSource,
            @JsonProperty("verified_at") String verifiedAt,
            @JsonProperty("bound_at") String boundAt) {

        private ShipmentCaseResourceBindingResponse toPlatformResponse() {
            return new ShipmentCaseResourceBindingResponse(
                    id,
                    resourceType,
                    resourceNumber,
                    resourceStatusAtVerification,
                    verificationSource,
                    verifiedAt,
                    boundAt);
        }
    }

    /** Python 追加式案件事件响应。 */
    private record RemoteCaseEvent(
            long id,
            int sequence,
            @JsonProperty("event_type") String eventType,
            @JsonProperty("previous_status") ShipmentCaseStatus previousStatus,
            @JsonProperty("current_status") ShipmentCaseStatus currentStatus,
            @JsonProperty("resource_type") ShipmentResourceType resourceType,
            @JsonProperty("resource_number") String resourceNumber,
            @JsonProperty("actor_subject") String actorSubject,
            @JsonProperty("actor_display_name") String actorDisplayName,
            @JsonProperty("actor_source") String actorSource,
            @JsonProperty("request_id") String requestId,
            String detail,
            @JsonProperty("occurred_at") String occurredAt) {

        private ShipmentCaseEventResponse toPlatformResponse() {
            return new ShipmentCaseEventResponse(
                    id,
                    sequence,
                    eventType,
                    previousStatus,
                    currentStatus,
                    resourceType,
                    resourceNumber,
                    actorSubject,
                    actorDisplayName,
                    actorSource,
                    requestId,
                    detail,
                    occurredAt);
        }
    }
}
