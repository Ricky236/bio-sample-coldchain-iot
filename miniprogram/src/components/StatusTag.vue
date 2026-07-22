<script setup lang="ts">
import { computed } from 'vue'
import type { TaskStatus } from '@/types/api'
import { taskStatusText } from '@/utils/status'

const props = defineProps<{ status: TaskStatus }>()
const tone = computed(() => {
  const tones: Partial<Record<TaskStatus, string>> = {
    signed: 'success',
    rejected: 'danger',
    canceled: 'neutral',
    in_transit: 'active',
    arrived: 'arrived',
  }
  return tones[props.status] || 'waiting'
})
</script>

<template>
  <text class="tag" :class="tone">{{ taskStatusText[status] }}</text>
</template>

<style scoped>
.tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8rpx 20rpx;
  border-radius: 999rpx;
  color: #9a6a08;
  background: #fff5d9;
  font-size: 23rpx;
  font-weight: 650;
  white-space: nowrap;
}
.active { color: #4a9f00; background: #eff9e7; }
.success { color: #29996b; background: #e7f7ef; }
.arrived { color: #4a9f00; background: #eff9e7; }
.danger { color: #cb3e49; background: #ffeaec; }
.neutral { color: #7d8b9c; background: #edf1f5; }
</style>
