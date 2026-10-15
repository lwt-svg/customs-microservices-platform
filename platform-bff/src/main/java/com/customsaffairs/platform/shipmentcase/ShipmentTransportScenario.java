package com.customsaffairs.platform.shipmentcase;

/**
 * 发运案件第一阶段支持的运输场景。
 *
 * <p>枚举值必须与 Python {@code ShipmentTransportScenario} 保持一致。BFF 使用同名枚举
 * 可以尽早拒绝拼写错误，但不会根据场景自行决定要创建哪些下游任务。</p>
 */
public enum ShipmentTransportScenario {
    UNDECIDED,
    SEA_FCL,
    SEA_LCL,
    AIR,
    EXPRESS,
    LOAD_PLANNING_ONLY,
    BOOKING_ONLY
}
