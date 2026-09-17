package com.customsaffairs.platform.authorization;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 创建平台权限时接收的 JSON。 */
public record CreatePlatformPermissionRequest(
        @NotBlank(message = "权限编码不能为空。")
        @Size(max = 128, message = "权限编码最多 128 个字符。")
        String permissionCode,

        @NotBlank(message = "权限名称不能为空。")
        @Size(max = 128, message = "权限名称最多 128 个字符。")
        String permissionName) {
}
