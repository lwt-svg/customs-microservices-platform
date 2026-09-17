package com.customsaffairs.platform.authorization;

import java.time.LocalDateTime;

/** 返回给调用方的权限 JSON，不直接暴露 JPA 实体。 */
public record PlatformPermissionResponse(
        Long id,
        String permissionCode,
        String permissionName,
        AuthorizationStatus status,
        LocalDateTime createdAt,
        LocalDateTime updatedAt) {

    /** 将数据库权限实体转换为稳定的 API 响应。 */
    public static PlatformPermissionResponse from(PlatformPermission permission) {
        return new PlatformPermissionResponse(
                permission.getId(),
                permission.getPermissionCode(),
                permission.getPermissionName(),
                permission.getStatus(),
                permission.getCreatedAt(),
                permission.getUpdatedAt());
    }
}
