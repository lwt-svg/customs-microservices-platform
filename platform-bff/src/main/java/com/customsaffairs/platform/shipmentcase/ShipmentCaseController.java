package com.customsaffairs.platform.shipmentcase;

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
 * Vue 访问 Shipment Case 的统一 Java 平台入口。
 *
 * <p>Controller 只做 Java 请求校验、调用薄 Service 和包装统一响应。所有 POST 都必须接收
 * 浏览器生成的 {@code Idempotency-Key}，并交给 Service 原样转发；案件写入、revision
 * 递增和审计事件均发生在 Python。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/shipment-cases")
public class ShipmentCaseController {

    private final ShipmentCaseService shipmentCaseService;

    public ShipmentCaseController(ShipmentCaseService shipmentCaseService) {
        this.shipmentCaseService = shipmentCaseService;
    }

    /** 查询案件中心列表。 */
    @GetMapping
    public ApiResponse<List<ShipmentCaseResponse>> listCases() {
        return ApiResponse.success(shipmentCaseService.listCases());
    }

    /** 查询一份案件的当前快照、资源引用和完整事件时间线。 */
    @GetMapping("/{caseNumber}")
    public ApiResponse<ShipmentCaseResponse> getCase(@PathVariable String caseNumber) {
        return ApiResponse.success(shipmentCaseService.getCase(caseNumber));
    }

    /** 创建案件；成功时沿用 Python 的创建语义返回 HTTP 201。 */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<ShipmentCaseResponse> createCase(
            @Valid @RequestBody CreateShipmentCaseRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(
                shipmentCaseService.createCase(request, idempotencyKey));
    }

    /** 绑定一个经过 Python 验证的装载任务或 LINK 订舱申请。 */
    @PostMapping("/{caseNumber}/resources")
    public ApiResponse<ShipmentCaseResponse> bindResource(
            @PathVariable String caseNumber,
            @Valid @RequestBody BindShipmentCaseResourceRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(
                shipmentCaseService.bindResource(caseNumber, request, idempotencyKey));
    }

    /** 由 Python 状态机推进案件状态，成功后返回最新 revision。 */
    @PostMapping("/{caseNumber}/status")
    public ApiResponse<ShipmentCaseResponse> changeStatus(
            @PathVariable String caseNumber,
            @Valid @RequestBody ChangeShipmentCaseStatusRequest request,
            @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiResponse.success(
                shipmentCaseService.changeStatus(caseNumber, request, idempotencyKey));
    }
}
