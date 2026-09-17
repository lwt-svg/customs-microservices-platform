package com.customsaffairs.platform.health;

import com.customsaffairs.platform.common.api.ApiResponse;
import com.customsaffairs.platform.common.api.HealthData;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 平台层自身的健康检查 API。
 *
 * <p>{@code @RestController} 表示本类的方法返回值会自动转成 JSON，而不是跳转到网页模板。</p>
 * <p>{@code @RequestMapping} 为本类所有接口设置共同前缀，便于 API 版本演进。</p>
 */
@RestController
@RequestMapping("/api/v1/platform")
public class PlatformHealthController {

    /**
     * 返回 BFF 进程可响应 HTTP 请求的最小证明。
     *
     * <p>这个接口暂时只检查 Java 应用本身；MySQL、Nacos 和 Python 服务尚未被接入，
     * 因此不能在这里伪称它们已经就绪。</p>
     *
     * @return 使用统一外壳包装的健康状态。
     */
    @GetMapping("/health")
    public ApiResponse<HealthData> health() {
        HealthData healthData = new HealthData("UP", "platform-bff");
        return ApiResponse.success(healthData);
    }
}
