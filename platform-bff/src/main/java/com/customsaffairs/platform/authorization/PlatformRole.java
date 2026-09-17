package com.customsaffairs.platform.authorization;

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
 * 平台角色实体，对应 {@code platform_role} 表。
 *
 * <p>角色不是具体操作权限，而是一组权限的集合。例如“系统管理员”角色可以同时包含
 * 用户查看、用户管理、组织查看和组织管理等权限。</p>
 */
@Entity
@Table(name = "platform_role")
public class PlatformRole {

    /** 数据库生成的自增主键。 */
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 稳定且唯一的角色编码，例如 SYSTEM_ADMIN。 */
    @Column(name = "role_code", nullable = false, unique = true, length = 64)
    private String roleCode;

    /** 页面展示名称，例如“系统管理员”。 */
    @Column(name = "role_name", nullable = false, length = 128)
    private String roleName;

    /** 角色当前状态，以字符串保存。 */
    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 32)
    private AuthorizationStatus status;

    /**
     * 当前角色包含的权限。
     *
     * <p>{@code @JoinTable} 指定中间表 {@code platform_role_permission}。
     * 中间表每一行只表示“某个角色拥有某个权限”。</p>
     */
    @ManyToMany(fetch = FetchType.LAZY)
    @JoinTable(
            name = "platform_role_permission",
            joinColumns = @JoinColumn(name = "role_id"),
            inverseJoinColumns = @JoinColumn(name = "permission_id"))
    private Set<PlatformPermission> permissions = new LinkedHashSet<>();

    /** 新建记录时由 Hibernate 写入。 */
    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    /** 新建或修改记录时由 Hibernate 写入。 */
    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    /** JPA 从数据库恢复对象时使用。 */
    protected PlatformRole() {
        // JPA 专用无参构造方法。
    }

    /**
     * 创建一个默认启用的角色。
     *
     * @param roleCode 稳定角色编码。
     * @param roleName 页面展示名称。
     */
    public PlatformRole(String roleCode, String roleName) {
        this.roleCode = roleCode;
        this.roleName = roleName;
        this.status = AuthorizationStatus.ACTIVE;
    }

    /** 将一个权限加入角色；Set 会自动避免重复关系。 */
    public void assignPermission(PlatformPermission permission) {
        permissions.add(permission);
    }

    public Long getId() {
        return id;
    }

    public String getRoleCode() {
        return roleCode;
    }

    public String getRoleName() {
        return roleName;
    }

    public AuthorizationStatus getStatus() {
        return status;
    }

    public Set<PlatformPermission> getPermissions() {
        return permissions;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
