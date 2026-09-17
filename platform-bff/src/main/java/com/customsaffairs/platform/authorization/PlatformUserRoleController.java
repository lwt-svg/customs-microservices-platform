package com.customsaffairs.platform.authorization;

import com.customsaffairs.platform.common.api.ApiResponse;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 用户与角色关系的 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/platform/users/{userId}/roles")
public class PlatformUserRoleController {

    private final PlatformUserRoleService userRoleService;

    public PlatformUserRoleController(PlatformUserRoleService userRoleService) {
        this.userRoleService = userRoleService;
    }

    /** 给指定用户分配角色。 */
    @PostMapping("/{roleId}")
    public ApiResponse<List<PlatformRoleResponse>> assignRole(
            @PathVariable Long userId,
            @PathVariable Long roleId) {
        return ApiResponse.success(userRoleService.assignRole(userId, roleId));
    }

    /** 查询指定用户当前拥有的角色。 */
    @GetMapping
    public ApiResponse<List<PlatformRoleResponse>> findRoles(@PathVariable Long userId) {
        return ApiResponse.success(userRoleService.findRoles(userId));
    }
}
