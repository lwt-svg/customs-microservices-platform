package com.customsaffairs.platform.shipmentcaseagent;

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
 * Shipment Coordinator Agent 的平台 HTTP 入口。
 *
 * <p>案件维度的启动、当前运行和历史查询仍放在 {@code shipment-cases} 资源下；拿到
 * {@code runId} 后的读取、恢复和核对则使用稳定的 {@code shipment-agent-runs} 资源。
 * Controller 只校验请求形状并包装平台统一响应，不在这里推进任何工作流节点。</p>
 */
@RestController
@RequestMapping("/api/v1/platform")
public class ShipmentAgentRunController {

    private final ShipmentAgentRunService shipmentAgentRunService;

    public ShipmentAgentRunController(
            ShipmentAgentRunService shipmentAgentRunService) {
        this.shipmentAgentRunService = shipmentAgentRunService;
    }

    /** 从案件当前 revision 启动一条幂等的总编排运行。 */
    @PostMapping("/shipment-cases/{caseNumber}/agent-runs")
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<ShipmentAgentRunResponse> startRun(
            @PathVariable String caseNumber,
            @Valid @RequestBody StartShipmentAgentRunRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(shipmentAgentRunService.startRun(
                caseNumber, request, idempotencyKey));
    }

    /** 查询案件当前仍可继续的一条运行。 */
    @GetMapping("/shipment-cases/{caseNumber}/agent-runs/active")
    public ApiResponse<ShipmentAgentRunResponse> getActiveRun(
            @PathVariable String caseNumber) {
        return ApiResponse.success(
                shipmentAgentRunService.getActiveRun(caseNumber));
    }

    /** 查询一份案件的完整 Agent 运行历史。 */
    @GetMapping("/shipment-cases/{caseNumber}/agent-runs/history")
    public ApiResponse<List<ShipmentAgentRunResponse>> listRunHistory(
            @PathVariable String caseNumber) {
        return ApiResponse.success(
                shipmentAgentRunService.listRunHistory(caseNumber));
    }

    /** 查询全局运行列表，供 Agent 运行中心实时展示。 */
    @GetMapping("/shipment-agent-runs")
    public ApiResponse<List<ShipmentAgentRunResponse>> listRuns() {
        return ApiResponse.success(shipmentAgentRunService.listRuns());
    }

    /** 查询一条运行的最新状态、人工待办和 Trace。 */
    @GetMapping("/shipment-agent-runs/{runId}")
    public ApiResponse<ShipmentAgentRunResponse> getRun(
            @PathVariable String runId) {
        return ApiResponse.success(shipmentAgentRunService.getRun(runId));
    }

    /** 提交人工中断决定并恢复同一条持久化运行。 */
    @PostMapping("/shipment-agent-runs/{runId}/resume")
    public ApiResponse<ShipmentAgentRunResponse> resumeRun(
            @PathVariable String runId,
            @Valid @RequestBody ResumeShipmentAgentRunRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(shipmentAgentRunService.resumeRun(
                runId, request, idempotencyKey));
    }

    /** 幂等地重新核对一次外部资源状态。 */
    @PostMapping("/shipment-agent-runs/{runId}/reconcile")
    public ApiResponse<ShipmentAgentRunResponse> reconcileRun(
            @PathVariable String runId,
            @Valid @RequestBody ReconcileShipmentAgentRunRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(shipmentAgentRunService.reconcileRun(
                runId, request, idempotencyKey));
    }
}
