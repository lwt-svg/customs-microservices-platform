package com.customsaffairs.platform.shipmentcaseagent;

/**
 * Shipment Agent 数据就绪门禁当前接受的事实类型。
 *
 * <p>{@code AVAILABLE_QUANTITY} 描述可用库存，而不是装载任务里的计划数量；
 * {@code MASTER_DATA_CONFIRMED} 表示业务人员或权威系统已确认主数据可用于本次发运。</p>
 */
public enum ShipmentDataFactType {
    AVAILABLE_QUANTITY,
    MASTER_DATA_CONFIRMED
}
