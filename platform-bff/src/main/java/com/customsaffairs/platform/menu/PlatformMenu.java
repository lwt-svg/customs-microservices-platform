package com.customsaffairs.platform.menu;

import com.customsaffairs.platform.authorization.PlatformPermission;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.LocalDateTime;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;

/** 平台后台菜单实体，对应 {@code platform_menu} 表。 */
@Entity
@Table(name = "platform_menu")
public class PlatformMenu {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "menu_code", nullable = false, unique = true, length = 64)
    private String menuCode;

    @Column(name = "menu_name", nullable = false, length = 128)
    private String menuName;

    @Column(name = "route_path", nullable = false, length = 255)
    private String routePath;

    @Column(length = 64)
    private String icon;

    @Column(name = "sort_order", nullable = false)
    private Integer sortOrder;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 32)
    private MenuStatus status;

    /** 父菜单为空表示一级菜单。 */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "parent_id")
    private PlatformMenu parent;

    /** 菜单可以选择性绑定一个已有权限。 */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "permission_id")
    private PlatformPermission permission;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    protected PlatformMenu() {
        // JPA 专用无参构造方法。
    }

    public PlatformMenu(
            String menuCode,
            String menuName,
            String routePath,
            String icon,
            Integer sortOrder,
            PlatformMenu parent,
            PlatformPermission permission) {
        this.menuCode = menuCode;
        this.menuName = menuName;
        this.routePath = routePath;
        this.icon = icon;
        this.sortOrder = sortOrder;
        this.parent = parent;
        this.permission = permission;
        this.status = MenuStatus.ACTIVE;
    }

    public Long getId() {
        return id;
    }

    public String getMenuCode() {
        return menuCode;
    }

    public String getMenuName() {
        return menuName;
    }

    public String getRoutePath() {
        return routePath;
    }

    public String getIcon() {
        return icon;
    }

    public Integer getSortOrder() {
        return sortOrder;
    }

    public MenuStatus getStatus() {
        return status;
    }

    public PlatformMenu getParent() {
        return parent;
    }

    public PlatformPermission getPermission() {
        return permission;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
