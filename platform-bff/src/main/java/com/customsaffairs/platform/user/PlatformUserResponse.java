package com.customsaffairs.platform.user;

import java.time.LocalDateTime;

/**
 * Java BFF 对外返回的平台用户数据。
 *
 * <p>响应只返回所属组织的必要摘要，不直接序列化整个 Organization 实体，避免循环引用和内部字段泄露。</p>
 *
 * @param id 用户主键。
 * @param username 稳定账号标识。
 * @param displayName 页面显示名称。
 * @param organizationId 所属组织主键。
 * @param organizationCode 所属组织编码。
 * @param organizationName 所属组织名称。
 * @param status 用户状态。
 * @param createdAt 创建时间。
 * @param updatedAt 最后更新时间。
 */
public record PlatformUserResponse(
        Long id,
        String username,
        String displayName,
        Long organizationId,
        String organizationCode,
        String organizationName,
        PlatformUserStatus status,
        LocalDateTime createdAt,
        LocalDateTime updatedAt) {

    /** 将数据库实体转换为安全、稳定的 API 响应类型。 */
    public static PlatformUserResponse from(PlatformUser user) {
        return new PlatformUserResponse(
                user.getId(),
                user.getUsername(),
                user.getDisplayName(),
                user.getOrganization().getId(),
                user.getOrganization().getOrganizationCode(),
                user.getOrganization().getOrganizationName(),
                user.getStatus(),
                user.getCreatedAt(),
                user.getUpdatedAt());
    }
}
