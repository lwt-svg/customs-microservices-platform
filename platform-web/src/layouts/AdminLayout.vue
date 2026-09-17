<script setup lang="ts">
import UiIcon from '@/components/UiIcon.vue'

type PageKey = 'dashboard' | 'organizations'

defineProps<{
  currentPage: PageKey
}>()

const emit = defineEmits<{
  navigate: [page: PageKey]
}>()

const navigationGroups = [
  {
    title: '工作空间',
    items: [
      { label: '工作台', page: 'dashboard' as PageKey, icon: 'dashboard' as const, enabled: true },
    ],
  },
  {
    title: '平台管理',
    items: [
      {
        label: '组织管理',
        page: 'organizations' as PageKey,
        icon: 'organization' as const,
        enabled: true,
      },
      { label: '用户管理', page: null, icon: 'users' as const, enabled: false },
      { label: '角色管理', page: null, icon: 'shield' as const, enabled: false },
      { label: '权限管理', page: null, icon: 'key' as const, enabled: false },
    ],
  },
]

const pageTitles: Record<PageKey, { title: string; description: string }> = {
  dashboard: { title: '工作台', description: '查看平台数据与服务运行状态' },
  organizations: { title: '组织管理', description: '维护平台组织和用户归属关系' },
}
</script>

<template>
  <div class="admin-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark"><span></span></span>
        <div>
          <strong>沙丘 OS</strong>
          <small>管理控制台</small>
        </div>
      </div>

      <nav class="navigation" aria-label="后台主导航">
        <section v-for="group in navigationGroups" :key="group.title" class="navigation-group">
          <p>{{ group.title }}</p>
          <button
            v-for="item in group.items"
            :key="item.label"
            class="navigation-item"
            :class="{ active: item.page === currentPage, disabled: !item.enabled }"
            type="button"
            :disabled="!item.enabled"
            @click="item.page && emit('navigate', item.page)"
          >
            <UiIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <span v-if="!item.enabled" class="coming-soon">开发中</span>
          </button>
        </section>
      </nav>

      <div class="sidebar-footer">
        <span class="environment-dot"></span>
        本地学习环境
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="page-title">
          <h1>{{ pageTitles[currentPage].title }}</h1>
          <p>{{ pageTitles[currentPage].description }}</p>
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
