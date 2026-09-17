package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.organization.CreateOrganizationRequest;
import com.customsaffairs.platform.organization.OrganizationController;
import com.customsaffairs.platform.organization.OrganizationResponse;
import com.customsaffairs.platform.organization.OrganizationService;
import com.customsaffairs.platform.organization.OrganizationStatus;
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
 * 组织 Controller 的 HTTP 层测试。
 *
 * <p>这里手写一个构造 Service，专门验证 URL、JSON 与状态码；不会连接真实 MySQL。</p>
 */
@WebMvcTest(OrganizationController.class)
@Import(OrganizationControllerTest.FakeOrganizationServiceConfiguration.class)
class OrganizationControllerTest {

    /** 不启动真实 8080 端口的 HTTP 请求模拟器。 */
    @Autowired
    private MockMvc mockMvc;

    /** 验证 POST 请求能得到 201 和统一 JSON 外壳。 */
    @Test
    void shouldCreateOrganization() throws Exception {
        mockMvc.perform(post("/api/v1/platform/organizations")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "organizationCode": "CUSTOMS",
                                  "organizationName": "关务部"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.id").value(1))
                .andExpect(jsonPath("$.data.organizationCode").value("CUSTOMS"))
                .andExpect(jsonPath("$.data.organizationName").value("关务部"))
                .andExpect(jsonPath("$.data.status").value("ACTIVE"));
    }

    /** 验证 GET 列表接口返回统一 JSON 外壳与组织列表。 */
    @Test
    void shouldReturnOrganizationList() throws Exception {
        mockMvc.perform(get("/api/v1/platform/organizations"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data[0].organizationCode").value("CUSTOMS"));
    }

    /**
     * 仅供 Web 层测试使用的构造 Service。
     *
     * <p>Controller 的职责是 HTTP 输入输出，不应要求真实数据库在线才能测试。</p>
     */
    @TestConfiguration
    static class FakeOrganizationServiceConfiguration {

        /** 创建返回固定构造数据的 Service，避免测试依赖 MySQL。 */
        @Bean
        OrganizationService organizationService() {
            OrganizationResponse demoOrganization = new OrganizationResponse(
                    1L,
                    "CUSTOMS",
                    "关务部",
                    OrganizationStatus.ACTIVE,
                    LocalDateTime.of(2026, 1, 1, 0, 0),
                    LocalDateTime.of(2026, 1, 1, 0, 0));

            return new OrganizationService(null) {
                @Override
                public OrganizationResponse create(CreateOrganizationRequest request) {
                    return demoOrganization;
                }

                @Override
                public List<OrganizationResponse> findAll() {
                    return List.of(demoOrganization);
                }
            };
        }
    }
}
