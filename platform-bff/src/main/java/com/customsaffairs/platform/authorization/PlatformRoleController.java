package com.customsaffairs.platform.authorization;

import com.customsaffairs.platform.common.api.ApiResponse;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 平台角色以及角色权限关系的 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/platform/roles")
public class PlatformRoleController {

    private final PlatformRoleService roleService;

    public PlatformRoleController(PlatformRoleService roleService) {
        this.roleService = roleService;
    }

    /** 创建角色。 */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<PlatformRoleResponse> create(
            @Valid @RequestBody CreatePlatformRoleRequest request) {
        return ApiResponse.success(roleService.create(request));
    }

    /** 查询全部角色。 */
    @GetMapping
    public ApiResponse<List<PlatformRoleResponse>> findAll() {
        return ApiResponse.success(roleService.findAll());
    }

    /** 根据主键查询角色。 */
    @GetMapping("/{roleId}")
    public ApiResponse<PlatformRoleResponse> findById(@PathVariable Long roleId) {
        return ApiResponse.success(roleService.findById(roleId));
    }

    /** 给角色分配权限。 */
    @PostMapping("/{roleId}/permissions/{permissionId}")
    public ApiResponse<List<PlatformPermissionResponse>> assignPermission(
            @PathVariable Long roleId,
            @PathVariable Long permissionId) {
        return ApiResponse.success(roleService.assignPermission(roleId, permissionId));
    }

    /** 查询角色拥有的权限。 */
    @GetMapping("/{roleId}/permissions")
    public ApiResponse<List<PlatformPermissionResponse>> findPermissions(
            @PathVariable Long roleId) {
        return ApiResponse.success(roleService.findPermissions(roleId));
    }
}
