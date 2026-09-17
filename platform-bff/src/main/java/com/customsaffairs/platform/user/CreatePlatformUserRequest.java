package com.customsaffairs.platform.user;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * 创建平台用户接口接收的 JSON。
 *
 * @param username 稳定账号标识，不能为空。
 * @param displayName 页面显示名称，不能为空。
 * @param organizationId 用户所属组织的数据库主键。
 */
public record CreatePlatformUserRequest(
        @NotBlank(message = "用户名不能为空。")
        @Size(max = 64, message = "用户名不能超过 64 个字符。")
        String username,

        @NotBlank(message = "用户显示名称不能为空。")
        @Size(max = 128, message = "用户显示名称不能超过 128 个字符。")
        String displayName,

        @NotNull(message = "所属组织不能为空。")
        @Positive(message = "所属组织主键必须是正整数。")
        Long organizationId) {
}
