package com.customsaffairs.platform.link;

/**
 * PDF 中可用于创建订舱草稿的候选字段与装箱单业务明细。
 *
 * <p>字段固定成 record，而不是返回任意 Map，这样 Java、Vue 与 Python 的合同变更会在
 * 编译期尽早暴露。</p>
 */
public record LinkBookingDocumentCandidatesResponse(
        LinkBookingCandidateFieldResponse applicationNumber,
        LinkBookingCandidateFieldResponse originPort,
        LinkBookingCandidateFieldResponse destinationPort,
        LinkBookingCandidateFieldResponse transportMode,
        LinkBookingCandidateFieldResponse containerType,
        LinkBookingCandidateFieldResponse requestedEtd,
        LinkBookingDetailsResponse bookingDetails) {

    /** 兼容没有详细装箱单结构的旧解析响应。 */
    public LinkBookingDocumentCandidatesResponse(
            LinkBookingCandidateFieldResponse applicationNumber,
            LinkBookingCandidateFieldResponse originPort,
            LinkBookingCandidateFieldResponse destinationPort,
            LinkBookingCandidateFieldResponse transportMode,
            LinkBookingCandidateFieldResponse containerType,
            LinkBookingCandidateFieldResponse requestedEtd) {
        this(
                applicationNumber,
                originPort,
                destinationPort,
                transportMode,
                containerType,
                requestedEtd,
                null);
    }
}
