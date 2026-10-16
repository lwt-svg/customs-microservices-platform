package com.customsaffairs.platform.shipmentcaseagent;

/**
 * Shipment Coordinator 当前观察到的装载子 Agent 最小状态投影。
 *
 * <p>这里不复制候选方案、3D 坐标或装载约束，只给总运行中心提供跳转和等待判断所需的
 * 稳定字段。专业详情仍由智能装载工作台读取自己的权威 API。</p>
 */
public record ShipmentAgentChildResponse(
        String runId,
        String taskNumber,
        String status,
        String currentStep,
        int runVersion,
        String nextAction) {
}
