package com.customsaffairs.platform.shipmentcase;

/**
 * Shipment Case 当前能够引用的业务资源类型。
 *
 * <p>案件只保存稳定编号，不复制装载任务或 LINK 申请的业务明细。资源是否真实存在由
 * Python 在绑定命令中验证，Java BFF 不跨服务查询数据库。</p>
 */
public enum ShipmentResourceType {
    LOAD_PLANNING_TASK,
    LINK_BOOKING_APPLICATION
}
