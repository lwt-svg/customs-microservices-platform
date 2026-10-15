<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { getPlatformMenuTree, type PlatformMenu } from '@/api/platform'
import AdminLayout from '@/layouts/AdminLayout.vue'

const menuTree = ref<PlatformMenu[]>([])
const menuLoading = ref(true)
const menuError = ref('')

/**
 * 菜单名称和层级来自 Java BFF，而不是 Vue 写死的数据。
 *
 * Java 菜单接口失败时，AdminLayout 会使用最小兜底菜单，保证学习页面仍能进入。
 */
async function loadMenu(): Promise<void> {
  menuLoading.value = true
  menuError.value = ''

  try {
    menuTree.value = await getPlatformMenuTree()
  } catch (error) {
    menuError.value = error instanceof Error ? error.message : '读取平台菜单失败'
  } finally {
    menuLoading.value = false
  }
}

onMounted(loadMenu)
</script>

<template>
  <AdminLayout
    :menu-tree="menuTree"
    :menu-loading="menuLoading"
    :menu-error="menuError"
    @reload-menu="loadMenu"
  >
    <!--
      RouterView 会根据地址栏中的 URL 渲染页面。和原来的 currentPage 内存状态相比，
      刷新、前进后退、复制链接都能保持在同一业务页面。
    -->
    <RouterView />
  </AdminLayout>
</template>
