--
-- V2 创建平台用户表。
-- 本表只保存平台用户的基础身份和所属组织，不保存密码、Token、角色或权限。
-- 登录认证、密码哈希和角色授权将在后续独立小节设计，不能用明文密码临时占位。
CREATE TABLE platform_user (
    -- MySQL 自动生成的内部主键。
    id BIGINT NOT NULL AUTO_INCREMENT,

    -- 稳定登录名/账号标识。当前虽然还不能登录，但后续认证会复用这个字段。
    username VARCHAR(64) NOT NULL,

    -- 页面上展示给人看的姓名或称呼，可以与 username 不同。
    display_name VARCHAR(128) NOT NULL,

    -- 用户所属组织的主键，指向 V1 创建的 platform_organization.id。
    organization_id BIGINT NOT NULL,

    -- 用户状态先保留启用和停用两个最小选项。
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',

    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_platform_user_username (username),
    KEY idx_platform_user_organization_id (organization_id),

    -- 外键在数据库层保证：用户不能属于一条不存在的组织记录。
    CONSTRAINT fk_platform_user_organization
        FOREIGN KEY (organization_id)
        REFERENCES platform_organization (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
