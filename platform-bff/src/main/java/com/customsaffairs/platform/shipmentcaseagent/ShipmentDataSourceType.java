package com.customsaffairs.platform.shipmentcaseagent;

/**
 * 一条数据就绪事实的来源类型。
 *
 * <p>来源会随事实一起进入审计链。{@code SYNTHETIC_DEMO} 只能表示学习和演示数据，
 * 不能伪装成 ERP、WMS 或人工确认的企业真实数据。</p>
 */
public enum ShipmentDataSourceType {
    MANUAL_CONFIRMATION,
    CSV_IMPORT,
    ERP,
    WMS,
    SYNTHETIC_DEMO
}
