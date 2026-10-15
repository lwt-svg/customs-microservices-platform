package com.customsaffairs.platform.shipmentcase;

/**
 * 发运案件的最小生命周期。
 *
 * <p>Java BFF 只用这个枚举约束 Vue 的输入、承接 Python 的输出，不在本地复制状态机。
 * DRAFT 能否变成 ACTIVE、终态能否再次流转等业务规则仍由 Python Agent Runtime 判断。</p>
 */
public enum ShipmentCaseStatus {
    DRAFT,
    ACTIVE,
    COMPLETED,
    CANCELLED
}
