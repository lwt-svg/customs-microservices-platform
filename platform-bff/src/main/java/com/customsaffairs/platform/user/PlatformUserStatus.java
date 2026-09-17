package com.customsaffairs.platform.user;

/**
 * 平台用户的最小状态集合。
 *
 * <p>停用用户仍保留历史记录，但后续认证功能应禁止其登录。</p>
 */
public enum PlatformUserStatus {

    /** 用户当前可正常使用平台。 */
    ACTIVE,

    /** 用户已停用，保留数据但不能继续使用平台。 */
    DISABLED
}
