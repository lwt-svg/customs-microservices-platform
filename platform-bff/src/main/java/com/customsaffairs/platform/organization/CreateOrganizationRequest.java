package com.customsaffairs.platform.organization;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 创建组织接口接收的 JSON 数据。
 *
 * <p>record 自动提供构造方法和字段读取方法。它只描述 API 输入，不能直接当成数据库实体使用。</p>
 *
 * @param organizationCode 组织编码，不能为空，最多 64 个字符。
 * @param organizationName 组织名称，不能为空，最多 128 个字符。
 */
public record CreateOrganizationRequest(
        @NotBlank(message = "组织编码不能为空。")
        @Size(max = 64, message = "组织编码不能超过 64 个字符。")
        String organizationCode,

        @NotBlank(message = "组织名称不能为空。")
        @Size(max = 128, message = "组织名称不能超过 128 个字符。")
        String organizationName) {
}
