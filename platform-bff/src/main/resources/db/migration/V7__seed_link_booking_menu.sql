--
-- V7 增加 LINK 业务菜单。
-- 21.1 先开放订舱申请查询页；创建和状态操作在 21.2、21.3 分别接入。
--
INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
) VALUES (
    'link-booking',
    'LINK 业务',
    '/link-booking',
    'database',
    30,
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
)
SELECT
    'link-booking-applications',
    '订舱申请',
    '/link-booking/applications',
    'database',
    10,
    'ACTIVE',
    parent.id
FROM platform_menu AS parent
WHERE parent.menu_code = 'link-booking';
