<script setup lang="ts">
import { ref } from 'vue'

import AdminLayout from '@/layouts/AdminLayout.vue'
import DashboardView from '@/views/DashboardView.vue'
import OrganizationView from '@/views/OrganizationView.vue'

type PageKey = 'dashboard' | 'organizations'

const currentPage = ref<PageKey>('dashboard')
</script>

<template>
  <AdminLayout :current-page="currentPage" @navigate="currentPage = $event">
    <DashboardView v-if="currentPage === 'dashboard'" @open-organizations="currentPage = 'organizations'" />
    <OrganizationView v-else />
  </AdminLayout>
</template>
