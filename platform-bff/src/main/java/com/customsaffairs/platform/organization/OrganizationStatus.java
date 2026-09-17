package com.customsaffairs.platform.organization;

/**
 * 组织可使用的最小状态集合。
 *
 * <p>数据库中保存的是 {@code ACTIVE} 或 {@code DISABLED} 这样的文字，而不是难以理解的数字。
 * 当前只做基础启用/停用；组织注销、有效期和审批流程属于后续需求。</p>
 */
public enum OrganizationStatus {

    /** 组织可正常被分配给用户并参与权限判断。 */
    ACTIVE,

    /** 组织保留历史记录，但后续业务不应再分配新的用户。 */
    DISABLED
}
