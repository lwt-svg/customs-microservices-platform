--
-- V12 将侧栏从“展示系统里有什么模块”调整为“业务人员每天怎样完成工作”。
--
-- 这里只调整菜单名称、分组、顺序和可见状态，不删除历史路由，也不改变业务数据：
-- 1. 驾驶舱改成“我的工作”，与发运案件一起组成日常入口；
-- 2. LINK 与装载只各保留一个专业作业入口；
-- 3. 自动化运行详情下沉到独立的运营监控分组；
-- 4. 能力目录和装载概览仍可通过原 URL 访问，但不再出现在日常主导航；
-- 5. 平台管理保持独立分组。
--

-- “我的工作”和“发运案件”是业务人员每天使用的两个入口。
UPDATE platform_menu
SET menu_name = '日常工作',
    sort_order = 10,
    status = 'ACTIVE'
WHERE menu_code = 'tradeops';

UPDATE platform_menu
SET menu_name = '我的工作',
    sort_order = 10,
    status = 'ACTIVE'
WHERE menu_code = 'dashboard';

UPDATE platform_menu
SET menu_name = '发运案件',
    sort_order = 20,
    status = 'ACTIVE'
WHERE menu_code = 'tradeops-shipment-cases';

-- 专业作业只呈现可以直接开始工作的入口，避免“概览”和“任务”并列造成重复。
UPDATE platform_menu
SET menu_name = '专业作业',
    sort_order = 20,
    status = 'ACTIVE'
WHERE menu_code = 'link-booking';

UPDATE platform_menu
SET menu_name = 'LINK 订舱',
    sort_order = 10,
    status = 'ACTIVE'
WHERE menu_code = 'link-booking-applications';

UPDATE platform_menu
SET menu_name = '装载作业',
    sort_order = 20,
    status = 'ACTIVE'
WHERE menu_code = 'supply-chain-load-tasks';

UPDATE platform_menu
SET status = 'DISABLED'
WHERE menu_code = 'supply-chain-load-planning';

-- 运行轨迹是主管和运维排查入口，不与普通业务待办并列。
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
    'tradeops-monitoring',
    '运营监控',
    '/tradeops/monitoring',
    'server',
    30,
    'ACTIVE',
    NULL
WHERE NOT EXISTS (
    SELECT 1
    FROM platform_menu
    WHERE menu_code = 'tradeops-monitoring'
);

UPDATE platform_menu
SET menu_name = '运营监控',
    route_path = '/tradeops/monitoring',
    icon = 'server',
    sort_order = 30,
    status = 'ACTIVE',
    parent_id = NULL
WHERE menu_code = 'tradeops-monitoring';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'tradeops-monitoring'
SET child.menu_name = '自动化运行监控',
    child.sort_order = 10,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'tradeops-agent-center';

-- 能力目录是辅助说明页，保留 URL 供直接访问，但退出日常主导航。
UPDATE platform_menu
SET status = 'DISABLED'
WHERE menu_code = 'portal';

-- 管理能力继续保持独立，不与业务作业和运营监控混排。
UPDATE platform_menu
SET menu_name = '平台管理',
    sort_order = 90,
    status = 'ACTIVE'
WHERE menu_code = 'platform';
