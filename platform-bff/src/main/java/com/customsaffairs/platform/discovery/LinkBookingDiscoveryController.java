package com.customsaffairs.platform.discovery;

import com.customsaffairs.platform.common.api.ApiResponse;
import java.util.List;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Java BFF 发现 Python LINK 服务的学习接口。
 *
 * <p>本类不直接写死 {@code 127.0.0.1:8000}。它把服务名交给 Spring 的
 * {@link DiscoveryClient}，由 Nacos 返回当前可用实例列表。这就是“服务发现”。</p>
 *
 * <p>本小节只验证“Java 能找到 Python”。真正向 Python 发 HTTP 请求属于 13.5，不能提前
 * 混入本控制器。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/discovery")
public class LinkBookingDiscoveryController {

    /**
     * Spring 注入的服务发现工具。
     *
     * <p>代码只依赖 Spring 提供的通用接口，而不是直接依赖 Nacos 专有类；将来若替换注册中心，
     * 这里的业务代码通常不需要改动。</p>
     */
    private final DiscoveryClient discoveryClient;

    /**
     * 创建控制器时由 Spring 自动传入 DiscoveryClient。
     *
     * @param discoveryClient 用于按服务名查询实例列表的注册中心客户端。
     */
    public LinkBookingDiscoveryController(DiscoveryClient discoveryClient) {
        this.discoveryClient = discoveryClient;
    }

    /**
     * 查询 Nacos 中已经注册的 Python LINK 服务实例。
     *
     * @return 统一 JSON 外壳；data 是零个或多个安全的实例地址。
     */
    @GetMapping("/link-booking-service")
    public ApiResponse<List<DiscoveredServiceInstance>> discoverLinkBookingService() {
        // 这里的字符串是服务注册时使用的“服务名”，不是 Python 文件名，也不是 URL。
        List<ServiceInstance> nacosInstances = discoveryClient.getInstances("link-booking-service");

        // 将 Nacos/Spring 的原始实例对象转换为我们自己定义的安全 API 数据。
        List<DiscoveredServiceInstance> responseData = nacosInstances.stream()
                .map(instance -> new DiscoveredServiceInstance(
                        instance.getServiceId(),
                        instance.getHost(),
                        instance.getPort(),
                        instance.isSecure()))
                .toList();

        return ApiResponse.success(responseData);
    }
}
