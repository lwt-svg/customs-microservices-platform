--
-- V11 在核心工作台下增加 Agent 运行中心。
--
-- 页面展示真实 Shipment Agent 运行、人工待办和 Trace；它不是静态“智能体卡片”目录。
-- INSERT ... SELECT ... WHERE NOT EXISTS 让初始化脚本即使被人工重复执行也不会插入重复菜单。
--
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
    'tradeops-agent-center',
    'Agent 运行中心',
    '/tradeops/agents',
    'server',
    30,
    'ACTIVE',
    parent.id
FROM platform_menu AS parent
WHERE parent.menu_code = 'tradeops'
  AND NOT EXISTS (
      SELECT 1
      FROM platform_menu
      WHERE menu_code = 'tradeops-agent-center'
  );
