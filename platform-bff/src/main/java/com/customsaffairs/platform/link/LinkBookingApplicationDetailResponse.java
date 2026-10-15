package com.customsaffairs.platform.link;

import java.util.List;

/**
 * 一份订舱申请的详情：当前快照和倒序操作流水。
 *
 * <p>把这两个概念拆开，可以避免前端根据当前状态猜测历史；未来 PDF 解析、模拟承运人回调、
 * 失败重试等也能继续追加事件，而无需污染申请主表。</p>
 *
 * @param application 当前申请快照。
 * @param events 最新事件在前的不可变流水。
 * @param carrierSubmission 向模拟承运人投递的当前状态；草稿尚未提交时为空。
 */
public record LinkBookingApplicationDetailResponse(
        LinkBookingApplicationResponse application,
        List<LinkBookingApplicationEventResponse> events,
        LinkCarrierSubmissionResponse carrierSubmission) {
}
