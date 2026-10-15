--
-- V5 为本地学习环境提供一组最小平台菜单。
-- 菜单仍由 Java BFF 返回，Vue 不把这些入口写死在页面里。
-- 这里不绑定权限：第 15 章先验证菜单读取和导航，登录鉴权留到后续章节。
--
INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
) VALUES (
    'dashboard',
    '工作台',
    '/dashboard',
    'dashboard',
    10,
    'ACTIVE'
);

SET @platform_menu_id = (
    SELECT id
    FROM platform_menu
    WHERE menu_code = 'platform'
);

INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
) VALUES (
    'platform',
    '平台管理',
    '/platform',
    'organization',
    20,
    'ACTIVE'
);

INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status,
    parent_id
) VALUES
(
    'platform-organizations',
    '组织管理',
    '/organizations',
    'organization',
    10,
    'ACTIVE',
    @platform_menu_id
),
(
    'platform-users',
    '用户管理',
    '/users',
    'users',
    20,
    'ACTIVE',
    @platform_menu_id
),
(
    'platform-authorization',
    '角色与权限',
    '/authorization',
    'shield',
    30,
    'ACTIVE',
    @platform_menu_id
);
