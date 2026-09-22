<script setup lang="ts">
defineProps<{ status: string }>();
</script>

<template>
  <span class="status-badge" :data-status="status"><span aria-hidden="true" class="status-dot" />{{ status }}</span>
</template>
