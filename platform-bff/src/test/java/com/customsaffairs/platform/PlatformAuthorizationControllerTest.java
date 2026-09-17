package com.customsaffairs.platform;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.customsaffairs.platform.authorization.AuthorizationStatus;
import com.customsaffairs.platform.authorization.CreatePlatformPermissionRequest;
import com.customsaffairs.platform.authorization.CreatePlatformRoleRequest;
import com.customsaffairs.platform.authorization.PlatformPermissionController;
import com.customsaffairs.platform.authorization.PlatformPermissionResponse;
import com.customsaffairs.platform.authorization.PlatformPermissionService;
import com.customsaffairs.platform.authorization.PlatformRoleController;
import com.customsaffairs.platform.authorization.PlatformRoleResponse;
import com.customsaffairs.platform.authorization.PlatformRoleService;
import com.customsaffairs.platform.authorization.PlatformUserRoleController;
import com.customsaffairs.platform.authorization.PlatformUserRoleService;
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

/** 14.4 角色、权限和分配接口的 HTTP 层测试。 */
@WebMvcTest({
        PlatformRoleController.class,
        PlatformPermissionController.class,
        PlatformUserRoleController.class
})
@Import(PlatformAuthorizationControllerTest.FakeAuthorizationServiceConfiguration.class)
class PlatformAuthorizationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    /** 验证创建角色返回 201。 */
    @Test
    void shouldCreateRole() throws Exception {
        mockMvc.perform(post("/api/v1/platform/roles")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "roleCode": "SYSTEM_ADMIN",
                                  "roleName": "系统管理员"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.roleCode").value("SYSTEM_ADMIN"));
    }

    /** 验证创建权限返回 201。 */
    @Test
    void shouldCreatePermission() throws Exception {
        mockMvc.perform(post("/api/v1/platform/permissions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "permissionCode": "platform:user:read",
                                  "permissionName": "查看平台用户"
                                }
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.permissionCode").value("platform:user:read"));
    }

    /** 验证给角色分配权限后返回该权限列表。 */
    @Test
    void shouldAssignPermissionToRole() throws Exception {
        mockMvc.perform(post("/api/v1/platform/roles/1/permissions/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].permissionName").value("查看平台用户"));
    }

    /** 验证给用户分配角色后返回该角色列表。 */
    @Test
    void shouldAssignRoleToUser() throws Exception {
        mockMvc.perform(post("/api/v1/platform/users/1/roles/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].roleName").value("系统管理员"));
    }

    /** 验证角色和权限列表查询接口。 */
    @Test
    void shouldReturnRoleAndPermissionLists() throws Exception {
        mockMvc.perform(get("/api/v1/platform/roles"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].roleCode").value("SYSTEM_ADMIN"));

        mockMvc.perform(get("/api/v1/platform/permissions"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].permissionCode").value("platform:user:read"));
    }

    /** 为 Controller 测试提供固定构造数据，不连接数据库。 */
    @TestConfiguration
    static class FakeAuthorizationServiceConfiguration {

        private static final LocalDateTime DEMO_TIME = LocalDateTime.of(2026, 1, 1, 0, 0);
        private static final PlatformRoleResponse ROLE = new PlatformRoleResponse(
                1L, "SYSTEM_ADMIN", "系统管理员",
                AuthorizationStatus.ACTIVE, DEMO_TIME, DEMO_TIME);
        private static final PlatformPermissionResponse PERMISSION =
                new PlatformPermissionResponse(
                        1L, "platform:user:read", "查看平台用户",
                        AuthorizationStatus.ACTIVE, DEMO_TIME, DEMO_TIME);

        @Bean
        PlatformRoleService platformRoleService() {
            return new PlatformRoleService(null, null) {
                @Override
                public PlatformRoleResponse create(CreatePlatformRoleRequest request) {
                    return ROLE;
                }

                @Override
                public List<PlatformRoleResponse> findAll() {
                    return List.of(ROLE);
                }

                @Override
                public PlatformRoleResponse findById(Long roleId) {
                    return ROLE;
                }

                @Override
                public List<PlatformPermissionResponse> assignPermission(
                        Long roleId, Long permissionId) {
                    return List.of(PERMISSION);
                }

                @Override
                public List<PlatformPermissionResponse> findPermissions(Long roleId) {
                    return List.of(PERMISSION);
                }
            };
        }

        @Bean
        PlatformPermissionService platformPermissionService() {
            return new PlatformPermissionService(null) {
                @Override
                public PlatformPermissionResponse create(CreatePlatformPermissionRequest request) {
                    return PERMISSION;
                }

                @Override
                public List<PlatformPermissionResponse> findAll() {
                    return List.of(PERMISSION);
                }

                @Override
                public PlatformPermissionResponse findById(Long permissionId) {
                    return PERMISSION;
                }
            };
        }

        @Bean
        PlatformUserRoleService platformUserRoleService() {
            return new PlatformUserRoleService(null, null) {
                @Override
                public List<PlatformRoleResponse> assignRole(Long userId, Long roleId) {
                    return List.of(ROLE);
                }

                @Override
                public List<PlatformRoleResponse> findRoles(Long userId) {
                    return List.of(ROLE);
                }
            };
        }
    }
}
