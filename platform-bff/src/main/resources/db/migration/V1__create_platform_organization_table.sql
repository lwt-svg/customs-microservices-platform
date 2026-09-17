--
-- V1 表示平台数据库的第一份结构迁移。
-- Flyway 只会对同一个 schema 成功执行一次此文件，执行记录保存在 flyway_schema_history 表。
--
-- 这张表属于 Java 平台层；未来的 Python LINK 服务不得直接读写它。
-- 14.2 才会编写 Java Organization 实体和组织管理 API，本文件只先建立稳定的数据地基。
CREATE TABLE platform_organization (
    -- 自增主键是数据库内部稳定编号，不把组织名称当作主键。
    id BIGINT NOT NULL AUTO_INCREMENT,

    -- 组织编码是给 API、权限和外部系统使用的稳定业务标识。
    organization_code VARCHAR(64) NOT NULL,

    -- 组织显示名称，例如“关务部”；本学习阶段只保存构造样例。
    organization_name VARCHAR(128) NOT NULL,

    -- 状态先使用简单字符串；14.2 会定义可选状态及其校验规则。
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',

    -- UTC/时区策略将在平台时间规范小节统一设计；现在保留精确创建、更新时间字段。
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_platform_organization_code (organization_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
