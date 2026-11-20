<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from './components/AppShell.vue'

const route = useRoute()
const showShell = computed(() => route.name !== 'login')
</script>

<template>
  <AppShell v-if="showShell">
    <router-view />
  </AppShell>
  <router-view v-else />
</template>
