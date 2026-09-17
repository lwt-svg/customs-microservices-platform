package com.customsaffairs.platform.authorization;

/**
 * 角色和权限共用的最小状态。
 *
 * <p>14.4 只负责保存状态；真正鉴权时才会判断停用的角色或权限是否可以生效。</p>
 */
public enum AuthorizationStatus {
    /** 可以正常分配和使用。 */
    ACTIVE,

    /** 已停用，后续鉴权时不应继续生效。 */
    DISABLED
}
