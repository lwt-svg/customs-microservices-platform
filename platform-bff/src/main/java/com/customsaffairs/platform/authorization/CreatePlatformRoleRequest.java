package com.customsaffairs.platform.authorization;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 创建平台角色时接收的 JSON。 */
public record CreatePlatformRoleRequest(
        @NotBlank(message = "角色编码不能为空。")
        @Size(max = 64, message = "角色编码最多 64 个字符。")
        String roleCode,

        @NotBlank(message = "角色名称不能为空。")
        @Size(max = 128, message = "角色名称最多 128 个字符。")
        String roleName) {
}
