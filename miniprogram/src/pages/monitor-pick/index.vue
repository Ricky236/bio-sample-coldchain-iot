<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useSessionStore } from '@/stores/session'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task } from '@/types/api'
import { taskStatusText } from '@/utils/status'

const session = useSessionStore()
const tasks = ref<Task[]>([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const activeFilter = ref<'all' | 'in_transit' | 'pending' | 'done'>('all')
const highlightId = ref('')

const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return tasks.value.filter((task) => {
    const status = task.status
    const filterOK =
      activeFilter.value === 'all'
      || (activeFilter.value === 'in_transit' && status === 'in_transit')
      || (activeFilter.value === 'pending' && (status === 'pending_pack' || status === 'pending_handoff'))
      || (activeFilter.value === 'done' && ['arrived', 'signed', 'rejected'].includes(status))
    const queryOK = !query || [task.task_id, task.sample_name, task.device_id || '', task.sender || '', task.receiver || '']
      .some((v) => String(v).toLowerCase().includes(query))
    return filterOK && queryOK
  })
})

function displayTaskId(task: Task) {
  return task.task_id
}

function tempRange(task: Task) {
  const min = task.temperature_min
  const max = task.temperature_max
  if (min == null && max == null) return '2~8℃'
  return `${min ?? '—'}~${max ?? '—'}℃`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    tasks.value = await taskService.listTasks()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function openMonitor(task: Task) {
  uni.redirectTo({
    url: `/pages/monitor/index?task_id=${encodeURIComponent(task.task_id)}`,
  })
}

onLoad((query) => {
  highlightId.value = String(query?.task_id || '')
  if (session.requireSession()) load()
})
onShow(() => {
  if (session.isAuthenticated && !loading.value) load()
})
onPullDownRefresh(load)
</script>

<template>
  <view class="page pick-page">
    <view class="pick-hero">
      <view class="eyebrow">LIVE COLD CHAIN</view>
      <view class="hero-title">选择监控任务</view>
      <view class="hero-desc">从可查看运单中选择，进入运输实时监控</view>
    </view>

    <view class="search">
      <input v-model="keyword" placeholder="搜索运单号 / 样本 / 设备" confirm-type="search" />
    </view>

    <view class="filters">
      <text :class="{ active: activeFilter === 'all' }" @tap="activeFilter = 'all'">全部</text>
      <text :class="{ active: activeFilter === 'in_transit' }" @tap="activeFilter = 'in_transit'">运输中</text>
      <text :class="{ active: activeFilter === 'pending' }" @tap="activeFilter = 'pending'">待发出</text>
      <text :class="{ active: activeFilter === 'done' }" @tap="activeFilter = 'done'">已到达</text>
    </view>

    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <StatePanel v-else-if="!filtered.length" state="empty" message="没有可监控的运单，请先创建或绑定任务" />

    <view v-else class="task-list">
      <view
        v-for="task in filtered"
        :key="task.task_id"
        class="task-card"
        :class="{ current: task.task_id === highlightId }"
        @tap="openMonitor(task)"
      >
        <view class="card-top">
          <view class="task-id">{{ displayTaskId(task) }}</view>
          <StatusTag :status="task.status" />
        </view>
        <view class="sample">{{ task.sample_name }}</view>
        <view class="meta">
          <text>{{ task.device_id || '未绑定设备' }}</text>
          <text>{{ tempRange(task) }}</text>
        </view>
        <view class="route">{{ task.sender || '发货方' }} → {{ task.receiver || '接收方' }}</view>
        <view class="card-foot">
          <text class="status-text">{{ taskStatusText[task.status] }}</text>
          <text class="go">进入监控 ›</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style scoped>
.pick-page {
  min-height: 100vh;
  padding: 0 28rpx 40rpx;
  box-sizing: border-box;
  background: #fbfcf9;
}
.pick-hero {
  margin: 0 -28rpx 24rpx;
  padding: 48rpx 32rpx 40rpx;
  color: #fff;
  background: linear-gradient(135deg, #80d60c, #46a700);
  border-radius: 0 0 38rpx 38rpx;
  box-shadow: 0 16rpx 36rpx rgba(75, 166, 0, 0.22);
}
.eyebrow {
  font-size: 18rpx;
  letter-spacing: 4rpx;
  opacity: 0.72;
}
.hero-title {
  margin-top: 10rpx;
  font-size: 40rpx;
  font-weight: 800;
}
.hero-desc {
  margin-top: 12rpx;
  font-size: 22rpx;
  opacity: 0.85;
}
.search {
  height: 80rpx;
  margin-bottom: 18rpx;
  padding: 0 28rpx;
  border: 1rpx solid #dfe5da;
  border-radius: 999rpx;
  background: #fff;
}
.search input {
  width: 100%;
  height: 80rpx;
  font-size: 25rpx;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  margin-bottom: 20rpx;
}
.filters text {
  padding: 10rpx 24rpx;
  border: 1rpx solid #e1e6dc;
  border-radius: 999rpx;
  color: #687164;
  font-size: 22rpx;
}
.filters .active {
  color: #fff;
  border-color: #55b500;
  background: linear-gradient(90deg, #79d900, #4caf00);
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.task-card {
  padding: 24rpx 26rpx;
  border: 1rpx solid #e6eadf;
  border-radius: 22rpx;
  background: #fff;
  box-shadow: 0 8rpx 22rpx rgba(48, 94, 12, 0.05);
}
.task-card.current {
  border-color: #7ecf3a;
  box-shadow: 0 8rpx 22rpx rgba(84, 173, 6, 0.14);
}
.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}
.task-id {
  color: #1a2a14;
  font-size: 28rpx;
  font-weight: 780;
}
.sample {
  margin-top: 12rpx;
  color: #3d4738;
  font-size: 26rpx;
  font-weight: 650;
}
.meta {
  display: flex;
  gap: 20rpx;
  margin-top: 10rpx;
  color: #6f7b68;
  font-size: 22rpx;
}
.route {
  margin-top: 10rpx;
  color: #52604d;
  font-size: 22rpx;
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18rpx;
  padding-top: 16rpx;
  border-top: 1rpx dashed #e5eadf;
}
.status-text {
  color: #7a8177;
  font-size: 22rpx;
}
.go {
  color: #54ad06;
  font-size: 24rpx;
  font-weight: 700;
}
</style>
