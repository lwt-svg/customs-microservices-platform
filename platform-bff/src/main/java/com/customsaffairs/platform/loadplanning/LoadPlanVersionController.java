package com.customsaffairs.platform.loadplanning;

import com.customsaffairs.platform.common.api.ApiResponse;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 正式装载方案发布与版本历史的平台入口。 */
@RestController
@RequestMapping("/api/v1/platform/load-planning")
public class LoadPlanVersionController {

  private final LoadPlanVersionService planVersionService;

  public LoadPlanVersionController(LoadPlanVersionService planVersionService) {
    this.planVersionService = planVersionService;
  }

  @PostMapping("/agent-runs/{runId}/plan-versions")
  @ResponseStatus(HttpStatus.CREATED)
  public ApiResponse<LoadPlanVersionResponse> lock(
      @PathVariable String runId,
      @Valid @RequestBody LockLoadPlanVersionRequest request) {
    return ApiResponse.success(planVersionService.lock(runId, request));
  }

  @GetMapping("/tasks/{taskNumber}/plan-versions")
  public ApiResponse<List<LoadPlanVersionResponse>> list(
      @PathVariable String taskNumber) {
    return ApiResponse.success(planVersionService.list(taskNumber));
  }

  @GetMapping("/tasks/{taskNumber}/plan-versions/current")
  public ApiResponse<LoadPlanVersionResponse> current(
      @PathVariable String taskNumber) {
    return ApiResponse.success(planVersionService.current(taskNumber));
  }

  @GetMapping("/plan-versions/{versionId}")
  public ApiResponse<LoadPlanVersionResponse> get(@PathVariable String versionId) {
    return ApiResponse.success(planVersionService.get(versionId));
  }
}
