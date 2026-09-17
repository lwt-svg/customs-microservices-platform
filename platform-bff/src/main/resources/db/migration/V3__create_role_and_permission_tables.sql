--
-- V3 创建角色、权限以及两张关系表。
-- 本迁移只建立“用户拥有哪些角色、角色拥有哪些权限”的数据基础，
-- 尚不执行登录认证，也不会自动拦截任何 HTTP 请求。

CREATE TABLE platform_role (
    id BIGINT NOT NULL AUTO_INCREMENT,
    role_code VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_platform_role_code (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE platform_permission (
    id BIGINT NOT NULL AUTO_INCREMENT,
    permission_code VARCHAR(128) NOT NULL,
    permission_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_platform_permission_code (permission_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 一个用户可以有多个角色，一个角色也可以分配给多个用户。
CREATE TABLE platform_user_role (
    user_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,

    PRIMARY KEY (user_id, role_id),
    KEY idx_platform_user_role_role_id (role_id),

    CONSTRAINT fk_platform_user_role_user
        FOREIGN KEY (user_id)
        REFERENCES platform_user (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_platform_user_role_role
        FOREIGN KEY (role_id)
        REFERENCES platform_role (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 一个角色可以包含多个权限，一个权限也可以被多个角色复用。
CREATE TABLE platform_role_permission (
    role_id BIGINT NOT NULL,
    permission_id BIGINT NOT NULL,

    PRIMARY KEY (role_id, permission_id),
    KEY idx_platform_role_permission_permission_id (permission_id),

    CONSTRAINT fk_platform_role_permission_role
        FOREIGN KEY (role_id)
        REFERENCES platform_role (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_platform_role_permission_permission
        FOREIGN KEY (permission_id)
        REFERENCES platform_permission (id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
