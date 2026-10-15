package com.customsaffairs.platform.link;

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

/**
 * Vue 访问 LINK 订舱业务的统一 Java BFF 入口。
 *
 * <p>控制器不连接 LINK 数据库，也不保存 Python 数据，只负责请求校验、调用代理服务和包装
 * 平台统一响应。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/link-booking/applications")
public class LinkBookingApplicationController {

    private final LinkBookingApplicationService linkBookingApplicationService;

    /**
     * @param linkBookingApplicationService 发现并调用 Python LINK 的代理服务。
     */
    public LinkBookingApplicationController(
            LinkBookingApplicationService linkBookingApplicationService) {
        this.linkBookingApplicationService = linkBookingApplicationService;
    }

    /** 查询全部 LINK 订舱申请。 */
    @GetMapping
    public ApiResponse<List<LinkBookingApplicationResponse>> listApplications() {
        return ApiResponse.success(linkBookingApplicationService.listApplications());
    }

    /**
     * 查询一份申请的详情和操作流水。
     *
     * <p>详情页始终向 Python 读取权威历史，不根据列表中可能已过期的行数据推导操作过程。</p>
     */
    @GetMapping("/{applicationNumber}/detail")
    public ApiResponse<LinkBookingApplicationDetailResponse> getApplicationDetail(
            @PathVariable String applicationNumber) {
        return ApiResponse.success(
                linkBookingApplicationService.getApplicationDetail(applicationNumber));
    }

    /**
     * 创建一份 LINK 订舱草稿。
     *
     * <p>{@code @Valid} 会先执行 Java 请求对象上的必填、长度和 DEMO 编号校验。校验通过后
     * 才会进入 Service，因此明显错误的数据不会继续占用 Nacos 和 Python 调用。</p>
     */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<LinkBookingApplicationResponse> createApplication(
            @Valid @RequestBody CreateLinkBookingApplicationRequest request) {
        return ApiResponse.success(
                linkBookingApplicationService.createApplication(request));
    }

    /**
     * 提交或取消一份已有的 LINK 申请。
     *
     * <p>HTTP 路径仍使用申请编号，JSON 使用 Java 平台的 camelCase。Controller 不在此处
     * 复制状态机；Python LINK 会拒绝浏览器直接请求 CONFIRMED / REJECTED，最终确认或拒绝
     * 只能来自模拟承运人回调。</p>
     */
    @PostMapping("/{applicationNumber}/status")
    public ApiResponse<LinkBookingApplicationResponse> changeApplicationStatus(
            @PathVariable String applicationNumber,
            @Valid @RequestBody ChangeLinkBookingApplicationStatusRequest request) {
        return ApiResponse.success(
                linkBookingApplicationService.changeApplicationStatus(applicationNumber, request));
    }

    /** 对已经进入 DEAD 的 LINK→承运人提交任务发起一次人工重试。 */
    @PostMapping("/{applicationNumber}/carrier-submission/retry")
    public ApiResponse<LinkCarrierSubmissionResponse> retryCarrierSubmission(
            @PathVariable String applicationNumber) {
        return ApiResponse.success(
                linkBookingApplicationService.retryCarrierSubmission(applicationNumber));
    }
}
