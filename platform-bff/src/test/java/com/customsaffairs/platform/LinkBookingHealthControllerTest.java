package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.link.LinkBookingHealthController;
import com.customsaffairs.platform.link.LinkBookingHealthData;
import com.customsaffairs.platform.link.LinkBookingHealthService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.web.client.RestClient;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Java BFF 转发 Python 健康检查接口的 Web 层测试。
 *
 * <p>本测试提供一个手写的假业务服务，因此不会调用真实 Nacos、Python、MySQL 或企业环境。</p>
 */
@WebMvcTest(LinkBookingHealthController.class)
@Import(LinkBookingHealthControllerTest.FakeLinkBookingHealthConfiguration.class)
class LinkBookingHealthControllerTest {

    /** Spring 提供的 HTTP 请求模拟器，不会实际占用 8080 端口。 */
    @Autowired
    private MockMvc mockMvc;

    /** 验证 BFF 用统一 JSON 外壳返回 Python 的健康状态。 */
    @Test
    void shouldReturnPythonHealthThroughStandardBffResponse() throws Exception {
        mockMvc.perform(get("/api/v1/platform/link-booking/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.message").value("success"))
                .andExpect(jsonPath("$.data.status").value("ok"))
                .andExpect(jsonPath("$.data.service").value("link-booking-service"))
                .andExpect(jsonPath("$.data.environment").value("local"));
    }

    /**
     * 仅供本测试使用的 Spring 配置。
     *
     * <p>它返回固定的构造健康状态，以验证控制器 JSON；不执行任何网络请求。</p>
     */
    @TestConfiguration
    static class FakeLinkBookingHealthConfiguration {

        /** 将构造业务服务交给 Spring，供被测控制器注入。 */
        @Bean
        LinkBookingHealthService linkBookingHealthService() {
            return new LinkBookingHealthService(null, RestClient.builder()) {
                @Override
                public LinkBookingHealthData getLiveHealth() {
                    return new LinkBookingHealthData("ok", "link-booking-service", "local");
                }
            };
        }
    }
}
