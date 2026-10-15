--
-- V6 修正 V5 初始菜单的父子关系。
-- 使用 JOIN 回填 parent_id，避免迁移脚本依赖跨语句用户变量。
--
UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'platform'
SET child.parent_id = parent.id
WHERE child.menu_code IN (
    'platform-organizations',
    'platform-users',
    'platform-authorization'
)
  AND child.parent_id IS NULL;
