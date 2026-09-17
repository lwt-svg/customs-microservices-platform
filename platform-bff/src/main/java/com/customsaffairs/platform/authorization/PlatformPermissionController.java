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

/** 平台权限管理的 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/platform/permissions")
public class PlatformPermissionController {

    private final PlatformPermissionService permissionService;

    public PlatformPermissionController(PlatformPermissionService permissionService) {
        this.permissionService = permissionService;
    }

    /** 创建权限。 */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<PlatformPermissionResponse> create(
            @Valid @RequestBody CreatePlatformPermissionRequest request) {
        return ApiResponse.success(permissionService.create(request));
    }

    /** 查询全部权限。 */
    @GetMapping
    public ApiResponse<List<PlatformPermissionResponse>> findAll() {
        return ApiResponse.success(permissionService.findAll());
    }

    /** 根据主键查询权限。 */
    @GetMapping("/{permissionId}")
    public ApiResponse<PlatformPermissionResponse> findById(@PathVariable Long permissionId) {
        return ApiResponse.success(permissionService.findById(permissionId));
    }
}
