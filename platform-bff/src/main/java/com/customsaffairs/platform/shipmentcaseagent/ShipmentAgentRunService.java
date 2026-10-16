package com.customsaffairs.platform.shipmentcaseagent;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.net.URI;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Shipment Coordinator Agent 的 Java BFF 薄代理。
 *
 * <p>这里不实现 LangGraph 节点，也不保存第二份运行状态。Service 只负责：</p>
 *
 * <ol>
 *   <li>通过 Nacos 服务名寻找 {@code load-planning-service}；</li>
 *   <li>把浏览器 camelCase DTO 显式转换为 Python snake_case DTO；</li>
 *   <li>原样透传写命令的 {@code Idempotency-Key}；</li>
 *   <li>把上游状态码翻译为稳定平台语义，并隐藏上游响应正文。</li>
 * </ol>
 *
 * <p>案件、checkpoint、人工中断、运行版本和事件时间线始终由 Python Agent Runtime
 * 持有。这样 Java 仍是适合鉴权和 API 治理的平台层，不会与 Python 工作流形成双状态源。</p>
 */
@Service
public class ShipmentAgentRunService {

    private static final String SERVICE_ID = "load-planning-service";

    private static final ParameterizedTypeReference<List<RemoteShipmentAgentRun>>
            RUN_LIST_TYPE = new ParameterizedTypeReference<>() {
            };

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    public ShipmentAgentRunService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /**
     * 从既有案件启动一条总编排运行。
     *
     * <p>同一个浏览器命令在超时后必须复用同一个幂等键，Python 才能返回原运行而不是创建
     * 第二条运行。案件 revision 同时保护“启动时看到的资源绑定快照”。</p>
     */
    public ShipmentAgentRunResponse startRun(
            String caseNumber,
            StartShipmentAgentRunRequest request,
            String idempotencyKey) {
        String normalizedCaseNumber =
                requiredPathValue(caseNumber, "发运案件编号不能为空。");
        URI uri = buildUri(
                "api",
                "v1",
                "shipment-cases",
                normalizedCaseNumber,
                "agent-runs");
        return post(
                uri,
                new RemoteStartRunRequest(request.expectedCaseRevision()),
                idempotencyKey,
                "启动 Shipment Agent 运行");
    }

    /** 读取案件当前仍可继续的一条运行；没有活动运行时返回 200 + null。 */
    public ShipmentAgentRunResponse getActiveRun(String caseNumber) {
        String normalizedCaseNumber =
                requiredPathValue(caseNumber, "发运案件编号不能为空。");
        URI uri = buildUri(
                "api",
                "v1",
                "shipment-cases",
                normalizedCaseNumber,
                "agent-runs",
                "active");
        return getNullable(uri, "读取案件当前 Shipment Agent 运行");
    }

    /** 查询一份案件的全部运行历史，包括已完成、已终止和失败运行。 */
    public List<ShipmentAgentRunResponse> listRunHistory(String caseNumber) {
        String normalizedCaseNumber =
                requiredPathValue(caseNumber, "发运案件编号不能为空。");
        URI uri = buildUri(
                "api",
                "v1",
                "shipment-cases",
                normalizedCaseNumber,
                "agent-runs",
                "history");
        return getList(uri, "查询案件 Shipment Agent 运行历史");
    }

    /**
     * 查询全局运行列表，供 Agent 运行中心展示真实运行而不是静态能力卡片。
     *
     * <p>筛选、分页若在后续章节加入，也应先由 Python 定义契约，再由本层显式代理。</p>
     */
    public List<ShipmentAgentRunResponse> listRuns() {
        URI uri = buildUri("api", "v1", "shipment-agent-runs");
        return getList(uri, "查询 Shipment Agent 全局运行列表");
    }

    /** 按稳定运行编号读取 checkpoint 对应的最新快照和 Trace。 */
    public ShipmentAgentRunResponse getRun(String runId) {
        String normalizedRunId =
                requiredPathValue(runId, "Shipment Agent 运行编号不能为空。");
        URI uri = buildUri(
                "api", "v1", "shipment-agent-runs", normalizedRunId);
        return get(uri, "读取 Shipment Agent 运行详情");
    }

    /**
     * 回答当前人工中断，并从同一条运行继续。
     *
     * <p>{@code expectedRunVersion} 与 {@code interruptType} 一起防止旧页面处理了新待办；
     * 动作和可选输入都作为结构化 JSON 发送，不让 Python 从自由文本中猜业务意图。</p>
     */
    public ShipmentAgentRunResponse resumeRun(
            String runId,
            ResumeShipmentAgentRunRequest request,
            String idempotencyKey) {
        String normalizedRunId =
                requiredPathValue(runId, "Shipment Agent 运行编号不能为空。");
        URI uri = buildUri(
                "api", "v1", "shipment-agent-runs", normalizedRunId, "resume");
        return post(
                uri,
                RemoteResumeRunRequest.from(request),
                idempotencyKey,
                "恢复 Shipment Agent 运行");
    }

    /**
     * 重新读取 LINK、装载子 Agent 等外部资源后继续判断。
     *
     * <p>这个接口执行一次核对后立即返回，不在 HTTP 线程中持续等待承运人回调。</p>
     */
    public ShipmentAgentRunResponse reconcileRun(
            String runId,
            ReconcileShipmentAgentRunRequest request,
            String idempotencyKey) {
        String normalizedRunId =
                requiredPathValue(runId, "Shipment Agent 运行编号不能为空。");
        URI uri = buildUri(
                "api", "v1", "shipment-agent-runs", normalizedRunId, "reconcile");
        return post(
                uri,
                new RemoteReconcileRunRequest(request.expectedRunVersion()),
                idempotencyKey,
                "重新核对 Shipment Agent 外部状态");
    }

    private ShipmentAgentRunResponse get(URI uri, String operation) {
        try {
            RemoteShipmentAgentRun response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemoteShipmentAgentRun.class);
            return requireBody(response, operation);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    /**
     * 读取允许 {@code data=null} 的查询。
     *
     * <p>案件真实存在但当前没有活动运行不是网关故障。Python 以 200 + JSON null 返回时，
     * BFF 保留 null，让案件工作台显示“尚未启动”；案件本身不存在仍由上游 404 区分。</p>
     */
    private ShipmentAgentRunResponse getNullable(URI uri, String operation) {
        try {
            RemoteShipmentAgentRun response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemoteShipmentAgentRun.class);
            return response == null ? null : response.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    private List<ShipmentAgentRunResponse> getList(URI uri, String operation) {
        try {
            List<RemoteShipmentAgentRun> response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RUN_LIST_TYPE);
            if (response == null) {
                throw badGateway(
                        "Python Agent Runtime 在" + operation + "时返回了空响应。");
            }
            return response.stream()
                    .map(RemoteShipmentAgentRun::toPlatformResponse)
                    .toList();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    private ShipmentAgentRunResponse post(
            URI uri,
            Object body,
            String idempotencyKey,
            String operation) {
        String requiredKey = requiredIdempotencyKey(idempotencyKey);
        try {
            RemoteShipmentAgentRun response = restClient.post()
                    .uri(uri)
                    // 不 trim、不重写：网络重试只有使用完全相同的 key 才是同一条命令。
                    .header("Idempotency-Key", requiredKey)
                    .body(body)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemoteShipmentAgentRun.class);
            return requireBody(response, operation);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (ResourceAccessException exception) {
            throw dependencyUnavailable(operation, exception);
        } catch (RestClientException exception) {
            throw badGateway("调用 Python " + operation + "接口失败。", exception);
        }
    }

    private ShipmentAgentRunResponse requireBody(
            RemoteShipmentAgentRun response, String operation) {
        if (response == null) {
            throw badGateway(
                    "Python Agent Runtime 在" + operation + "时返回了空响应。");
        }
        return response.toPlatformResponse();
    }

    /** 每次请求按服务名发现实例，Java 代码不写死 Python 的主机和端口。 */
    private URI buildUri(String... pathSegments) {
        ServiceInstance instance = discoveryClient.getInstances(SERVICE_ID).stream()
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 load-planning-service 实例。"));

        UriComponentsBuilder builder =
                UriComponentsBuilder.fromUri(instance.getUri());
        for (String pathSegment : pathSegments) {
            builder.pathSegment(pathSegment);
        }
        return builder.build().encode().toUri();
    }

    /**
     * 翻译上游状态但从不读取、拼接或回显上游响应正文。
     *
     * <p>浏览器只获得稳定的平台错误语义，不会看到 Python traceback、数据库信息、内部服务
     * 地址或第三方响应内容。FastAPI 常用的 422 在平台边界归一为 400。</p>
     */
    private void handleRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            ClientHttpResponse response) throws IOException {
        HttpStatus targetStatus = switch (response.getStatusCode().value()) {
            case 404 -> HttpStatus.NOT_FOUND;
            case 409 -> HttpStatus.CONFLICT;
            case 400, 422 -> HttpStatus.BAD_REQUEST;
            case 503 -> HttpStatus.SERVICE_UNAVAILABLE;
            default -> HttpStatus.BAD_GATEWAY;
        };

        String reason = switch (targetStatus) {
            case NOT_FOUND -> "发运案件或 Shipment Agent 运行不存在。";
            case CONFLICT -> "Shipment Agent 运行版本已变化或当前状态不允许该操作。";
            case BAD_REQUEST -> "Shipment Agent 请求不符合 Python 接口要求。";
            case SERVICE_UNAVAILABLE -> "Shipment Agent 依赖服务暂时不可用。";
            default -> "Python Agent Runtime 暂时无法处理 Shipment Agent 请求。";
        };
        throw new ResponseStatusException(targetStatus, reason);
    }

    private static String requiredPathValue(String value, String message) {
        if (value == null || value.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        }
        return value.trim();
    }

    private static String requiredIdempotencyKey(String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Idempotency-Key 请求头不能为空。");
        }
        return idempotencyKey;
    }

    private static String nullableTrim(String value) {
        if (value == null || value.trim().isEmpty()) {
            return null;
        }
        return value.trim();
    }

    private static List<String> nullableTrimmedList(List<String> values) {
        if (values == null) {
            return null;
        }
        return values.stream()
                .map(value -> value == null ? null : value.trim())
                .toList();
    }

    /** 复制远端结构化详情，避免可变 Map 从 HTTP 边界泄漏到平台响应。 */
    private static Map<String, Object> immutableDetails(
            Map<String, Object> values) {
        return values == null
                ? Map.of()
                : Collections.unmodifiableMap(new LinkedHashMap<>(values));
    }

    private static ResponseStatusException badGateway(String reason) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason);
    }

    private static ResponseStatusException badGateway(
            String reason, Throwable cause) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason, cause);
    }

    private static ResponseStatusException dependencyUnavailable(
            String operation, Throwable cause) {
        return new ResponseStatusException(
                HttpStatus.SERVICE_UNAVAILABLE,
                "Python Agent Runtime 在" + operation + "时连接失败或响应超时。",
                cause);
    }

    /** 启动命令的 Python snake_case 边界 DTO。 */
    private record RemoteStartRunRequest(
            @JsonProperty("expected_case_revision") int expectedCaseRevision) {
    }

    /** 回答人工中断的 Python snake_case 边界 DTO。 */
    private record RemoteResumeRunRequest(
            @JsonProperty("expected_run_version") int expectedRunVersion,
            @JsonProperty("interrupt_type")
            ShipmentAgentInterruptType interruptType,
            ShipmentAgentAction action,
            String comment,
            @JsonProperty("selected_resource_number")
            String selectedResourceNumber,
            @JsonProperty("required_capabilities")
            List<String> requiredCapabilities,
            @JsonProperty("data_facts")
            List<RemoteDataFact> dataFacts) {

        private static RemoteResumeRunRequest from(
                ResumeShipmentAgentRunRequest request) {
            return new RemoteResumeRunRequest(
                    request.expectedRunVersion(),
                    request.interruptType(),
                    request.action(),
                    nullableTrim(request.comment()),
                    nullableTrim(request.selectedResourceNumber()),
                    nullableTrimmedList(request.requiredCapabilities()),
                    request.dataFacts() == null
                            ? null
                            : request.dataFacts().stream()
                                    .map(RemoteDataFact::from)
                                    .toList());
        }
    }

    /**
     * 补数命令的 Python snake_case 边界 DTO。
     *
     * <p>value 保留 JsonNode，避免库存数字和主数据确认布尔值在代理时被字符串化。</p>
     */
    private record RemoteDataFact(
            @JsonProperty("subject_type")
            ShipmentDataSubjectType subjectType,
            @JsonProperty("subject_code") String subjectCode,
            @JsonProperty("fact_type") ShipmentDataFactType factType,
            JsonNode value,
            @JsonProperty("source_type")
            ShipmentDataSourceType sourceType,
            @JsonProperty("source_reference") String sourceReference,
            @JsonProperty("source_locator") String sourceLocator,
            @JsonProperty("observed_at") String observedAt,
            @JsonProperty("expires_at") String expiresAt) {

        private static RemoteDataFact from(ShipmentDataFactRequest request) {
            return new RemoteDataFact(
                    request.subjectType(),
                    request.subjectCode().trim(),
                    request.factType(),
                    request.value(),
                    request.sourceType(),
                    nullableTrim(request.sourceReference()),
                    nullableTrim(request.sourceLocator()),
                    nullableTrim(request.observedAt()),
                    nullableTrim(request.expiresAt()));
        }
    }

    /** 单次外部状态核对命令的 Python snake_case 边界 DTO。 */
    private record RemoteReconcileRunRequest(
            @JsonProperty("expected_run_version") int expectedRunVersion) {
    }

    /** Python 返回的完整运行快照；额外字段可由后续 Python 版本安全扩展。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteShipmentAgentRun(
            @JsonProperty("run_id") String runId,
            @JsonProperty("case_number") String caseNumber,
            @JsonProperty("case_name") String caseName,
            @JsonProperty("transport_scenario") String transportScenario,
            @JsonProperty("case_status") String caseStatus,
            @JsonProperty("workflow_version") String workflowVersion,
            @JsonProperty("policy_version") String policyVersion,
            String status,
            @JsonProperty("current_stage") String currentStage,
            @JsonProperty("run_version") int runVersion,
            @JsonProperty("started_case_revision")
            int startedCaseRevision,
            @JsonProperty("last_observed_case_revision")
            int lastObservedCaseRevision,
            @JsonProperty("required_capabilities")
            List<String> requiredCapabilities,
            @JsonProperty("scenario_policy_confirmed")
            boolean scenarioPolicyConfirmed,
            @JsonProperty("selected_load_task_number")
            String selectedLoadTaskNumber,
            @JsonProperty("selected_load_run_id")
            String selectedLoadRunId,
            @JsonProperty("locked_load_version_id")
            String lockedLoadVersionId,
            @JsonProperty("locked_load_version_number")
            Integer lockedLoadVersionNumber,
            @JsonProperty("selected_booking_application_number")
            String selectedBookingApplicationNumber,
            @JsonProperty("booking_status") String bookingStatus,
            @JsonProperty("carrier_submission_status")
            String carrierSubmissionStatus,
            @JsonProperty("booking_reference") String bookingReference,
            @JsonProperty("booking_submission_uncertain")
            boolean bookingSubmissionUncertain,
            @JsonProperty("data_readiness")
            RemoteDataReadiness dataReadiness,
            @JsonProperty("next_wake_at") String nextWakeAt,
            @JsonProperty("wake_reason") String wakeReason,
            List<RemoteAgentNotification> notifications,
            @JsonProperty("failure_code") String failureCode,
            @JsonProperty("failure_message") String failureMessage,
            @JsonProperty("pending_interrupt")
            RemotePendingInterrupt pendingInterrupt,
            @JsonProperty("allowed_actions")
            List<ShipmentAgentAction> allowedActions,
            @JsonProperty("child_agent") RemoteChildAgent childAgent,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt,
            @JsonProperty("completed_at") String completedAt,
            List<RemoteAgentEvent> events) {

        private ShipmentAgentRunResponse toPlatformResponse() {
            if (requiredCapabilities == null
                    || allowedActions == null
                    || events == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的 Shipment Agent 运行"
                                + "缺少能力、可执行动作或事件集合。");
            }
            return new ShipmentAgentRunResponse(
                    runId,
                    caseNumber,
                    caseName,
                    transportScenario,
                    caseStatus,
                    workflowVersion,
                    policyVersion,
                    status,
                    currentStage,
                    runVersion,
                    startedCaseRevision,
                    lastObservedCaseRevision,
                    List.copyOf(requiredCapabilities),
                    scenarioPolicyConfirmed,
                    selectedLoadTaskNumber,
                    selectedLoadRunId,
                    lockedLoadVersionId,
                    lockedLoadVersionNumber,
                    selectedBookingApplicationNumber,
                    bookingStatus,
                    carrierSubmissionStatus,
                    bookingReference,
                    bookingSubmissionUncertain,
                    dataReadiness == null
                            ? null
                            : dataReadiness.toPlatformResponse(),
                    nextWakeAt,
                    wakeReason,
                    notifications == null
                            ? List.of()
                            : notifications.stream()
                                    .map(RemoteAgentNotification::toPlatformResponse)
                                    .toList(),
                    failureCode,
                    failureMessage,
                    pendingInterrupt == null
                            ? null
                            : pendingInterrupt.toPlatformResponse(),
                    List.copyOf(allowedActions),
                    childAgent == null
                            ? null
                            : childAgent.toPlatformResponse(),
                    createdAt,
                    updatedAt,
                    completedAt,
                    events.stream()
                            .map(RemoteAgentEvent::toPlatformResponse)
                            .toList());
        }
    }

    /**
     * Python 数据就绪快照。
     *
     * <p>V2 使用 assessed_at、issue_count 和 blocking_issue_count。checked_at 与
     * issue_counts 只用于读取早期运行，转换后平台会同时给出稳定的兼容投影。</p>
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteDataReadiness(
            String status,
            Integer revision,
            @JsonProperty("policy_version") String policyVersion,
            @JsonProperty("source_mode") String sourceMode,
            @JsonProperty("snapshot_hash") String snapshotHash,
            @JsonProperty("assessed_at") String assessedAt,
            @JsonProperty("checked_at") String checkedAt,
            @JsonProperty("ready_at") String readyAt,
            @JsonProperty("issue_count") Integer issueCount,
            @JsonProperty("blocking_issue_count")
            Integer blockingIssueCount,
            @JsonProperty("issue_counts")
            RemoteDataReadinessIssueCounts issueCounts,
            List<RemoteDataReadinessIssue> issues) {

        private ShipmentDataReadinessResponse toPlatformResponse() {
            if (status == null
                    || revision == null
                    || policyVersion == null
                    || sourceMode == null
                    || issues == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪快照缺少必填字段。");
            }

            int normalizedIssueCount = normalizedIssueCount();
            int normalizedBlockingCount = normalizedBlockingCount();
            if (normalizedIssueCount < 0
                    || normalizedBlockingCount < 0
                    || normalizedBlockingCount > normalizedIssueCount) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪问题计数不合法。");
            }

            int normalizedWarningCount = issueCounts != null
                    && issueCounts.warning() != null
                    ? issueCounts.warning()
                    : normalizedIssueCount - normalizedBlockingCount;
            if (normalizedWarningCount < 0) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪警告计数不合法。");
            }

            List<ShipmentDataReadinessIssueResponse> normalizedIssues =
                    issues.stream()
                            .map(issue -> {
                                if (issue == null) {
                                    throw badGateway(
                                            "Python Agent Runtime 返回了空的"
                                                    + "数据就绪问题。");
                                }
                                return issue.toPlatformResponse();
                            })
                            .toList();

            return new ShipmentDataReadinessResponse(
                    status,
                    revision,
                    policyVersion,
                    sourceMode,
                    snapshotHash,
                    assessedAt == null ? checkedAt : assessedAt,
                    checkedAt,
                    readyAt,
                    normalizedIssueCount,
                    normalizedBlockingCount,
                    new ShipmentDataReadinessIssueCountsResponse(
                            normalizedIssueCount,
                            normalizedBlockingCount,
                            normalizedWarningCount),
                    normalizedIssues);
        }

        private int normalizedIssueCount() {
            if (issueCount != null) {
                return issueCount;
            }
            if (issueCounts == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪快照缺少问题计数。");
            }
            if (issueCounts.total() != null) {
                return issueCounts.total();
            }
            if (issueCounts.blocking() == null
                    || issueCounts.warning() == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪快照缺少问题计数。");
            }
            return issueCounts.blocking() + issueCounts.warning();
        }

        private int normalizedBlockingCount() {
            if (blockingIssueCount != null) {
                return blockingIssueCount;
            }
            if (issueCounts == null || issueCounts.blocking() == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪快照缺少阻塞问题计数。");
            }
            return issueCounts.blocking();
        }
    }

    /** 兼容读取早期 data_readiness.issue_counts 结构。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteDataReadinessIssueCounts(
            Integer total,
            Integer blocking,
            Integer warning) {
    }

    /** Python 返回的一项结构化数据就绪问题。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteDataReadinessIssue(
            @JsonProperty("issue_id") String issueId,
            @JsonProperty("issue_code") String issueCode,
            String severity,
            @JsonProperty("subject_type")
            ShipmentDataSubjectType subjectType,
            @JsonProperty("subject_code") String subjectCode,
            @JsonProperty("fact_type") ShipmentDataFactType factType,
            @JsonProperty("required_value") JsonNode requiredValue,
            @JsonProperty("actual_value") JsonNode actualValue,
            String unit,
            String message,
            ShipmentAgentAction resolution,
            @JsonProperty("owner_role") String ownerRole) {

        private ShipmentDataReadinessIssueResponse toPlatformResponse() {
            if (issueId == null
                    || issueCode == null
                    || severity == null
                    || subjectType == null
                    || subjectCode == null
                    || factType == null
                    || message == null
                    || resolution == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的数据就绪问题缺少必填字段。");
            }
            return new ShipmentDataReadinessIssueResponse(
                    issueId,
                    issueCode,
                    severity,
                    subjectType,
                    subjectCode,
                    factType,
                    requiredValue,
                    actualValue,
                    unit,
                    message,
                    resolution,
                    ownerRole);
        }
    }

    /** Python 当前人工待办响应。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemotePendingInterrupt(
            ShipmentAgentInterruptType type,
            String title,
            String description,
            @JsonProperty("allowed_actions")
            List<ShipmentAgentAction> allowedActions,
            Map<String, Object> details) {

        private ShipmentAgentPendingInterruptResponse toPlatformResponse() {
            if (allowedActions == null) {
                throw badGateway(
                        "Python Agent Runtime 返回的人工待办缺少 allowed_actions。");
            }
            return new ShipmentAgentPendingInterruptResponse(
                    type,
                    title,
                    description,
                    List.copyOf(allowedActions),
                    immutableDetails(details));
        }
    }

    /** Python 返回的装载子 Agent 状态投影。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteChildAgent(
            @JsonProperty("run_id") String runId,
            @JsonProperty("task_number") String taskNumber,
            String status,
            @JsonProperty("current_step") String currentStep,
            @JsonProperty("run_version") int runVersion,
            @JsonProperty("next_action") String nextAction) {

        private ShipmentAgentChildResponse toPlatformResponse() {
            return new ShipmentAgentChildResponse(
                    runId,
                    taskNumber,
                    status,
                    currentStep,
                    runVersion,
                    nextAction);
        }
    }

    /** Python 返回的去重站内通知。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteAgentNotification(
            String id,
            @JsonProperty("notification_type") String notificationType,
            String severity,
            String status,
            String title,
            String message,
            @JsonProperty("owner_role") String ownerRole,
            @JsonProperty("action_context") Map<String, Object> actionContext,
            @JsonProperty("occurrence_count") int occurrenceCount,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("last_seen_at") String lastSeenAt,
            @JsonProperty("resolved_at") String resolvedAt) {

        private ShipmentAgentNotificationResponse toPlatformResponse() {
            return new ShipmentAgentNotificationResponse(
                    id,
                    notificationType,
                    severity,
                    status,
                    title,
                    message,
                    ownerRole,
                    immutableDetails(actionContext),
                    occurrenceCount,
                    createdAt,
                    lastSeenAt,
                    resolvedAt);
        }
    }

    /** Python 追加式 Agent Trace 事件响应。 */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private record RemoteAgentEvent(
            long id,
            int sequence,
            @JsonProperty("event_type") String eventType,
            String stage,
            String title,
            String detail,
            Map<String, Object> data,
            @JsonProperty("resource_type") String resourceType,
            @JsonProperty("resource_number") String resourceNumber,
            @JsonProperty("actor_subject") String actorSubject,
            @JsonProperty("actor_display_name")
            String actorDisplayName,
            @JsonProperty("actor_source") String actorSource,
            @JsonProperty("request_id") String requestId,
            @JsonProperty("occurred_at") String occurredAt) {

        private ShipmentAgentEventResponse toPlatformResponse() {
            return new ShipmentAgentEventResponse(
                    id,
                    sequence,
                    eventType,
                    stage,
                    title,
                    detail,
                    immutableDetails(data),
                    resourceType,
                    resourceNumber,
                    actorSubject,
                    actorDisplayName,
                    actorSource,
                    requestId,
                    occurredAt);
        }
    }
}
