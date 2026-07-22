<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatusTag from '@/components/StatusTag.vue'
import StatePanel from '@/components/StatePanel.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task, Telemetry } from '@/types/api'
import { canStartTask, formatTime } from '@/utils/status'

const taskId = ref('')
const task = ref<Task | null>(null)
const telemetry = ref<Telemetry | null>(null)
const loading = ref(true)
const error = ref('')
const canStart = computed(() => task.value ? canStartTask(task.value.status) : false)
const canAccept = computed(() => task.value ? ['in_transit', 'arrived'].includes(task.value.status) : false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    [task.value, telemetry.value] = await Promise.all([
      taskService.getTask(taskId.value),
      taskService.getLatestTelemetry(taskId.value),
    ])
  } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false; uni.stopPullDownRefresh() }
}

function handoff() {
  uni.navigateTo({ url: `/pages/handoff/index?task_id=${encodeURIComponent(taskId.value)}` })
}

function openPage(page: 'monitor' | 'alarms' | 'trace' | 'acceptance') {
  uni.navigateTo({ url: `/pages/${page}/index?task_id=${encodeURIComponent(taskId.value)}` })
}

onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (!taskId.value) { error.value = '缺少 task_id'; loading.value = false; return }
  load()
})
onPullDownRefresh(load)
onShow(() => { if (taskId.value && !loading.value) load() })
</script>

<template>
  <view class="page detail-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />

    <template v-else-if="task">
      <view class="detail-tabs"><text class="active">概览</text><text @tap="openPage('monitor')">实时数据</text><text @tap="openPage('alarms')">异常</text><text @tap="openPage('trace')">责任链</text><text @tap="openPage('trace')">报告</text></view>
      <view class="card hero-card">
        <view class="accent" />
        <view class="row hero-top">
          <view class="task-code">任务 {{ task.task_id }}</view>
          <StatusTag :status="task.status" />
        </view>
        <view class="sample-name">{{ task.status === 'in_transit' ? '运输中' : task.sample_name }}</view>
        <view class="hero-route">{{ task.sender }} → {{ task.receiver }}</view>

        <view class="metric-grid">
          <view class="metric-box">
            <view class="metric-label">温度</view>
            <view class="metric-value">{{ telemetry ? telemetry.temperature : '--' }}<text v-if="telemetry" class="unit">℃</text></view>
            <view class="metric-status">{{ telemetry?.temp_status || '等待设备上传' }}</view>
          </view>
          <view class="divider" />
          <view class="metric-box">
            <view class="metric-label">湿度</view>
            <view class="metric-value">{{ telemetry ? telemetry.humidity : '--' }}<text v-if="telemetry" class="unit">%</text></view>
            <view class="metric-status">{{ telemetry ? '实时数据' : '等待设备上传' }}</view>
          </view>
        </view>

        <view class="route-line-text">
          <text>{{ task.sender }}</text><text class="arrow">→</text><text>{{ task.receiver }}</text>
        </view>
        <view class="updated">更新于 {{ formatTime(task.updated_at) }}</view>
      </view>

      <view v-if="!telemetry" class="empty-strip">暂无设备数据，等待设备上传…</view>

      <view class="card status-card">
        <view class="section-heading">
          <view class="section-title">设备状态</view>
          <view class="refresh" @tap="load">刷新</view>
        </view>
        <view class="status-grid">
          <view class="status-box"><view class="box-label">箱体</view><view class="box-value">{{ telemetry?.box_status || '--' }}</view><view class="box-hint">{{ telemetry ? '实时状态' : '—' }}</view></view>
          <view class="status-box"><view class="box-label">运动</view><view class="box-value">{{ telemetry?.move_status || '--' }}</view><view class="box-hint">{{ telemetry ? '实时状态' : '—' }}</view></view>
          <view class="status-box"><view class="box-label">设备</view><view class="box-value">{{ task.device_id }}</view><view class="box-hint">承运 {{ task.carrier }}</view></view>
        </view>
      </view>

      <view class="card info-card">
        <view class="section-heading"><view class="section-title">交接信息</view><view class="section-hint">任务档案</view></view>
        <view class="info-row"><view class="info-icon purple">发</view><view><view class="info-label">发出单位</view><view class="info-value">{{ task.sender }}</view></view></view>
        <view class="info-row"><view class="info-icon green">收</view><view><view class="info-label">接收单位</view><view class="info-value">{{ task.receiver }}</view></view></view>
        <view class="info-row"><view class="info-icon blue">时</view><view><view class="info-label">发出时间</view><view class="info-value">{{ formatTime(task.started_at) }}</view></view></view>
      </view>

      <view class="card workspace-card">
        <view class="section-heading"><view class="section-title">任务工作台</view><view class="section-hint">全程闭环</view></view>
        <view class="workspace-grid">
          <view class="workspace-item" @tap="openPage('monitor')"><view class="workspace-icon purple">监</view><view class="workspace-title">实时监控</view><view class="workspace-desc">趋势与设备状态</view></view>
          <view class="workspace-item" @tap="openPage('alarms')"><view class="workspace-icon orange">警</view><view class="workspace-title">异常告警</view><view class="workspace-desc">温度、开箱、碰撞</view></view>
          <view class="workspace-item" @tap="openPage('trace')"><view class="workspace-icon blue">链</view><view class="workspace-title">责任追溯</view><view class="workspace-desc">交接节点与报告</view></view>
          <view class="workspace-item" @tap="openPage('acceptance')"><view class="workspace-icon green">验</view><view class="workspace-title">到达验收</view><view class="workspace-desc">风险建议与签收</view></view>
        </view>
      </view>

      <button v-if="canStart" class="primary action-button" @tap="handoff">进入发出交接</button>
      <button v-else-if="canAccept" class="primary action-button" @tap="openPage('acceptance')">进入到达验收</button>
    </template>
  </view>
</template>

<style scoped>
.detail-page { background: #f4f7fb; }.hero-card { padding: 32rpx 34rpx 30rpx 42rpx; }.accent { position: absolute; left: 0; top: 0; bottom: 0; width: 8rpx; background: #6558ff; }.hero-top { margin-bottom: 5rpx; }.task-code { color: #8b9bb0; font-size: 24rpx; }.sample-name { color: #102a43; font-size: 40rpx; font-weight: 800; line-height: 1.35; }
.metric-grid { display: grid; grid-template-columns: 1fr 1rpx 1fr; gap: 28rpx; margin: 36rpx 0 30rpx; }.divider { background: #e8edf4; }.metric-label { margin-bottom: 12rpx; color: #8a9aaf; font-size: 24rpx; }.metric-value { color: #17334d; font-size: 44rpx; font-weight: 760; }.unit { margin-left: 6rpx; color: #8898ac; font-size: 24rpx; font-weight: 500; }.metric-status { margin-top: 8rpx; color: #a1adbd; font-size: 21rpx; }
.route-line-text { display: flex; align-items: center; gap: 14rpx; color: #63778f; font-size: 27rpx; }.arrow { color: #9caabd; }.updated { margin-top: 10rpx; color: #a6b1c0; font-size: 22rpx; }.empty-strip { margin: 0 0 22rpx; padding: 20rpx 24rpx; border-radius: 16rpx; color: #8999ae; background: #eef2f8; font-size: 24rpx; }
.refresh { color: #6558ff; font-size: 25rpx; }.status-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14rpx; }.status-box { min-width: 0; min-height: 140rpx; padding: 22rpx 18rpx; box-sizing: border-box; border-radius: 18rpx; background: #f2f5fa; }.box-label { color: #8e9db0; font-size: 23rpx; }.box-value { margin: 14rpx 0 6rpx; overflow: hidden; color: #455a72; font-size: 27rpx; font-weight: 680; text-overflow: ellipsis; white-space: nowrap; }.box-hint { overflow: hidden; color: #a1adbd; font-size: 20rpx; text-overflow: ellipsis; white-space: nowrap; }
.info-row { display: flex; align-items: center; gap: 20rpx; padding: 20rpx 0; border-top: 1rpx solid #eef1f6; }.info-icon { display: flex; align-items: center; justify-content: center; width: 62rpx; height: 62rpx; flex: 0 0 auto; border-radius: 18rpx; font-size: 23rpx; font-weight: 720; }.purple { color: #6255f6; background: #efedff; }.green { color: #29996b; background: #e8f7ef; }.blue { color: #247ca7; background: #e7f5fb; }.info-label { color: #98a6b7; font-size: 22rpx; }.info-value { margin-top: 4rpx; color: #41566e; font-size: 27rpx; font-weight: 620; }.action-button { margin-top: 30rpx; }
.workspace-grid { display:grid; grid-template-columns:1fr 1fr; gap:14rpx; }.workspace-item { padding:20rpx; border:1rpx solid #edf0f5; border-radius:20rpx; background:#f8f9fc; }.workspace-icon { display:flex; align-items:center; justify-content:center; width:48rpx; height:48rpx; margin-bottom:12rpx; border-radius:15rpx; font-size:20rpx; font-weight:750; }.workspace-icon.orange { color:#bd7720; background:#fff0dc; }.workspace-title { color:#40566e; font-size:24rpx; font-weight:690; }.workspace-desc { margin-top:4rpx; color:#99a6b6; font-size:19rpx; }
.detail-page{background:#fbfcf9}.detail-tabs{display:flex;margin-bottom:20rpx;overflow:hidden;border:1rpx solid #dfe5da;border-radius:999rpx;background:#fff}.detail-tabs text{flex:1;padding:17rpx 4rpx;color:#6d7569;text-align:center;font-size:20rpx}.detail-tabs .active{color:#fff;border-radius:999rpx;background:linear-gradient(90deg,#79d70d,#4aad00)}.hero-card{color:#fff;border:0;background:radial-gradient(circle at 80% 20%,rgba(220,255,144,.35),transparent 260rpx),linear-gradient(135deg,#7fd20c,#48a900);box-shadow:0 15rpx 35rpx rgba(74,162,0,.2)}.accent{display:none}.hero-card .task-code,.hero-card .metric-label,.hero-card .metric-status,.hero-card .updated{color:rgba(255,255,255,.72)}.hero-card .sample-name,.hero-card .metric-value{color:#fff}.hero-card .unit,.hero-card .route-line-text,.hero-card .arrow{color:rgba(255,255,255,.85)}.hero-route{margin-top:5rpx;font-size:24rpx;opacity:.9}.refresh{color:#53ad05}.purple{color:#54ad06;background:#eff9e7}
</style>
