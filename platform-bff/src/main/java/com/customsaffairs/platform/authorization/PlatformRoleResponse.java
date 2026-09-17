package com.customsaffairs.platform.authorization;

import java.time.LocalDateTime;

/** 返回给调用方的角色 JSON，不直接暴露 JPA 实体。 */
public record PlatformRoleResponse(
        Long id,
        String roleCode,
        String roleName,
        AuthorizationStatus status,
        LocalDateTime createdAt,
        LocalDateTime updatedAt) {

    /** 将数据库角色实体转换为稳定的 API 响应。 */
    public static PlatformRoleResponse from(PlatformRole role) {
        return new PlatformRoleResponse(
                role.getId(),
                role.getRoleCode(),
                role.getRoleName(),
                role.getStatus(),
                role.getCreatedAt(),
                role.getUpdatedAt());
    }
}
