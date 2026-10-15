package com.customsaffairs.platform.menu;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

/** 创建平台菜单时接收的 JSON。 */
public record CreatePlatformMenuRequest(
        @NotBlank(message = "菜单编码不能为空。")
        @Size(max = 64, message = "菜单编码最多 64 个字符。")
        String menuCode,

        @NotBlank(message = "菜单名称不能为空。")
        @Size(max = 128, message = "菜单名称最多 128 个字符。")
        String menuName,

        @NotBlank(message = "菜单路由不能为空。")
        @Size(max = 255, message = "菜单路由最多 255 个字符。")
        String routePath,

        @Size(max = 64, message = "菜单图标编码最多 64 个字符。")
        String icon,

        @NotNull(message = "菜单排序值不能为空。")
        @PositiveOrZero(message = "菜单排序值不能为负数。")
        Integer sortOrder,

        Long parentId,

        Long permissionId) {
}
