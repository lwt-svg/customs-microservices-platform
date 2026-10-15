package com.customsaffairs.platform.shipmentcase;

/**
 * 案件中的一个资源引用及其绑定时验证快照。
 *
 * @param id Python 数据库中的绑定流水主键。
 * @param resourceType 被绑定的资源类型。
 * @param resourceNumber 下游服务拥有的稳定业务编号。
 * @param resourceStatusAtVerification 绑定时看到的资源状态。
 * @param verificationSource 使用的验证适配器。
 * @param verifiedAt 完成资源验证的 UTC 时间。
 * @param boundAt 完成案件绑定的 UTC 时间。
 */
public record ShipmentCaseResourceBindingResponse(
        long id,
        ShipmentResourceType resourceType,
        String resourceNumber,
        String resourceStatusAtVerification,
        String verificationSource,
        String verifiedAt,
        String boundAt) {
}
