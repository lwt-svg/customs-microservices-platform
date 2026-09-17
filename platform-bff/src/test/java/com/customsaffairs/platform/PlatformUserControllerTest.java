package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.user.CreatePlatformUserRequest;
import com.customsaffairs.platform.user.PlatformUserController;
import com.customsaffairs.platform.user.PlatformUserResponse;
import com.customsaffairs.platform.user.PlatformUserService;
import com.customsaffairs.platform.user.PlatformUserStatus;
import java.time.LocalDateTime;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 用户 Controller 的 HTTP 层测试。
 *
 * <p>测试使用固定构造数据，只验证请求路径、JSON 和状态码，不连接真实 MySQL。</p>
 */
@WebMvcTest(PlatformUserController.class)
@Import(PlatformUserControllerTest.FakePlatformUserServiceConfiguration.class)
class PlatformUserControllerTest {

    /** Spring 提供的模拟 HTTP 工具，不占用真实 8080 端口。 */
    @Autowired
    private MockMvc mockMvc;

    /** 验证创建用户返回 201，并包含用户与所属组织摘要。 */
    @Test
    void shouldCreatePlatformUser() throws Exception {
        mockMvc.perform(post("/api/v1/platform/users")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "username": "customs_demo",
                                  "displayName": "关务演示用户",
                                  "organizationId": 1
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.username").value("customs_demo"))
                .andExpect(jsonPath("$.data.organizationId").value(1))
                .andExpect(jsonPath("$.data.organizationCode").value("CUSTOMS_DEMO"))
                .andExpect(jsonPath("$.data.status").value("ACTIVE"));
    }

    /** 验证查询列表接口返回用户数组。 */
    @Test
    void shouldReturnPlatformUserList() throws Exception {
        mockMvc.perform(get("/api/v1/platform/users"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].displayName").value("关务演示用户"));
    }

    /** 验证按主键查询单个用户。 */
    @Test
    void shouldReturnPlatformUserById() throws Exception {
        mockMvc.perform(get("/api/v1/platform/users/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(1))
                .andExpect(jsonPath("$.data.username").value("customs_demo"));
    }

    /** 仅供 Controller 测试使用的固定 Service。 */
    @TestConfiguration
    static class FakePlatformUserServiceConfiguration {

        /** 创建不会访问数据库的构造用户服务。 */
        @Bean
        PlatformUserService platformUserService() {
            PlatformUserResponse demoUser = new PlatformUserResponse(
                    1L,
                    "customs_demo",
                    "关务演示用户",
                    1L,
                    "CUSTOMS_DEMO",
                    "关务演示组织",
                    PlatformUserStatus.ACTIVE,
                    LocalDateTime.of(2026, 1, 1, 0, 0),
                    LocalDateTime.of(2026, 1, 1, 0, 0));

            return new PlatformUserService(null, null) {
                @Override
                public PlatformUserResponse create(CreatePlatformUserRequest request) {
                    return demoUser;
                }

                @Override
                public List<PlatformUserResponse> findAll() {
                    return List.of(demoUser);
                }

                @Override
                public PlatformUserResponse findById(Long userId) {
                    return demoUser;
                }
            };
        }
    }
}
