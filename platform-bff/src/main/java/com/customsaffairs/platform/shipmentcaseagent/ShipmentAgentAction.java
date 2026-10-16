package com.customsaffairs.platform.shipmentcaseagent;

/**
 * 人工处理中断时允许提交的结构化动作。
 *
 * <p>动作使用稳定枚举而不是任意聊天文本。备注仍可通过 {@code comment} 补充，但真正推进
 * 状态机的是这些可审计动作，Python 不需要猜测自然语言究竟代表批准还是终止。</p>
 */
public enum ShipmentAgentAction {
    CONFIRM,
    SELECT,
    APPROVE,
    SUPPLY_DATA,
    STOP,
    RECHECK
}
