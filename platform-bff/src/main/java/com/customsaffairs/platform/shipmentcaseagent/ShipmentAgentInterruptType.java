package com.customsaffairs.platform.shipmentcaseagent;

/**
 * Shipment Coordinator Agent 可以交给人工处理的六类中断。
 *
 * <p>中断类型描述“为什么工作流暂停”，而不是用户最终选择了什么动作。把它建模为枚举后，
 * Vue 传入拼写错误的类型会在 Java 边界直接得到 400，不会带着错误值进入 Python 工作流。
 */
public enum ShipmentAgentInterruptType {
    /** 首次启动时，运输场景需要的能力组合仍需业务人员确认。 */
    SCENARIO_POLICY_CONFIRMATION,

    /** 运行所需的库存、主数据或证据尚不完整，需要业务人员补数。 */
    DATA_READINESS_REMEDIATION,

    /** 案件绑定了多个同类资源，需要人工明确选择本次运行使用哪一个。 */
    RESOURCE_SELECTION,

    /** 案件仍是草稿，Agent 不得在没有人工授权的情况下自动激活。 */
    CASE_ACTIVATION_APPROVAL,

    /** LINK 申请即将从草稿进入真实提交阶段，需要人工批准。 */
    BOOKING_SUBMIT_APPROVAL,

    /** 订舱提交失败或结果不确定，禁止盲目重试，需要人工决定。 */
    BOOKING_RETRY_APPROVAL,

    /** 装载正式版本和订舱结果均已就绪，等待人工确认结案。 */
    COMPLETION_APPROVAL
}
