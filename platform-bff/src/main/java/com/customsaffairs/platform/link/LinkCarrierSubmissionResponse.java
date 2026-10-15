package com.customsaffairs.platform.link;

/**
 * LINK 向独立模拟承运人投递申请的当前集成状态。
 *
 * <p>它与申请业务状态分开：例如投递进入 DEAD 是技术失败，申请仍可能保持 SUBMITTED，
 * 不能伪装成承运人业务拒绝。</p>
 */
public record LinkCarrierSubmissionResponse(
        String status,
        int attemptCount,
        String lastErrorMessage,
        boolean canRetry,
        boolean canCancel,
        String carrierRequestId) {
}
