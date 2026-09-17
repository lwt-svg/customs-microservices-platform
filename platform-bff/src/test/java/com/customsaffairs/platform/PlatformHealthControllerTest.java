package com.customsaffairs.platform;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 平台健康检查的集成测试。
 *
 * <p>MockMvc 会在测试进程中模拟 HTTP 请求，不需要真的占用 8080 端口；
 * 因此测试快速、可重复，也不会连接 MySQL、Nacos 或企业环境。</p>
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class PlatformHealthControllerTest {

    /** Spring 注入的本地 HTTP 模拟器。 */
    @Autowired
    private MockMvc mockMvc;

    /**
     * 验证自定义健康检查的 HTTP 状态和统一响应 JSON 结构。
     *
     * @throws Exception MockMvc 在请求或断言失败时会抛出异常，JUnit 会将测试标记为失败。
     */
    @Test
    void shouldReturnPlatformHealthInStandardResponse() throws Exception {
        mockMvc.perform(get("/api/v1/platform/health").accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.message").value("success"))
                .andExpect(jsonPath("$.data.status").value("UP"))
                .andExpect(jsonPath("$.data.service").value("platform-bff"));
    }
}
