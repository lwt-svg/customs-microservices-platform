package com.customsaffairs.platform.link;

/**
 * Java BFF 返回给 Vue 的 LINK 操作流水。
 *
 * <p>流水描述一次已经完成的领域动作，不是浏览器本地拼出来的“操作记录”。事件时间和
 * 参考号均以 Python LINK 的数据库结果为准。</p>
 *
 * @param id LINK 数据库中的事件主键。
 * @param eventType 稳定的领域事件类型，例如 {@code SUBMITTED}。
 * @param previousStatus 动作前状态；创建草稿时没有前一状态。
 * @param currentStatus 动作完成后的状态。
 * @param bookingReference 本次动作关联的构造订舱参考号；只有确认事件可能存在。
 * @param actorLabel 当前学习阶段固定为 {@code SYSTEM_DEMO}，不冒充真实登录用户。
 * @param occurredAt Python 数据库记录的发生时间。
 */
public record LinkBookingApplicationEventResponse(
        long id,
        String eventType,
        LinkBookingStatus previousStatus,
        LinkBookingStatus currentStatus,
        String bookingReference,
        String actorLabel,
        String occurredAt) {
}
