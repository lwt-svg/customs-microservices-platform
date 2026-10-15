package com.customsaffairs.platform.link;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.LocalDate;

/**
 * 人工复核 PDF 候选后提交的权威草稿字段。
 *
 * <p>不能只传“接受解析结果”布尔值。用户可能修正任意候选字段，因此确认请求必须重新
 * 发送完整字段，Python 再执行最终校验和原子建单。</p>
 */
public record ConfirmLinkBookingDocumentImportRequest(
        @NotBlank
        @Size(max = 64)
        @Pattern(
                regexp = "^DEMO-[A-Za-z0-9_-]+$",
                message = "学习环境的申请编号必须以 DEMO- 开头")
        String applicationNumber,
        @NotBlank @Size(max = 64) String originPort,
        @NotBlank @Size(max = 64) String destinationPort,
        @NotBlank @Size(max = 32) String transportMode,
        @NotBlank @Size(max = 32) String containerType,
        LocalDate requestedEtd,
        LinkBookingDetailsResponse bookingDetails) {

    /** 兼容不带装箱单明细的手工确认请求。 */
    public ConfirmLinkBookingDocumentImportRequest(
            String applicationNumber,
            String originPort,
            String destinationPort,
            String transportMode,
            String containerType,
            LocalDate requestedEtd) {
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
