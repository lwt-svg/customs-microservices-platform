package com.customsaffairs.platform.shipmentcaseagent;

/**
 * 数据就绪事实所描述的业务对象类型。
 *
 * <p>这里只保留跨 Java、Python 和 Vue 都稳定的对象代码。展示名称和补数表单文案由
 * Python 的数据就绪策略以及前端共同决定，BFF 不根据对象类型重复推导业务规则。</p>
 */
public enum ShipmentDataSubjectType {
    CARGO_ITEM,
    PALLET_SPEC
}
