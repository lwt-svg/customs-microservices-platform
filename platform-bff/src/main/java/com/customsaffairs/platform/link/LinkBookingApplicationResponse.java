package com.customsaffairs.platform.link;

import java.time.LocalDate;

/**
 * Java BFF 返回给 Vue 的 LINK 订舱申请视图。
 *
 * <p>时间暂时按 Python 返回的 ISO-8601 字符串透传，避免本地 MySQL 无时区时间与测试中的
 * UTC 时间在 Java 反序列化时产生不必要的格式差异。</p>
 *
 * @param id Python LINK 数据库中的主键。
 * @param applicationNumber 申请编号。
 * @param bookingReference LINK 订舱参考号，首期可能为空。
 * @param originPort 起运港。
 * @param destinationPort 目的港。
 * @param transportMode 运输方式。
 * @param containerType 箱型。
 * @param requestedEtd 计划离港日期。
 * @param bookingDetails 从装箱单确认并保存的业务明细；手工草稿可能为空。
 * @param status 当前状态。
 * @param createdAt 创建时间。
 * @param updatedAt 更新时间。
 */
public record LinkBookingApplicationResponse(
        long id,
        String applicationNumber,
        String bookingReference,
        String originPort,
        String destinationPort,
        String transportMode,
        String containerType,
        LocalDate requestedEtd,
        LinkBookingDetailsResponse bookingDetails,
        LinkBookingStatus status,
        String createdAt,
        String updatedAt) {

    /** 兼容手工创建和旧测试；这类申请没有 PDF 业务明细。 */
    public LinkBookingApplicationResponse(
            long id,
            String applicationNumber,
            String bookingReference,
            String originPort,
            String destinationPort,
            String transportMode,
            String containerType,
            LocalDate requestedEtd,
            LinkBookingStatus status,
            String createdAt,
            String updatedAt) {
        this(
                id,
                applicationNumber,
                bookingReference,
                originPort,
                destinationPort,
                transportMode,
                containerType,
                requestedEtd,
                null,
                status,
                createdAt,
                updatedAt);
    }
}
