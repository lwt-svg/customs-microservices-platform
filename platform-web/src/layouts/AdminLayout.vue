<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { PlatformMenu } from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import { supportedRoutePaths } from '@/router'

type IconName =
  | 'dashboard'
  | 'organization'
  | 'users'
  | 'shield'
  | 'key'
  | 'server'
  | 'database'
  | 'layers'
  | 'box'
  | 'truck'

const props = defineProps<{
  menuTree: PlatformMenu[]
  menuLoading: boolean
  menuError: string
}>()

const emit = defineEmits<{
  reloadMenu: []
}>()

const route = useRoute()
const router = useRouter()

/**
 * 当 Java BFF 尚未启动、菜单接口失败或返回空树时使用的最小导航。
 *
 * 它只是可用性兜底，不是第二份真实菜单数据，也不代表用户拥有这些权限。真正的权限
 * 过滤要等登录和 Spring Security 章节实现。
 */
const fallbackGroups = [
  {
    title: '工作空间',
    items: [
      { label: '沙丘 OS', path: '/', icon: 'dashboard' as IconName, enabled: true },
      { label: '平台工作台', path: '/dashboard', icon: 'server' as IconName, enabled: true },
    ],
  },
  {
    title: '平台管理',
    items: [
      {
        label: '组织管理',
        path: '/organizations',
        icon: 'organization' as IconName,
        enabled: true,
      },
      { label: '用户管理', path: '/users', icon: 'users' as IconName, enabled: true },
      {
        label: '角色与权限',
        path: '/authorization',
        icon: 'shield' as IconName,
        enabled: true,
      },
    ],
  },
  {
    title: 'TradeOps',
    items: [
      {
        label: '发运案件中心',
        path: '/tradeops/shipment-cases',
        icon: 'truck' as IconName,
        enabled: true,
      },
    ],
  },
  {
    title: '关务 OS',
    items: [
      {
        label: '订舱申请',
        path: '/link-booking/applications',
        icon: 'database' as IconName,
        enabled: true,
      },
    ],
  },
  {
    title: '供应链 OS',
    items: [
      {
        label: '智能装载中心',
        path: '/supply-chain/load-planning',
        icon: 'layers' as IconName,
        enabled: true,
      },
      {
        label: '装载任务',
        path: '/supply-chain/load-planning/tasks',
        icon: 'box' as IconName,
        enabled: true,
      },
    ],
  },
]

function toIconName(icon: string | null): IconName {
  const supportedIcons: IconName[] = [
    'dashboard',
    'organization',
    'users',
    'shield',
    'key',
    'server',
    'database',
    'layers',
    'box',
    'truck',
  ]
  return icon && supportedIcons.includes(icon as IconName) ? (icon as IconName) : 'dashboard'
}

/**
 * 把 Java 返回的父子菜单树转换成侧栏分组。
 *
 * enabled 目前只表示“Vue Router 是否已经注册对应页面”，不是鉴权结果。数据库里有
 * 菜单但前端没有对应路由时，侧栏会显示“开发中”并禁止点击。
 */
const navigationGroups = computed(() => {
  if (props.menuTree.length === 0) {
    return fallbackGroups
  }

  return props.menuTree
    .filter((menu) => menu.status === 'ACTIVE')
    .map((rootMenu) => {
      const menuItems = rootMenu.children.length > 0 ? rootMenu.children : [rootMenu]
      return {
        title: rootMenu.children.length > 0 ? rootMenu.menuName : '工作空间',
        items: menuItems
          .filter((menu) => menu.status === 'ACTIVE')
          .map((menu) => ({
            label: menu.menuName,
            path: menu.routePath,
            icon: toIconName(menu.icon),
            enabled: supportedRoutePaths.has(menu.routePath),
          })),
      }
    })
})

/**
 * 页面标题优先采用 Java 菜单名称，页面说明来自前端路由 meta。
 * 这样后台可以调整菜单文案，但不会把大段体验文案塞进数据库。
 */
const pageTitle = computed(() => {
  let menuTitle = ''
  for (const rootMenu of props.menuTree) {
    const menus = rootMenu.children.length > 0 ? rootMenu.children : [rootMenu]
    const matchedMenu = menus.find((menu) => menu.routePath === route.path)
    if (matchedMenu) {
      menuTitle = matchedMenu.menuName
      break
    }
  }

  return {
    title: menuTitle || String(route.meta.title ?? '沙丘 OS'),
    description: String(route.meta.description ?? ''),
  }
})

function navigate(path: string): void {
  if (supportedRoutePaths.has(path)) {
    void router.push(path)
  }
}
</script>

<template>
  <div class="admin-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/" aria-label="返回沙丘 OS 门户">
        <span class="brand-mark"><span></span></span>
        <div>
          <strong>沙丘 OS</strong>
          <small>企业智能业务平台</small>
        </div>
      </RouterLink>

      <nav class="navigation" aria-label="后台主导航">
        <section v-for="group in navigationGroups" :key="group.title" class="navigation-group">
          <p>{{ group.title }}</p>
          <button
            v-for="item in group.items"
            :key="item.label"
            class="navigation-item"
            :class="{ active: item.path === route.path, disabled: !item.enabled }"
            type="button"
            :disabled="!item.enabled"
            @click="navigate(item.path)"
          >
            <UiIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <span v-if="!item.enabled" class="coming-soon">开发中</span>
          </button>
        </section>

        <div v-if="menuLoading" class="navigation-status">正在读取菜单…</div>
        <div v-else-if="menuError" class="navigation-status error">
          <span>菜单读取失败</span>
          <button type="button" @click="emit('reloadMenu')">重试</button>
        </div>
      </nav>

      <div class="sidebar-footer">
        <span class="environment-dot"></span>
        本地沙箱环境
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="page-title">
          <h1>{{ pageTitle.title }}</h1>
          <p>{{ pageTitle.description }}</p>
        </div>
        <div class="account">
          <span class="avatar">A</span>
          <div>
            <strong>admin</strong>
            <small>本地管理员</small>
          </div>
        </div>
      </header>

      <main class="main-content">
        <slot />
      </main>
    </div>
  </div>
</template>
