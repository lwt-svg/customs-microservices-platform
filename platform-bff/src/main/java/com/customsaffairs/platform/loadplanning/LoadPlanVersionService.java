package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
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
 * 正式装载方案版本的 Java BFF 适配层。
 *
 * <p>AgentRun 是可暂停、可重规划的“试算过程”；LoadPlanVersion 是一次正式发布形成的不可变
 * 业务事实。这里刻意使用独立 Service，避免以后导出、现场执行和版本历史反过来污染智能体
 * 状态机。
 */
@Service
public class LoadPlanVersionService {

  private static final String SERVICE_ID = "load-planning-service";

  private final DiscoveryClient discoveryClient;
  private final RestClient restClient;

  public LoadPlanVersionService(
      DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
    this.discoveryClient = discoveryClient;
    this.restClient = restClientBuilder.build();
  }

  /** 将已批准运行锁定为 V1/V2；Python 负责行锁、版本校验和快照哈希。 */
  public LoadPlanVersionResponse lock(
      String runId, LockLoadPlanVersionRequest request) {
    String normalizedRunId = requiredPathValue(runId, "智能体运行编号不能为空。");
    URI uri =
        buildUri(
            "api",
            "v1",
            "load-planning",
            "agent-runs",
            normalizedRunId,
            "plan-versions");
    try {
      RemoteLoadPlanVersion response =
          restClient
              .post()
              .uri(uri)
              .body(RemoteLockRequest.from(request))
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteLoadPlanVersion.class);
      return requireBody(response, "锁定正式装载方案");
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python 锁定正式装载方案失败。", exception);
    }
  }

  /** 按版本号倒序读取任务的完整正式版本链。 */
  public List<LoadPlanVersionResponse> list(String taskNumber) {
    String normalizedTaskNumber = requiredPathValue(taskNumber, "装载任务编号不能为空。");
    URI uri =
        buildUri(
            "api",
            "v1",
            "load-planning",
            "tasks",
            normalizedTaskNumber,
            "plan-versions");
    try {
      RemoteLoadPlanVersion[] response =
          restClient
              .get()
              .uri(uri)
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteLoadPlanVersion[].class);
      if (response == null) {
        return List.of();
      }
      return Arrays.stream(response).map(RemoteLoadPlanVersion::toPlatformResponse).toList();
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python 查询正式装载方案版本失败。", exception);
    }
  }

  /** 读取任务当前生效的最高版本；尚未发布时 Python 返回 404。 */
  public LoadPlanVersionResponse current(String taskNumber) {
    String normalizedTaskNumber = requiredPathValue(taskNumber, "装载任务编号不能为空。");
    URI uri =
        buildUri(
            "api",
            "v1",
            "load-planning",
            "tasks",
            normalizedTaskNumber,
            "plan-versions",
            "current");
    return getOne(uri, "读取当前正式装载方案");
  }

  /** 按不可变版本 ID 读取审计快照。 */
  public LoadPlanVersionResponse get(String versionId) {
    String normalizedVersionId = requiredPathValue(versionId, "正式方案版本编号不能为空。");
    URI uri =
        buildUri("api", "v1", "load-planning", "plan-versions", normalizedVersionId);
    return getOne(uri, "读取正式装载方案版本");
  }

  private LoadPlanVersionResponse getOne(URI uri, String operation) {
    try {
      RemoteLoadPlanVersion response =
          restClient
              .get()
              .uri(uri)
              .retrieve()
              .onStatus(HttpStatusCode::isError, this::handleRemoteError)
              .body(RemoteLoadPlanVersion.class);
      return requireBody(response, operation);
    } catch (ResponseStatusException exception) {
      throw exception;
    } catch (RestClientException exception) {
      throw badGateway("调用 Python " + operation + "失败。", exception);
    }
  }

  private LoadPlanVersionResponse requireBody(
      RemoteLoadPlanVersion response, String operation) {
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
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 load-planning-service 实例。"));
    UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
    for (String pathSegment : pathSegments) {
      builder.pathSegment(pathSegment);
    }
    return builder.build().encode().toUri();
  }

  /**
   * 只保留浏览器需要区分的业务状态，不把 Python 内部错误正文直接穿透。
   *
   * <p>403 为权限或服务器身份上下文不足；404 为运行、任务或版本不存在；409 为运行/任务
   * 版本过期或状态冲突；422 为锁定参数不满足接口约束。
   */
  private void handleRemoteError(
      org.springframework.http.HttpRequest ignoredRequest, ClientHttpResponse response)
      throws IOException {
    HttpStatus targetStatus =
        switch (response.getStatusCode().value()) {
          case 403 -> HttpStatus.FORBIDDEN;
          case 404 -> HttpStatus.NOT_FOUND;
          case 409 -> HttpStatus.CONFLICT;
          case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
          default -> HttpStatus.BAD_GATEWAY;
        };
    String reason =
        switch (targetStatus) {
          case FORBIDDEN -> "当前服务器身份没有锁定正式装载方案的权限。";
          case NOT_FOUND -> "装载任务、智能体运行或正式方案版本不存在。";
          case CONFLICT -> "运行或正式方案版本已经变化，请刷新后重新确认。";
          case UNPROCESSABLE_ENTITY -> "正式方案锁定请求不符合 Python 业务接口要求。";
          default -> "Python 装载规划服务暂时无法处理正式方案请求。";
        };
    throw new ResponseStatusException(targetStatus, reason);
  }

  private static String requiredPathValue(String value, String message) {
    if (value == null || value.trim().isEmpty()) {
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }
    return value.trim();
  }

  private static ResponseStatusException badGateway(String reason) {
    return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason);
  }

  private static ResponseStatusException badGateway(String reason, Throwable cause) {
    return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason, cause);
  }

  /** Java camelCase 请求在远程边界显式转换为 Python snake_case。 */
  private record RemoteLockRequest(
      @JsonProperty("expected_run_version") Integer expectedRunVersion,
      @JsonProperty("expected_current_version_number") Integer expectedCurrentVersionNumber,
      @JsonProperty("lock_reason") String lockReason) {

    private static RemoteLockRequest from(LockLoadPlanVersionRequest request) {
      return new RemoteLockRequest(
          request.expectedRunVersion(),
          request.expectedCurrentVersionNumber(),
          request.lockReason().trim());
    }
  }

  /** Python 返回的正式版本快照，只在 BFF 远程边界保留 snake_case。 */
  private record RemoteLoadPlanVersion(
      @JsonProperty("version_id") String versionId,
      String status,
      @JsonProperty("task_number") String taskNumber,
      @JsonProperty("version_number") Integer versionNumber,
      @JsonProperty("source_run_id") String sourceRunId,
      @JsonProperty("source_plan_id") String sourcePlanId,
      @JsonProperty("plan_code") String planCode,
      @JsonProperty("selected_pallet_code") String selectedPalletCode,
      @JsonProperty("supersedes_version_id") String supersedesVersionId,
      @JsonProperty("source_run_version") Integer sourceRunVersion,
      @JsonProperty("snapshot_schema_version") String snapshotSchemaVersion,
      @JsonProperty("input_snapshot_hash") String inputSnapshotHash,
      @JsonProperty("constraint_snapshot_hash") String constraintSnapshotHash,
      @JsonProperty("plan_snapshot_hash") String planSnapshotHash,
      @JsonProperty("algorithm_version") String algorithmVersion,
      @JsonProperty("score_version") String scoreVersion,
      @JsonProperty("approval_actor_subject") String approvalActorSubject,
      @JsonProperty("approval_actor_display_name") String approvalActorDisplayName,
      @JsonProperty("approval_actor_source") String approvalActorSource,
      @JsonProperty("approved_at") String approvedAt,
      @JsonProperty("approval_comment") String approvalComment,
      @JsonProperty("locked_by_subject") String lockedBySubject,
      @JsonProperty("locked_by_display_name") String lockedByDisplayName,
      @JsonProperty("locked_by_source") String lockedBySource,
      @JsonProperty("lock_reason") String lockReason,
      @JsonProperty("locked_at") String lockedAt,
      @JsonProperty("input_snapshot") JsonNode inputSnapshot,
      @JsonProperty("constraint_snapshot") JsonNode constraintSnapshot,
      @JsonProperty("plan_snapshot") JsonNode planSnapshot,
      List<RemoteLoadPlanVersionEvent> events) {

    private LoadPlanVersionResponse toPlatformResponse() {
      return new LoadPlanVersionResponse(
          versionId,
          status,
          taskNumber,
          versionNumber,
          sourceRunId,
          sourcePlanId,
          planCode,
          selectedPalletCode,
          supersedesVersionId,
          sourceRunVersion,
          snapshotSchemaVersion,
          inputSnapshotHash,
          constraintSnapshotHash,
          planSnapshotHash,
          algorithmVersion,
          scoreVersion,
          approvalActorSubject,
          approvalActorDisplayName,
          approvalActorSource,
          approvedAt,
          approvalComment,
          lockedBySubject,
          lockedByDisplayName,
          lockedBySource,
          lockReason,
          lockedAt,
          inputSnapshot,
          constraintSnapshot,
          planSnapshot,
          events == null
              ? List.of()
              : events.stream().map(RemoteLoadPlanVersionEvent::toPlatformResponse).toList());
    }
  }

  private record RemoteLoadPlanVersionEvent(
      Integer sequence,
      @JsonProperty("event_type") String eventType,
      @JsonProperty("actor_subject") String actorSubject,
      @JsonProperty("actor_display_name") String actorDisplayName,
      @JsonProperty("actor_source") String actorSource,
      @JsonProperty("request_id") String requestId,
      String detail,
      @JsonProperty("occurred_at") String occurredAt) {

    private LoadPlanVersionEventResponse toPlatformResponse() {
      return new LoadPlanVersionEventResponse(
          sequence,
          eventType,
          actorSubject,
          actorDisplayName,
          actorSource,
          requestId,
          detail,
          occurredAt);
    }
  }
}
