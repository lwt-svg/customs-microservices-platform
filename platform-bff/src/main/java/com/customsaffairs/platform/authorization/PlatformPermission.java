package com.customsaffairs.platform.authorization;

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
 * 平台权限实体，对应 {@code platform_permission} 表。
 *
 * <p>权限表示一个最小操作能力，例如 {@code platform:user:read}。权限编码供程序判断，
 * 权限名称供人阅读。</p>
 */
@Entity
@Table(name = "platform_permission")
public class PlatformPermission {

    /** 数据库生成的自增主键。 */
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 稳定且唯一的机器编码，例如 platform:user:read。 */
    @Column(name = "permission_code", nullable = false, unique = true, length = 128)
    private String permissionCode;

    /** 页面展示名称，例如“查看平台用户”。 */
    @Column(name = "permission_name", nullable = false, length = 128)
    private String permissionName;

    /** 权限当前状态，以字符串保存。 */
    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 32)
    private AuthorizationStatus status;

    /** 新建记录时由 Hibernate 写入。 */
    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    /** 新建或修改记录时由 Hibernate 写入。 */
    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    /** JPA 从数据库恢复对象时使用。 */
    protected PlatformPermission() {
        // JPA 专用无参构造方法。
    }

    /**
     * 创建一个默认启用的权限。
     *
     * @param permissionCode 稳定权限编码。
     * @param permissionName 页面展示名称。
     */
    public PlatformPermission(String permissionCode, String permissionName) {
        this.permissionCode = permissionCode;
        this.permissionName = permissionName;
        this.status = AuthorizationStatus.ACTIVE;
    }

    public Long getId() {
        return id;
    }

    public String getPermissionCode() {
        return permissionCode;
    }

    public String getPermissionName() {
        return permissionName;
    }

    public AuthorizationStatus getStatus() {
        return status;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
