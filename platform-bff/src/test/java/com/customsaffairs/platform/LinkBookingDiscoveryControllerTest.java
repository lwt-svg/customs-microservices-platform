package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.discovery.LinkBookingDiscoveryController;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.cloud.client.DefaultServiceInstance;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Java BFF 服务发现接口的 Web 层测试。
 *
 * <p>本测试只加载 LinkBookingDiscoveryController，不启动完整 Spring Boot 应用，也不连接
 * Nacos、Python 或企业环境。下方配置类提供一个手写的假 DiscoveryClient。</p>
 */
@WebMvcTest(LinkBookingDiscoveryController.class)
@Import(LinkBookingDiscoveryControllerTest.FakeDiscoveryClientConfiguration.class)
class LinkBookingDiscoveryControllerTest {

    /** Spring 提供的 HTTP 请求模拟器，不会实际占用 8080 端口。 */
    @Autowired
    private MockMvc mockMvc;

    /** 验证 Java 能按 LINK 服务名得到一个经过安全转换的实例地址。 */
    @Test
    void shouldReturnLinkBookingInstanceFoundByServiceName() throws Exception {
        mockMvc.perform(get("/api/v1/platform/discovery/link-booking-service"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.message").value("success"))
                .andExpect(jsonPath("$.data[0].serviceId").value("link-booking-service"))
                .andExpect(jsonPath("$.data[0].host").value("127.0.0.1"))
                .andExpect(jsonPath("$.data[0].port").value(8000))
                .andExpect(jsonPath("$.data[0].secure").value(false));
    }

    /**
     * 仅供本测试使用的 Spring 配置。
     *
     * <p>它手写实现 DiscoveryClient：当控制器查询 link-booking-service 时，固定返回一台
     * 构造的 Python 实例。这样不需要 Mockito，也不会向任何真实注册中心发送请求。</p>
     */
    @TestConfiguration
    static class FakeDiscoveryClientConfiguration {

        /** 将手写的假 DiscoveryClient 交给 Spring，供被测控制器注入。 */
        @Bean
        DiscoveryClient discoveryClient() {
            return new DiscoveryClient() {
                @Override
                public String description() {
                    return "仅用于测试的构造服务发现客户端";
                }

                @Override
                public List<ServiceInstance> getInstances(String serviceId) {
                    if (!"link-booking-service".equals(serviceId)) {
                        return List.of();
                    }

                    ServiceInstance syntheticPythonInstance = new DefaultServiceInstance(
                            "synthetic-link-instance-1",
                            "link-booking-service",
                            "127.0.0.1",
                            8000,
                            false);
                    return List.of(syntheticPythonInstance);
                }

                @Override
                public List<String> getServices() {
                    return List.of("link-booking-service");
                }
            };
        }
    }
}
