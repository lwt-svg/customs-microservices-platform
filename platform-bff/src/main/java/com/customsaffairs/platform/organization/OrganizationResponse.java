package com.customsaffairs.platform.organization;

import java.time.LocalDateTime;

/**
 * Java BFF 向 Vue 或 curl 返回的组织 JSON 数据。
 *
 * <p>单独定义 Response 的好处是：数据库实体将来即使增加内部字段，也不会自动出现在 API 中。</p>
 *
 * @param id 组织主键。
 * @param organizationCode 组织编码。
 * @param organizationName 组织显示名称。
 * @param status 组织状态。
 * @param createdAt 创建时间。
 * @param updatedAt 最后更新时间。
 */
public record OrganizationResponse(
        Long id,
        String organizationCode,
        String organizationName,
        OrganizationStatus status,
        LocalDateTime createdAt,
        LocalDateTime updatedAt) {

    /**
     * 将仅供数据库使用的实体对象转换为安全的 API 响应对象。
     *
     * @param organization 从数据库查询或刚保存的组织实体。
     * @return 对外返回的组织数据。
     */
    public static OrganizationResponse from(Organization organization) {
        return new OrganizationResponse(
                organization.getId(),
                organization.getOrganizationCode(),
                organization.getOrganizationName(),
                organization.getStatus(),
                organization.getCreatedAt(),
                organization.getUpdatedAt());
    }
}
