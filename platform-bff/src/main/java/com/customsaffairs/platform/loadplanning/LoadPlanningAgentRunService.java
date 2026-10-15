package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.util.Arrays;
import java.util.List;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * 状态化智能装托运行代理。
 *
 * <p>BFF 的职责仅有四项：通过 Nacos 找到 Python 实例、执行 HTTP 调用、在 camelCase 与 snake_case
 * 之间显式适配、把远程错误翻译为平台稳定状态码。运行状态、 中断恢复、几何计算和人工决策规则都只由 Python load-planning-service 管理。
 */
@Service
public class LoadPlanningAgentRunService {

  private static final String SERVICE_ID = "load-planning-service";

  private final DiscoveryClient discoveryClient;
  private final RestClient restClient;

  public LoadPlanningAgentRunService(
      DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
    this.discoveryClient = discoveryClient;
    this.restClient = restClientBuilder.build();
  }

  /** 为既有任务启动一次可暂停、可恢复的智能体运行。 */
  public LoadPlanningAgentRunResponse createRun(
      String taskNumber, CreateLoadPlanningAgentRunRequest request) {
    return createRun(taskNumber, request, null);
  }

  /**
   * 为既有任务启动一次可暂停、可恢复的智能体运行，并透传可选的幂等键。
   *
   * <p>幂等键放在 HTTP header 而不是业务 JSON 中，因为它描述的是“这次请求”，不是
   * 装载任务本身。网络超时后，浏览器可以用同一个 key 重试，Python 会返回同一条运行，
   * 而不会再创建第二条运行。
   */
  public LoadPlanningAgentRunResponse createRun(
      String taskNumber, CreateLoadPlanningAgentRunRequest request, String idempotencyKey) {
    String normalizedTaskNumber = requiredPathValue(taskNumber, "装载任务编号不能为空。");
    URI uri = buildUri("api", "v1", "load-planning", "tasks", normalizedTaskNumber, "agent-runs");
    return post(
        uri,
        RemoteCreateRunRequest.from(request),
        "启动智能装托运行",
        nullableTrim(idempotencyKey));
  }

  /** 读取运行当前快照，供页面刷新或轮询。 */
  public LoadPlanningAgentRunResponse getRun(String runId) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri = buildUri("api", "v1", "load-planning", "agent-runs", normalizedRunId);
    try {
      RemoteAgentRun response =
          restClient
              .get()
              .uri(uri)
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteAgentRun.class);
      return requireBody(response, "读取智能装托运行");
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python 读取智能装托运行失败。", exception);
    }
  }

  /** 查询任务下仍可继续的运行，支持清缓存或换设备后恢复。 */
  public List<LoadPlanningAgentRunResponse> listActiveRuns(String taskNumber) {
    String normalizedTaskNumber = requiredPathValue(taskNumber, "装载任务编号不能为空。");
    URI uri =
        buildUri("api", "v1", "load-planning", "tasks", normalizedTaskNumber, "agent-runs");
    return getRuns(uri, "查询可恢复智能装托运行");
  }

  /** 查询任务的完整运行历史，包括已批准、已驳回和失败运行。 */
  public List<LoadPlanningAgentRunResponse> listRunHistory(String taskNumber) {
    String normalizedTaskNumber = requiredPathValue(taskNumber, "装载任务编号不能为空。");
    URI uri =
        buildUri(
            "api",
            "v1",
            "load-planning",
            "tasks",
            normalizedTaskNumber,
            "agent-runs",
            "history");
    return getRuns(uri, "查询智能装托运行历史");
  }

  private List<LoadPlanningAgentRunResponse> getRuns(URI uri, String operation) {
    try {
      RemoteAgentRun[] response =
          restClient
              .get()
              .uri(uri)
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteAgentRun[].class);
      if (response == null) {
        return List.of();
      }
      return Arrays.stream(response).map(RemoteAgentRun::toPlatformResponse).toList();
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python " + operation + "失败。", exception);
    }
  }

  /** 提交结构化答案并从同一条持久化 AgentRun 继续运行。 */
  public LoadPlanningAgentRunResponse resumeRun(
      String runId, ResumeLoadPlanningAgentRunRequest request) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri = buildUri("api", "v1", "load-planning", "agent-runs", normalizedRunId, "resume");
    return post(uri, RemoteResumeRunRequest.from(request), "恢复智能装托运行");
  }

  /** 把人工批准或拒绝发送给 Python，由业务服务完成最终状态迁移。 */
  public LoadPlanningAgentRunResponse decideRun(
      String runId, DecideLoadPlanningAgentRunRequest request) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri = buildUri("api", "v1", "load-planning", "agent-runs", normalizedRunId, "decision");
    return post(uri, RemoteDecisionRequest.from(request), "提交智能装托决策");
  }

  /** 从已驳回/失败运行创建一条新的可审计规划运行。 */
  public LoadPlanningAgentRunResponse replanRun(
      String runId, ReplanLoadPlanningAgentRunRequest request) {
    return replanRun(runId, request, null);
  }

  /** 从已驳回/失败运行创建新运行，并把网络重试幂等键透传给 Python。 */
  public LoadPlanningAgentRunResponse replanRun(
      String runId, ReplanLoadPlanningAgentRunRequest request, String idempotencyKey) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri =
        buildUri("api", "v1", "load-planning", "agent-runs", normalizedRunId, "replan");
    return post(
        uri,
        RemoteReplanRequest.from(request),
        "创建重新规划运行",
        nullableTrim(idempotencyKey));
  }

  /**
   * 请求 Python 对某个已存在的候选方案做业务语言解读。
   *
   * <p>这里没有复用 {@link #post(URI, Object, String)}，因为普通写接口返回 {@code RemoteAgentRun}，
   * 而本接口返回独立的解读 DTO。两类响应分开反序列化，能避免把辅助文字误认为运行状态的一部分。
   */
  public LoadPlanningPlanInsightResponse generatePlanInsight(
      String runId, GenerateLoadPlanningPlanInsightRequest request) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri =
        buildUri(
            "api", "v1", "load-planning", "agent-runs", normalizedRunId, "plan-insights");
    try {
      RemotePlanInsight response =
          restClient
              .post()
              .uri(uri)
              .body(RemotePlanInsightRequest.from(request))
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handlePlanInsightRemoteError)
              .body(RemotePlanInsight.class);
      if (response == null) {
        throw badGateway("装载方案智能解读返回了空响应。");
      }
      return response.toPlatformResponse();
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用装载方案智能解读服务失败。", exception);
    }
  }

  private LoadPlanningAgentRunResponse post(URI uri, Object body, String operation) {
    return post(uri, body, operation, null);
  }

  private LoadPlanningAgentRunResponse post(
      URI uri, Object body, String operation, String idempotencyKey) {
    try {
      var requestSpec = restClient.post().uri(uri).body(body);
      if (idempotencyKey != null) {
        requestSpec = requestSpec.header("Idempotency-Key", idempotencyKey);
      }
      RemoteAgentRun response =
          requestSpec
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteAgentRun.class);
      return requireBody(response, operation);
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python " + operation + "失败。", exception);
    }
  }

  private LoadPlanningAgentRunResponse requireBody(RemoteAgentRun response, String operation) {
    if (response == null) {
      throw badGateway("Python 装载规划服务在" + operation + "时返回了空响应。");
    }
    return response.toPlatformResponse();
  }

  private URI buildUri(String... pathSegments) {
    ServiceInstance instance =
        discoveryClient.getInstances(SERVICE_ID).stream()
            .findFirst()
            .orElseThrow(
                () ->
                    new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE, "Nacos 中没有可用的 load-planning-service 实例。"));
    UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
    for (String pathSegment : pathSegments) {
      builder.pathSegment(pathSegment);
    }
    return builder.build().encode().toUri();
  }

  /**
   * Python 的业务状态码按约定原样保留；基础设施错误统一隐藏为 502。
   *
   * <p>404 表示任务或运行不存在，409 表示当前运行状态不允许该动作，422 表示请求 虽然 JSON 合法但不满足业务数据要求。其余远程状态不直接泄露给浏览器。
   */
  private void handleRemoteError(
      org.springframework.http.HttpRequest ignoredRequest, ClientHttpResponse response)
      throws IOException {
    HttpStatus targetStatus =
        switch (response.getStatusCode().value()) {
          case 404 -> HttpStatus.NOT_FOUND;
          case 409 -> HttpStatus.CONFLICT;
          case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
          default -> HttpStatus.BAD_GATEWAY;
        };
    String reason =
        switch (targetStatus) {
          case NOT_FOUND -> "装载任务或智能体运行不存在。";
          case CONFLICT -> "智能体运行当前状态不允许执行该操作。";
          case UNPROCESSABLE_ENTITY -> "智能装托请求不符合 Python 业务接口要求。";
          default -> "Python 装载规划服务暂时无法处理智能体请求。";
        };
    throw new ResponseStatusException(targetStatus, reason);
  }

  /**
   * 智能解读有独立的可用性语义。
   *
   * <p>503 表示“辅助解读未启用或当前不可用”，不是装载规划失败，因此原样返回给页面，让页面给出可继续审批的友好说明。
   * 其他业务状态仍按运行不存在、方案阶段不正确和方案编号不匹配分别保留。
   */
  private void handlePlanInsightRemoteError(
      org.springframework.http.HttpRequest ignoredRequest, ClientHttpResponse response)
      throws IOException {
    HttpStatus targetStatus =
        switch (response.getStatusCode().value()) {
          case 404 -> HttpStatus.NOT_FOUND;
          case 409 -> HttpStatus.CONFLICT;
          case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
          case 503 -> HttpStatus.SERVICE_UNAVAILABLE;
          default -> HttpStatus.BAD_GATEWAY;
        };
    String reason =
        switch (targetStatus) {
          case NOT_FOUND -> "装载规划运行不存在。";
          case CONFLICT -> "当前运行还没有可供解读的候选方案。";
          case UNPROCESSABLE_ENTITY -> "选择的候选方案不属于当前规划运行。";
          case SERVICE_UNAVAILABLE -> "装载方案智能解读当前未启用或暂时不可用。";
          default -> "装载方案智能解读服务暂时无法处理请求。";
        };
    throw new ResponseStatusException(targetStatus, reason);
  }

  private static String requiredPathValue(String value, String message) {
    if (value == null || value.trim().isEmpty()) {
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }
    return value.trim();
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

  /** Java 发往 Python 的启动请求；snake_case 被限制在远程边界内部。 */
  private record RemoteCreateRunRequest(
      @JsonProperty("selected_pallet_code") String selectedPalletCode,
      @JsonProperty("strategy_codes") List<LoadPlanningStrategyCode> strategyCodes) {

    private static RemoteCreateRunRequest from(CreateLoadPlanningAgentRunRequest request) {
      return new RemoteCreateRunRequest(
          nullableTrim(request.selectedPalletCode()), List.copyOf(request.strategyCodes()));
    }
  }

  /** 答案 value 使用 JsonNode，序列化时不会把数字或布尔值改成字符串。 */
  private record RemoteAnswer(String key, JsonNode value) {

    private static RemoteAnswer from(LoadPlanningAgentAnswerRequest answer) {
      return new RemoteAnswer(answer.key().trim(), answer.value());
    }
  }

  private record RemoteResumeRunRequest(
      List<RemoteAnswer> answers,
      @JsonProperty("expected_run_version")
          @JsonInclude(JsonInclude.Include.NON_NULL)
          Integer expectedRunVersion) {

    private static RemoteResumeRunRequest from(ResumeLoadPlanningAgentRunRequest request) {
      return new RemoteResumeRunRequest(
          request.answers().stream().map(RemoteAnswer::from).toList(),
          request.expectedRunVersion());
    }
  }

  private record RemoteDecisionRequest(
      LoadPlanningAgentDecision decision,
      @JsonProperty("plan_code") String planCode,
      String comment,
      @JsonProperty("expected_run_version")
          @JsonInclude(JsonInclude.Include.NON_NULL)
          Integer expectedRunVersion) {

    private static RemoteDecisionRequest from(DecideLoadPlanningAgentRunRequest request) {
      return new RemoteDecisionRequest(
          request.decision(),
          nullableTrim(request.planCode()),
          nullableTrim(request.comment()),
          request.expectedRunVersion());
    }
  }

  private record RemoteReplanRequest(
      String reason,
      @JsonProperty("selected_pallet_code")
          @JsonInclude(JsonInclude.Include.NON_NULL)
          String selectedPalletCode,
      @JsonProperty("strategy_codes")
          @JsonInclude(JsonInclude.Include.NON_NULL)
          List<LoadPlanningStrategyCode> strategyCodes) {

    private static RemoteReplanRequest from(ReplanLoadPlanningAgentRunRequest request) {
      return new RemoteReplanRequest(
          request.reason().trim(),
          nullableTrim(request.selectedPalletCode()),
          request.strategyCodes() == null ? null : List.copyOf(request.strategyCodes()));
    }
  }

  /** 浏览器使用 camelCase，只有跨越 Python HTTP 边界时才转换为 {@code plan_code}。 */
  private record RemotePlanInsightRequest(
      @JsonProperty("plan_code")
          @JsonInclude(JsonInclude.Include.ALWAYS)
          String planCode) {

    private static RemotePlanInsightRequest from(
        GenerateLoadPlanningPlanInsightRequest request) {
      return new RemotePlanInsightRequest(nullableTrim(request.planCode()));
    }
  }

  /** Python 的 snake_case 解读响应；映射后平台和 Vue 始终只看到 camelCase。 */
  private record RemotePlanInsight(
      @JsonProperty("run_id") String runId,
      @JsonProperty("plan_code") String planCode,
      String provider,
      String model,
      Boolean degraded,
      @JsonProperty("generated_at") String generatedAt,
      String summary,
      List<String> tradeoffs,
      List<String> risks,
      @JsonProperty("next_checks") List<String> nextChecks,
      String disclaimer) {

    private LoadPlanningPlanInsightResponse toPlatformResponse() {
      return new LoadPlanningPlanInsightResponse(
          runId,
          planCode,
          provider,
          model,
          Boolean.TRUE.equals(degraded),
          generatedAt,
          summary,
          safeStrings(tradeoffs),
          safeStrings(risks),
          safeStrings(nextChecks),
          disclaimer);
    }
  }

  /** Python 顶层运行快照；每一层都显式转换为平台响应 record。 */
  private record RemoteAgentRun(
      @JsonProperty("run_id") String runId,
      @JsonProperty("parent_run_id") String parentRunId,
      @JsonProperty("task_number") String taskNumber,
      String status,
      @JsonProperty("current_step") String currentStep,
      @JsonProperty("run_version") Integer runVersion,
      @JsonProperty("selected_pallet_code") String selectedPalletCode,
      @JsonProperty("selected_plan_code") String selectedPlanCode,
      @JsonProperty("failure_code") String failureCode,
      @JsonProperty("failure_message") String failureMessage,
      List<RemoteQuestion> questions,
      List<RemoteEvent> events,
      List<RemotePlan> plans) {

    private LoadPlanningAgentRunResponse toPlatformResponse() {
      return new LoadPlanningAgentRunResponse(
          runId,
          parentRunId,
          taskNumber,
          status,
          currentStep,
          runVersion,
          selectedPalletCode,
          selectedPlanCode,
          failureCode,
          failureMessage,
          mapList(questions, RemoteQuestion::toPlatformResponse),
          mapList(events, RemoteEvent::toPlatformResponse),
          mapList(plans, RemotePlan::toPlatformResponse));
    }
  }

  private record RemoteQuestion(
      String key,
      String label,
      @JsonProperty("value_type") String valueType,
      String unit,
      String reason,
      Boolean required,
      @JsonProperty("min_value") BigDecimal minValue,
      @JsonProperty("max_value") BigDecimal maxValue) {

    private LoadPlanningAgentQuestionResponse toPlatformResponse() {
      return new LoadPlanningAgentQuestionResponse(
          key, label, valueType, unit, reason, required, minValue, maxValue);
    }
  }

  private record RemoteEvent(
      Long sequence,
      @JsonProperty("event_type") String eventType,
      String title,
      String detail,
      @JsonProperty("created_at") String createdAt) {

    private LoadPlanningAgentEventResponse toPlatformResponse() {
      return new LoadPlanningAgentEventResponse(sequence, eventType, title, detail, createdAt);
    }
  }

  private record RemotePlan(
      @JsonProperty("plan_id") String planId,
      @JsonProperty("plan_code") String planCode,
      @JsonProperty("strategy_code") String strategyCode,
      @JsonProperty("strategy_name") String strategyName,
      @JsonProperty("algorithm_version") String algorithmVersion,
      @JsonProperty("score_version") String scoreVersion,
      Boolean feasible,
      BigDecimal score,
      String summary,
      RemoteMetrics metrics,
      List<String> warnings,
      List<RemotePallet> pallets) {

    private LoadPlanningAgentPlanResponse toPlatformResponse() {
      return new LoadPlanningAgentPlanResponse(
          planId,
          planCode,
          strategyCode,
          strategyName,
          algorithmVersion,
          scoreVersion,
          feasible,
          score,
          summary,
          metrics == null ? null : metrics.toPlatformResponse(),
          warnings == null ? List.of() : List.copyOf(warnings),
          mapList(pallets, RemotePallet::toPlatformResponse));
    }
  }

  private record RemoteMetrics(
      @JsonProperty("total_pallets") Integer totalPallets,
      @JsonProperty("total_boxes") Integer totalBoxes,
      @JsonProperty("unplaced_boxes") Integer unplacedBoxes,
      @JsonProperty("volume_utilization") BigDecimal volumeUtilization,
      @JsonProperty("weight_utilization") BigDecimal weightUtilization,
      @JsonProperty("average_loaded_height_mm") BigDecimal averageLoadedHeightMm,
      @JsonProperty("max_loaded_height_mm") BigDecimal maxLoadedHeightMm,
      @JsonProperty("total_gross_weight_kg") BigDecimal totalGrossWeightKg) {

    private LoadPlanningAgentMetricsResponse toPlatformResponse() {
      return new LoadPlanningAgentMetricsResponse(
          totalPallets,
          totalBoxes,
          unplacedBoxes,
          volumeUtilization,
          weightUtilization,
          averageLoadedHeightMm,
          maxLoadedHeightMm,
          totalGrossWeightKg);
    }
  }

  private record RemotePallet(
      @JsonProperty("pallet_index") Integer palletIndex,
      @JsonProperty("pallet_code") String palletCode,
      @JsonProperty("loaded_height_mm") BigDecimal loadedHeightMm,
      @JsonProperty("gross_weight_kg") BigDecimal grossWeightKg,
      @JsonProperty("box_count") Integer boxCount,
      List<RemotePlacement> placements) {

    private LoadPlanningAgentPalletResponse toPlatformResponse() {
      return new LoadPlanningAgentPalletResponse(
          palletIndex,
          palletCode,
          loadedHeightMm,
          grossWeightKg,
          boxCount,
          mapList(placements, RemotePlacement::toPlatformResponse));
    }
  }

  private record RemotePlacement(
      @JsonProperty("item_code") String itemCode,
      @JsonProperty("box_index") Integer boxIndex,
      @JsonProperty("x_mm") BigDecimal xMm,
      @JsonProperty("y_mm") BigDecimal yMm,
      @JsonProperty("z_mm") BigDecimal zMm,
      @JsonProperty("length_mm") BigDecimal lengthMm,
      @JsonProperty("width_mm") BigDecimal widthMm,
      @JsonProperty("height_mm") BigDecimal heightMm,
      String orientation) {

    private LoadPlanningAgentPlacementResponse toPlatformResponse() {
      return new LoadPlanningAgentPlacementResponse(
          itemCode, boxIndex, xMm, yMm, zMm, lengthMm, widthMm, heightMm, orientation);
    }
  }

  private static <T, R> List<R> mapList(List<T> values, java.util.function.Function<T, R> mapper) {
    return values == null ? List.of() : values.stream().map(mapper).toList();
  }

  private static List<String> safeStrings(List<String> values) {
    return values == null ? List.of() : List.copyOf(values);
  }
}
