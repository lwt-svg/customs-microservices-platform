--
-- V4 创建平台菜单表。
-- 菜单属于 Java 平台层，用于后台导航；它不代表 Python 业务数据。
-- permission_id 可选：菜单可以绑定一个权限，也可以先作为公共入口存在。
--
CREATE TABLE platform_menu (
    id BIGINT NOT NULL AUTO_INCREMENT,
    menu_code VARCHAR(64) NOT NULL,
    menu_name VARCHAR(128) NOT NULL,
    route_path VARCHAR(255) NOT NULL,
    icon VARCHAR(64) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    parent_id BIGINT NULL,
    permission_id BIGINT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_platform_menu_code (menu_code),
    KEY idx_platform_menu_parent_id (parent_id),
    KEY idx_platform_menu_permission_id (permission_id),

    CONSTRAINT fk_platform_menu_parent
        FOREIGN KEY (parent_id)
        REFERENCES platform_menu (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_platform_menu_permission
        FOREIGN KEY (permission_id)
        REFERENCES platform_permission (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
