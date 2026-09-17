package com.customsaffairs.platform.organization;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDateTime;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;

/**
 * Java 中的组织对象，对应 MySQL 的 {@code platform_organization} 表。
 *
 * <p>{@code @Entity} 告诉 JPA：这个类不是普通工具类，而是一份需要保存到数据库的数据。
 * {@code @Table(name = "platform_organization")} 则明确指出它对应哪张表。</p>
 *
 * <p>这个实体只代表数据库结构，不直接作为 HTTP 响应返回。Controller 会使用
 * {@link OrganizationResponse} 生成对外 JSON，避免未来不小心暴露内部字段。</p>
 */
@Entity
@Table(name = "platform_organization")
public class Organization {

    /** 数据库主键；IDENTITY 表示 MySQL 在插入时生成自增编号。 */
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 组织稳定编码，例如 CUSTOMS；数据库迁移已要求它唯一且不能为空。 */
    @Column(name = "organization_code", nullable = false, unique = true, length = 64)
    private String organizationCode;

    /** 面向使用者的组织显示名称，例如“关务部”。 */
    @Column(name = "organization_name", nullable = false, length = 128)
    private String organizationName;

    /**
     * STRING 表示把枚举名称保存为 ACTIVE/DISABLED，而不是保存数字位置。
     * 这样即使将来枚举顺序变化，也不会让旧数据含义改变。
     */
    @Enumerated(EnumType.STRING)
    // Hibernate 6 在 MySQL 上可能默认期待原生 ENUM；这里明确与 Flyway 的 VARCHAR(32) 保持一致。
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 32)
    private OrganizationStatus status;

    /** Hibernate 插入记录时自动写入创建时间。 */
    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    /** Hibernate 创建或更新记录时自动写入最后更新时间。 */
    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    /**
     * JPA 从数据库还原对象时需要无参构造方法。
     * protected 表示业务代码不能随意使用“空组织”。
     */
    protected Organization() {
        // JPA 专用构造方法，不在这里填写业务默认值。
    }

    /**
     * 创建一个默认启用的组织。
     *
     * @param organizationCode 稳定且唯一的组织编码。
     * @param organizationName 显示名称。
     */
    public Organization(String organizationCode, String organizationName) {
        this.organizationCode = organizationCode;
        this.organizationName = organizationName;
        this.status = OrganizationStatus.ACTIVE;
    }

    /** @return 数据库生成的组织主键。 */
    public Long getId() {
        return id;
    }

    /** @return 组织稳定编码。 */
    public String getOrganizationCode() {
        return organizationCode;
    }

    /** @return 组织显示名称。 */
    public String getOrganizationName() {
        return organizationName;
    }

    /** @return 组织当前状态。 */
    public OrganizationStatus getStatus() {
        return status;
    }

    /** @return 记录创建时间。 */
    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    /** @return 记录最后更新时间。 */
    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
