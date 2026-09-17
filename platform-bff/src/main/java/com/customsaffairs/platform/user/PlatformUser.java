package com.customsaffairs.platform.user;

import com.customsaffairs.platform.authorization.PlatformRole;
import com.customsaffairs.platform.organization.Organization;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.JoinTable;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.ManyToMany;
import jakarta.persistence.Table;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.Set;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;

/**
 * Java 中的平台用户实体，对应 MySQL 的 {@code platform_user} 表。
 *
 * <p>类名使用 PlatformUser，而不是 User，是为了避免与 Java、Spring Security 或数据库中的
 * 通用 User 名称混淆。</p>
 */
@Entity
@Table(name = "platform_user")
public class PlatformUser {

    /** 数据库生成的自增主键。 */
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 稳定且唯一的账号标识；14.3 尚未赋予它真正的登录能力。 */
    @Column(nullable = false, unique = true, length = 64)
    private String username;

    /** 页面显示名称，例如“关务演示用户”。 */
    @Column(name = "display_name", nullable = false, length = 128)
    private String displayName;

    /**
     * 多个用户可以属于同一个组织，所以这里是 ManyToOne（多对一）。
     *
     * <p>LAZY 表示查询用户时先保存组织引用，需要组织数据时再读取，避免每次查询都加载过多内容。</p>
     */
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "organization_id", nullable = false)
    private Organization organization;

    /** 数据库存储 ACTIVE 或 DISABLED 字符串。 */
    @Enumerated(EnumType.STRING)
    // 与 V2 迁移中的 VARCHAR(32) 对齐，不使用 MySQL 专有 ENUM 类型。
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 32)
    private PlatformUserStatus status;

    /**
     * 当前用户拥有的角色。
     *
     * <p>一个用户可以有多个角色，一个角色也可以分配给多个用户，所以使用 ManyToMany。
     * 实际关系保存在 {@code platform_user_role} 中间表中。</p>
     */
    @ManyToMany(fetch = FetchType.LAZY)
    @JoinTable(
            name = "platform_user_role",
            joinColumns = @JoinColumn(name = "user_id"),
            inverseJoinColumns = @JoinColumn(name = "role_id"))
    private Set<PlatformRole> roles = new LinkedHashSet<>();

    /** 新增记录时由 Hibernate 写入。 */
    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    /** 新增或修改记录时由 Hibernate写入。 */
    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    /** JPA 从数据库恢复对象时使用，业务代码不应创建空用户。 */
    protected PlatformUser() {
        // JPA 专用无参构造方法。
    }

    /**
     * 创建一个默认启用的平台用户。
     *
     * @param username 稳定账号标识。
     * @param displayName 页面显示名称。
     * @param organization 用户所属组织实体。
     */
    public PlatformUser(String username, String displayName, Organization organization) {
        this.username = username;
        this.displayName = displayName;
        this.organization = organization;
        this.status = PlatformUserStatus.ACTIVE;
    }

    /** 给用户分配角色；Set 会自动避免重复关系。 */
    public void assignRole(PlatformRole role) {
        roles.add(role);
    }

    /** @return 用户主键。 */
    public Long getId() {
        return id;
    }

    /** @return 稳定账号标识。 */
    public String getUsername() {
        return username;
    }

    /** @return 页面显示名称。 */
    public String getDisplayName() {
        return displayName;
    }

    /** @return 用户所属组织。 */
    public Organization getOrganization() {
        return organization;
    }

    /** @return 用户当前状态。 */
    public PlatformUserStatus getStatus() {
        return status;
    }

    /** @return 用户当前拥有的角色集合。 */
    public Set<PlatformRole> getRoles() {
        return roles;
    }

    /** @return 创建时间。 */
    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    /** @return 最后更新时间。 */
    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
