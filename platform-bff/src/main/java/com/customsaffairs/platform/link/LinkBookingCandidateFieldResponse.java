package com.customsaffairs.platform.link;

/**
 * PDF 解析得到的单个候选字段。
 *
 * <p>候选值不是最终业务真值。Vue 必须同时展示证据与置信度，并允许学习者在创建草稿前
 * 人工修改。</p>
 *
 * @param value 解析器建议值；未识别时可以为空。
 * @param evidence PDF 中与候选值对应的短证据，不包含整篇原文。
 * @param confidence HIGH、MEDIUM、LOW 或 NONE。
 */
public record LinkBookingCandidateFieldResponse(
        String value,
        String evidence,
        String confidence) {
}
