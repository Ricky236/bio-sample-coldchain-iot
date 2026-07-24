<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { AlarmEvent, Task } from '@/types/api'
import { formatTime } from '@/utils/status'

const taskId = ref('')
const task = ref<Task | null>(null)
const alarms = ref<AlarmEvent[]>([])
const loading = ref(true)
const error = ref('')
const filter = ref<'all' | 'temperature' | 'box' | 'motion'>('all')

const filtered = computed(() => alarms.value.filter((item) => {
  if (filter.value === 'all') return true
  if (filter.value === 'temperature') return item.event_type === 'TEMP_ALERT'
  if (filter.value === 'box') return item.event_type === 'BOX_OPEN'
  return ['MILD', 'SEVERE', 'IMPACT', 'FREE_FALL'].includes(item.event_type)
}))
const filters = [
  { value: 'all', label: '全部' }, { value: 'temperature', label: '温度' },
  { value: 'box', label: '开箱' }, { value: 'motion', label: '碰撞' },
] as const

const openCount = computed(() => alarms.value.filter((item) => (item.alarm_status || 'new') === 'new').length)
const severeCount = computed(() => alarms.value.filter((item) => ['IMPACT', 'FREE_FALL', 'SEVERE', 'TEMP_ALERT'].includes(item.event_type)).length)
const todayCount = computed(() => {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const startMs = start.getTime()
  return alarms.value.filter((item) => {
    const ts = new Date(item.timestamp || item.created_at || 0).getTime()
    return Number.isFinite(ts) && ts >= startMs
  }).length
})

function tone(type: string) {
  if (type === 'TEMP_ALERT') return 'red'
  if (type === 'BOX_OPEN') return 'orange'
  return 'purple'
}
function icon(type: string) {
  if (type === 'TEMP_ALERT') return '温'
  if (type === 'BOX_OPEN') return '箱'
  return '震'
}
function level(type: string) {
  return ['IMPACT', 'FREE_FALL', 'SEVERE'].includes(type) ? '严重' : type === 'TEMP_ALERT' ? '需复核' : '一般'
}
function sourceLabel(_item: AlarmEvent) {
  return '公网同步'
}
function statusLabel(item: AlarmEvent) {
  if (item.alarm_status === 'resolved') return '已关闭'
  if (item.alarm_status === 'acknowledged') return '处置中'
  return '未处理'
}

async function load(options: { silent?: boolean } = {}) {
  const silent = Boolean(options.silent)
  if (!silent) {
    loading.value = true
    error.value = ''
  }
  try {
    const [taskData, alarmData] = await Promise.all([
      taskService.getTask(taskId.value, silent ? { showLoading: false } : {}),
      taskService.getAlarms(taskId.value, 100, silent ? { showLoading: false } : {}),
    ])
    task.value = taskData
    alarms.value = alarmData.items || []
    if (!silent) error.value = ''
  } catch (e) {
    if (!silent || !task.value) error.value = errorMessage(e)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}
function openAlarm(item: AlarmEvent) {
  uni.navigateTo({
    url: `/pages/alarm-detail/index?task_id=${encodeURIComponent(taskId.value)}&alarm_id=${item.id}`,
  })
}

onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (taskId.value) load()
  else { loading.value = false; error.value = '缺少 task_id' }
})
onPullDownRefresh(() => load())
onShow(() => {
  if (taskId.value && !loading.value) load({ silent: Boolean(task.value) })
})
</script>

<template>
  <view class="page alarm-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <template v-else-if="task">
      <view class="alarm-title">
        <view>
          <b>告警中心</b>
          <text>{{ task.task_id }} · {{ task.device_id || '未绑定设备' }}</text>
        </view>
        <view>♧</view>
      </view>
      <view class="stat-grid">
        <view class="stat-item">
          <view class="stat-icon">!</view>
          <view class="stat-copy">
            <text class="stat-label">未处理</text>
            <text class="stat-value">{{ openCount }}</text>
          </view>
        </view>
        <view class="stat-item">
          <view class="stat-icon">⚡</view>
          <view class="stat-copy">
            <text class="stat-label">严重/复核</text>
            <text class="stat-value">{{ severeCount }}</text>
          </view>
        </view>
        <view class="stat-item">
          <view class="stat-icon">▣</view>
          <view class="stat-copy">
            <text class="stat-label">今日告警</text>
            <text class="stat-value">{{ todayCount }}</text>
          </view>
        </view>
      </view>

      <scroll-view scroll-x class="filter-scroll" :show-scrollbar="false">
        <view class="filter-row">
          <view
            v-for="item in filters"
            :key="item.value"
            class="filter-chip"
            :class="{ active: filter === item.value }"
            @tap="filter = item.value"
          >{{ item.label }}</view>
        </view>
      </scroll-view>

      <StatePanel v-if="!filtered.length" state="empty" message="当前没有异常记录；设备上报温度/开箱/碰撞后将显示在这里" />
      <view v-else class="alarm-list">
        <view v-for="item in filtered" :key="item.id" class="alarm-card" @tap="openAlarm(item)">
          <view class="alarm-head">
            <view class="alarm-icon" :class="tone(item.event_type)">{{ icon(item.event_type) }}</view>
            <view class="alarm-main">
              <view class="alarm-name">{{ item.event_name }}</view>
              <view class="alarm-time">{{ formatTime(item.timestamp) }} · {{ sourceLabel(item) }}</view>
            </view>
            <view class="level" :class="tone(item.event_type)">{{ level(item.event_type) }}</view>
          </view>
          <view class="alarm-detail">{{ item.event_detail || item.description }}</view>
          <view class="alarm-meta">
            <text class="meta-device">设备 {{ item.device_id }} · {{ statusLabel(item) }}</text>
            <text class="dispose">去处置 ›</text>
          </view>
        </view>
      </view>

      <view class="notice">
        <view class="notice-icon">i</view>
        <view>
          <view class="notice-title">数据来源</view>
          <view class="notice-text">设备 {{ task.device_id || '—' }} 的公网异常会同步到本地；确认与闭环使用本地处置逻辑。</view>
        </view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.alarm-page {
  background: radial-gradient(circle at 70% 0, rgba(191, 255, 85, 0.18), transparent 320rpx), #fbfcf9;
}
.alarm-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 5rpx 10rpx 24rpx;
}
.alarm-title b {
  display: block;
  font-size: 43rpx;
  color: #183115;
}
.alarm-title text {
  display: block;
  margin-top: 7rpx;
  color: #70796c;
  font-size: 22rpx;
}
.alarm-title > view:last-child {
  color: #52ad05;
  font-size: 52rpx;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 15rpx;
  margin-bottom: 22rpx;
}
.stat-item {
  display: flex;
  align-items: center;
  gap: 12rpx;
  min-width: 0;
  padding: 20rpx;
  border: 1rpx solid #e1e8dc;
  border-radius: 22rpx;
  background: #fff;
}
.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 49rpx;
  height: 49rpx;
  flex: 0 0 auto;
  border-radius: 15rpx;
  color: #55b005;
  background: #eff9e7;
  font-size: 22rpx;
  font-weight: 750;
}
.stat-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.stat-label {
  color: #626b5e;
  font-size: 19rpx;
  line-height: 1.2;
}
.stat-value {
  margin-top: 6rpx;
  color: #1e3420;
  font-size: 31rpx;
  font-weight: 760;
  line-height: 1.1;
}
.filter-scroll {
  height: 76rpx;
  margin: 25rpx 0 22rpx;
  white-space: nowrap;
}
.filter-row {
  display: flex;
  gap: 13rpx;
}
.filter-chip {
  padding: 14rpx 26rpx;
  border: 1rpx solid #e3e8ef;
  border-radius: 999rpx;
  color: #7c8ca0;
  background: #fff;
  font-size: 23rpx;
}
.filter-chip.active {
  color: #fff;
  border-color: #55b006;
  background: #55b006;
  box-shadow: 0 8rpx 18rpx rgba(81, 172, 0, 0.2);
}
.alarm-list {
  display: grid;
  gap: 17rpx;
}
.alarm-card {
  padding: 25rpx;
  border: 1rpx solid #e7ebf1;
  border-radius: 24rpx;
  background: #fff;
  box-shadow: 0 9rpx 24rpx rgba(38, 54, 88, 0.07);
}
.alarm-head {
  display: flex;
  align-items: center;
  gap: 17rpx;
}
.alarm-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 60rpx;
  height: 60rpx;
  flex: 0 0 auto;
  border-radius: 19rpx;
  font-size: 22rpx;
  font-weight: 780;
}
.red {
  color: #d44c57;
  background: #ffeaec;
}
.orange {
  color: #bc7624;
  background: #fff0dc;
}
.purple {
  color: #6558ff;
  background: #ece9ff;
}
.alarm-main {
  flex: 1;
  min-width: 0;
}
.alarm-name {
  color: #314a62;
  font-size: 28rpx;
  font-weight: 730;
}
.alarm-time {
  margin-top: 5rpx;
  color: #9aa7b7;
  font-size: 20rpx;
}
.level {
  flex: 0 0 auto;
  padding: 7rpx 14rpx;
  border-radius: 999rpx;
  font-size: 19rpx;
  font-weight: 680;
}
.alarm-detail {
  margin: 19rpx 0;
  padding: 18rpx 20rpx;
  border-radius: 16rpx;
  color: #5f7389;
  background: #f6f8fb;
  font-size: 23rpx;
  line-height: 1.55;
}
.alarm-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}
.meta-device {
  flex: 1;
  min-width: 0;
  color: #9aa7b7;
  font-size: 19rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dispose {
  flex: 0 0 auto;
  color: #55ae08;
  font-size: 21rpx;
  font-weight: 700;
}
.notice {
  display: flex;
  gap: 16rpx;
  margin-top: 22rpx;
  padding: 20rpx;
  border-radius: 20rpx;
  color: #687b91;
  background: #eef2f8;
}
.notice-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38rpx;
  height: 38rpx;
  flex: 0 0 auto;
  border-radius: 50%;
  color: #fff;
  background: #8496aa;
  font-weight: 750;
}
.notice-title {
  font-size: 22rpx;
  font-weight: 680;
}
.notice-text {
  margin-top: 5rpx;
  font-size: 20rpx;
  line-height: 1.5;
}
</style>
