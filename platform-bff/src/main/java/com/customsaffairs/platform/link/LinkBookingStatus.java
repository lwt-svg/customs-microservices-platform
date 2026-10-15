package com.customsaffairs.platform.link;

/**
 * Java BFF 与 Python LINK 共同使用的订舱申请状态。
 *
 * <p>状态是否允许转换由 Python LINK 领域服务决定。Java BFF 只负责校验状态名称并转发，
 * 不在平台层复制一份状态机规则。</p>
 */
public enum LinkBookingStatus {
    DRAFT,
    SUBMITTED,
    CONFIRMED,
    REJECTED,
    CANCELLED
}
