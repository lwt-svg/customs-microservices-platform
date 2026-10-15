package com.customsaffairs.platform.loadplanning;

import com.customsaffairs.platform.common.api.ApiResponse;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * 状态化智能装托运行的 Java BFF 入口。
 *
 * <p>写接口都返回最新运行快照，查询接口复用同一响应结构，因此 Vue 无需分别维护“启动响应”“恢复响应”和“审批响应”三套状态模型。
 */
@RestController
@RequestMapping("/api/v1/platform/load-planning")
public class LoadPlanningAgentRunController {

  private final LoadPlanningAgentRunService agentRunService;

  public LoadPlanningAgentRunController(LoadPlanningAgentRunService agentRunService) {
    this.agentRunService = agentRunService;
  }

  @PostMapping("/tasks/{taskNumber}/agent-runs")
  @ResponseStatus(HttpStatus.CREATED)
  public ApiResponse<LoadPlanningAgentRunResponse> createRun(
      @PathVariable String taskNumber,
      @Valid @RequestBody CreateLoadPlanningAgentRunRequest request,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
    // 旧客户端没有这个请求头时继续走旧重载；新客户端带 key 时，BFF 才把幂等语义
    // 穿透到 Python。这样升级期间不会因为 Java 测试或旧脚本缺少 header 而被迫重写。
    LoadPlanningAgentRunResponse response =
        idempotencyKey == null
            ? agentRunService.createRun(taskNumber, request)
            : agentRunService.createRun(taskNumber, request, idempotencyKey);
    return ApiResponse.success(response);
  }

  @GetMapping("/agent-runs/{runId}")
  public ApiResponse<LoadPlanningAgentRunResponse> getRun(@PathVariable String runId) {
    return ApiResponse.success(agentRunService.getRun(runId));
  }

  @GetMapping("/tasks/{taskNumber}/agent-runs")
  public ApiResponse<List<LoadPlanningAgentRunResponse>> listActiveRuns(
      @PathVariable String taskNumber) {
    return ApiResponse.success(agentRunService.listActiveRuns(taskNumber));
  }

  @GetMapping("/tasks/{taskNumber}/agent-runs/history")
  public ApiResponse<List<LoadPlanningAgentRunResponse>> listRunHistory(
      @PathVariable String taskNumber) {
    return ApiResponse.success(agentRunService.listRunHistory(taskNumber));
  }

  @PostMapping("/agent-runs/{runId}/resume")
  public ApiResponse<LoadPlanningAgentRunResponse> resumeRun(
      @PathVariable String runId, @Valid @RequestBody ResumeLoadPlanningAgentRunRequest request) {
    return ApiResponse.success(agentRunService.resumeRun(runId, request));
  }

  @PostMapping("/agent-runs/{runId}/decision")
  public ApiResponse<LoadPlanningAgentRunResponse> decideRun(
      @PathVariable String runId, @Valid @RequestBody DecideLoadPlanningAgentRunRequest request) {
    return ApiResponse.success(agentRunService.decideRun(runId, request));
  }

  @PostMapping("/agent-runs/{runId}/replan")
  public ApiResponse<LoadPlanningAgentRunResponse> replanRun(
      @PathVariable String runId,
      @Valid @RequestBody ReplanLoadPlanningAgentRunRequest request,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
    LoadPlanningAgentRunResponse response =
        idempotencyKey == null
            ? agentRunService.replanRun(runId, request)
            : agentRunService.replanRun(runId, request, idempotencyKey);
    return ApiResponse.success(response);
  }

  /**
   * 为运行中已经生成的候选方案生成一份辅助解读。
   *
   * <p>这是独立的只读辅助动作：它不会推进运行状态，也不会修改批准、驳回或正式发布结果。
   */
  @PostMapping("/agent-runs/{runId}/plan-insights")
  public ApiResponse<LoadPlanningPlanInsightResponse> generatePlanInsight(
      @PathVariable String runId,
      @Valid @RequestBody GenerateLoadPlanningPlanInsightRequest request) {
    return ApiResponse.success(agentRunService.generatePlanInsight(runId, request));
  }
}
