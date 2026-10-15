package com.customsaffairs.platform.menu;

import java.time.LocalDateTime;
import java.util.List;

/** 对外返回的菜单节点；不直接序列化 JPA 实体关系。 */
public record PlatformMenuResponse(
        Long id,
        String menuCode,
        String menuName,
        String routePath,
        String icon,
        Integer sortOrder,
        MenuStatus status,
        Long parentId,
        String permissionCode,
        LocalDateTime createdAt,
        LocalDateTime updatedAt,
        List<PlatformMenuResponse> children) {

    /** 转换为没有子节点的平面响应。 */
    public static PlatformMenuResponse from(PlatformMenu menu) {
        return from(menu, List.of());
    }

    /** 转换为带子节点的树响应。 */
    public static PlatformMenuResponse from(
            PlatformMenu menu, List<PlatformMenuResponse> children) {
        return new PlatformMenuResponse(
                menu.getId(),
                menu.getMenuCode(),
                menu.getMenuName(),
                menu.getRoutePath(),
                menu.getIcon(),
                menu.getSortOrder(),
                menu.getStatus(),
                menu.getParent() == null ? null : menu.getParent().getId(),
                menu.getPermission() == null ? null : menu.getPermission().getPermissionCode(),
                menu.getCreatedAt(),
                menu.getUpdatedAt(),
                children);
    }
}
