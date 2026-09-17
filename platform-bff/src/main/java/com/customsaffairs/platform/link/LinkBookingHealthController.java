package com.customsaffairs.platform.link;

import com.customsaffairs.platform.common.api.ApiResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Java BFF 对外提供的 Python LINK 健康检查转发接口。
 *
 * <p>浏览器将来只调用 Java BFF，不直接依赖 Python 的地址；BFF 内部决定通过 Nacos 找到
 * 哪台 Python 实例并进行调用。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/link-booking")
public class LinkBookingHealthController {

    /** 负责“发现 Python 实例并调用健康检查”的业务服务。 */
    private final LinkBookingHealthService linkBookingHealthService;

    /**
     * @param linkBookingHealthService Spring 注入的 LINK 健康检查业务服务。
     */
    public LinkBookingHealthController(LinkBookingHealthService linkBookingHealthService) {
        this.linkBookingHealthService = linkBookingHealthService;
    }

    /**
     * 通过 Java BFF 获取 Python LINK 服务的存活状态。
     *
     * @return 使用 Java BFF 统一 JSON 外壳包装的 Python 健康状态。
     */
    @GetMapping("/health")
    public ApiResponse<LinkBookingHealthData> getLinkBookingHealth() {
        return ApiResponse.success(linkBookingHealthService.getLiveHealth());
    }
}
