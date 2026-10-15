package com.customsaffairs.platform.link;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.LocalDate;

/**
 * Vue 通过 Java BFF 创建 LINK 订舱草稿时提交的平台请求。
 *
 * <p>这里使用 camelCase，是因为浏览器只依赖 Java 平台接口。Java 调用 Python 时会把这些
 * 字段转换为 snake_case，Vue 不需要知道 Python 的内部命名习惯。</p>
 *
 * <p>当前仓库是本地学习环境，因此申请编号必须以 {@code DEMO-} 开头。这个限制用于避免
 * 学习者误把真实客户单号或企业数据写入本地数据库。</p>
 *
 * @param applicationNumber 构造申请编号，必须以 DEMO- 开头。
 * @param originPort 构造的起运港标识。
 * @param destinationPort 构造的目的港标识。
 * @param transportMode 运输方式，当前构造流程使用 SEA。
 * @param containerType 箱型，例如 20GP、40GP 或 40HQ。
 * @param requestedEtd 计划离港日期；暂时允许为空。
 */
public record CreateLinkBookingApplicationRequest(
        @NotBlank
        @Size(max = 64)
        @Pattern(
                regexp = "^DEMO-[A-Za-z0-9_-]+$",
                message = "学习环境的申请编号必须以 DEMO- 开头，且只能包含字母、数字、下划线和短横线")
        String applicationNumber,
        @NotBlank @Size(max = 64) String originPort,
        @NotBlank @Size(max = 64) String destinationPort,
        @NotBlank @Size(max = 32) String transportMode,
        @NotBlank @Size(max = 32) String containerType,
        LocalDate requestedEtd) {
}
