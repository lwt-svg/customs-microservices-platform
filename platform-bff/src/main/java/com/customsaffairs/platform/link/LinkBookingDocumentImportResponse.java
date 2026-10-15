package com.customsaffairs.platform.link;

/**
 * Java BFF 返回给 Vue 的 PDF 导入会话。
 *
 * <p>响应只包含文件元数据、结构化候选和短证据，不返回存储路径、SHA-256 或完整 PDF
 * 文本。</p>
 */
public record LinkBookingDocumentImportResponse(
        long id,
        String status,
        String originalFilename,
        long fileSizeBytes,
        String parserVersion,
        LinkBookingDocumentCandidatesResponse candidates,
        String createdApplicationNumber,
        String createdAt,
        String updatedAt) {
}
