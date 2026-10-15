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

/** Vue 访问 Python 智能装载服务的统一 Java BFF 入口。 */
@RestController
@RequestMapping("/api/v1/platform/load-planning/tasks")
public class LoadPlanningTaskController {

    private final LoadPlanningTaskService loadPlanningTaskService;

    public LoadPlanningTaskController(LoadPlanningTaskService loadPlanningTaskService) {
        this.loadPlanningTaskService = loadPlanningTaskService;
    }

    @GetMapping
    public ApiResponse<List<LoadPlanningTaskSummaryResponse>> listTasks() {
        return ApiResponse.success(loadPlanningTaskService.listTasks());
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<LoadPlanningTaskResponse> createTask(
            @Valid @RequestBody CreateLoadPlanningTaskRequest request) {
        return ApiResponse.success(loadPlanningTaskService.createTask(request));
    }

    @GetMapping("/{taskNumber}")
    public ApiResponse<LoadPlanningTaskResponse> getTask(@PathVariable String taskNumber) {
        return ApiResponse.success(loadPlanningTaskService.getTask(taskNumber));
    }
}
