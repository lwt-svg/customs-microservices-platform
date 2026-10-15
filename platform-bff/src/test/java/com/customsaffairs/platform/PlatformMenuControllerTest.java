package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.menu.CreatePlatformMenuRequest;
import com.customsaffairs.platform.menu.MenuStatus;
import com.customsaffairs.platform.menu.PlatformMenuController;
import com.customsaffairs.platform.menu.PlatformMenuResponse;
import com.customsaffairs.platform.menu.PlatformMenuService;
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

/** 14.5 菜单接口的 HTTP 层测试。 */
@WebMvcTest(PlatformMenuController.class)
@Import(PlatformMenuControllerTest.FakeMenuServiceConfiguration.class)
class PlatformMenuControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void shouldCreateAndQueryMenu() throws Exception {
        mockMvc.perform(post("/api/v1/platform/menus")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "menuCode": "platform-users",
                                  "menuName": "用户管理",
                                  "routePath": "/users",
                                  "icon": "users",
                                  "sortOrder": 20
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.menuCode").value("platform-users"));

        mockMvc.perform(get("/api/v1/platform/menus/tree"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].children[0].menuName").value("用户管理"));
    }

    @TestConfiguration
    static class FakeMenuServiceConfiguration {

        private static final LocalDateTime DEMO_TIME = LocalDateTime.of(2026, 1, 1, 0, 0);
        private static final PlatformMenuResponse CHILD = new PlatformMenuResponse(
                2L,
                "platform-users",
                "用户管理",
                "/users",
                "users",
                20,
                MenuStatus.ACTIVE,
                1L,
                "platform:user:read",
                DEMO_TIME,
                DEMO_TIME,
                List.of());
        private static final PlatformMenuResponse ROOT = new PlatformMenuResponse(
                1L,
                "platform",
                "平台管理",
                "/platform",
                "organization",
                10,
                MenuStatus.ACTIVE,
                null,
                null,
                DEMO_TIME,
                DEMO_TIME,
                List.of(CHILD));

        @Bean
        PlatformMenuService platformMenuService() {
            return new PlatformMenuService(null, null) {
                @Override
                public PlatformMenuResponse create(CreatePlatformMenuRequest request) {
                    return CHILD;
                }

                @Override
                public List<PlatformMenuResponse> findAll() {
                    return List.of(ROOT, CHILD);
                }

                @Override
                public List<PlatformMenuResponse> findTree() {
                    return List.of(ROOT);
                }

                @Override
                public PlatformMenuResponse findById(Long menuId) {
                    return menuId == 1L ? ROOT : CHILD;
                }
            };
        }
    }
}
