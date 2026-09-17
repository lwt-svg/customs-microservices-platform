package com.customsaffairs.platform.link;

import java.net.URI;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Java BFF 调用 Python LINK 健康检查的业务服务。
 *
 * <p>这里严格分成两步：先通过 DiscoveryClient 从 Nacos 找到服务实例，再向返回的地址
 * 发送 HTTP 请求。代码中不写死 Python 的主机或端口。</p>
 */
@Service
public class LinkBookingHealthService {

    /** Nacos 和 Python 启动配置中共同使用的稳定服务名。 */
    private static final String LINK_BOOKING_SERVICE_ID = "link-booking-service";

    /** 用于向 Nacos 查询实例地址的 Spring 通用接口。 */
    private final DiscoveryClient discoveryClient;

    /** Spring 6 提供的同步 HTTP 客户端，用于从 Java 调用 Python API。 */
    private final RestClient restClient;

    /**
     * 由 Spring 注入服务发现客户端与 HTTP 客户端构建器。
     *
     * @param discoveryClient 用服务名查询 Nacos 实例列表的工具。
     * @param restClientBuilder 用于创建 HTTP 请求客户端的 Spring 构建器。
     */
    public LinkBookingHealthService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /**
     * 查找一台 Python LINK 实例，并调用它的无业务数据健康检查接口。
     *
     * @return Python 返回的健康状态。
     */
    public LinkBookingHealthData getLiveHealth() {
        ServiceInstance pythonInstance = discoveryClient.getInstances(LINK_BOOKING_SERVICE_ID).stream()
                // 第一阶段只有一台本地 Python 实例；多实例负载均衡将在后续独立小节实现。
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 link-booking-service 实例。"));

        URI healthUri = UriComponentsBuilder.newInstance()
                .scheme(pythonInstance.isSecure() ? "https" : "http")
                .host(pythonInstance.getHost())
                .port(pythonInstance.getPort())
                .path("/health/live")
                .build()
                .toUri();

        try {
            LinkBookingHealthData healthData = restClient.get()
                    .uri(healthUri)
                    .retrieve()
                    .body(LinkBookingHealthData.class);

            if (healthData == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY,
                        "Python LINK 服务返回了空的健康检查响应。");
            }
            return healthData;
        } catch (RestClientException exception) {
            // 不向浏览器暴露底层网络地址、完整异常栈或任何潜在基础设施细节。
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY,
                    "无法调用 Python LINK 服务的健康检查接口。",
                    exception);
        }
    }
}
