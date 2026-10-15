package com.customsaffairs.platform.link;

import jakarta.validation.constraints.NotNull;

/**
 * Vue 通过 Java BFF 请求变更 LINK 订舱申请状态时提交的平台请求。
 *
 * <p>Java 只校验状态字段存在且名称属于已知枚举；例如“草稿不能直接确认”这样的状态机
 * 规则仍然由 Python LINK 决定。这样平台层不会复制业务规则，后续 Python 规则改变时也
 * 不会出现两套不一致的判断。</p>
 *
 * @param targetStatus 希望迁移到的目标状态。
 */
public record ChangeLinkBookingApplicationStatusRequest(
        @NotNull(message = "目标订舱状态不能为空") LinkBookingStatus targetStatus) {
}
