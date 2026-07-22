<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task, Telemetry } from '@/types/api'
import { formatTime } from '@/utils/status'

const taskId = ref('')
const task = ref<Task | null>(null)
const latest = ref<Telemetry | null>(null)
const history = ref<Telemetry[]>([])
const alarmCount = ref(0)
const loading = ref(true)
const error = ref('')

const chartItems = computed(() => [...history.value].reverse().slice(-18))
const temperatures = computed(() => chartItems.value.map((item) => item.temperature))
const minTemp = computed(() => temperatures.value.length ? Math.min(...temperatures.value) : null)
const maxTemp = computed(() => temperatures.value.length ? Math.max(...temperatures.value) : null)
const isFresh = computed(() => latest.value ? Date.now() - new Date(latest.value.created_at).getTime() < 10 * 60_000 : false)

function barHeight(value: number) {
  const values = temperatures.value
  if (!values.length) return 30
  const min = Math.min(...values, 2)
  const max = Math.max(...values, 8)
  return Math.round(28 + ((value - min) / Math.max(max - min, .1)) * 118)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [taskData, latestData, historyData, alarmData] = await Promise.all([
      taskService.getTask(taskId.value), taskService.getLatestTelemetry(taskId.value),
      taskService.getTelemetryHistory(taskId.value, 60), taskService.getAlarms(taskId.value, 100),
    ])
    task.value = taskData
    latest.value = latestData
    history.value = historyData.items
    alarmCount.value = alarmData.items.length
  } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false; uni.stopPullDownRefresh() }
}

function open(path: string) { uni.navigateTo({ url: `/pages/${path}/index?task_id=${encodeURIComponent(taskId.value)}` }) }

onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (taskId.value) load()
  else { loading.value = false; error.value = '缺少 task_id' }
})
onPullDownRefresh(load)
onShow(() => { if (taskId.value && !loading.value) load() })
</script>

<template>
  <view class="page monitor-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <template v-else-if="task">
      <view class="monitor-hero">
        <view class="hero-row"><view><view class="eyebrow">LIVE COLD CHAIN</view><view class="hero-title">运输实时监控</view></view><StatusTag :status="task.status" /></view>
        <view class="hero-code">{{ task.task_id }} · {{ task.device_id }}</view>
        <view class="online-pill" :class="{ offline: !isFresh }"><view class="online-dot" />{{ isFresh ? '设备在线' : '数据可能延迟' }}</view>
      </view>

      <view class="metric-card">
        <view class="primary-metric"><view class="metric-caption">箱内温度</view><view class="temperature">{{ latest?.temperature ?? '--' }}<text>℃</text></view><view class="range">建议范围 2℃—8℃</view></view>
        <view class="metric-side">
          <view><view class="side-label">湿度</view><view class="side-value">{{ latest?.humidity ?? '--' }}%</view></view>
          <view><view class="side-label">更新</view><view class="side-time">{{ formatTime(latest?.created_at || null) }}</view></view>
        </view>
      </view>

      <view class="card chart-card">
        <view class="section-heading"><view><view class="section-title">温度趋势</view><view class="section-subtitle">最近 {{ chartItems.length }} 个监测点</view></view><view class="chart-range">{{ minTemp ?? '--' }}—{{ maxTemp ?? '--' }}℃</view></view>
        <view v-if="chartItems.length" class="chart">
          <view class="limit-line"><text>8℃</text></view>
          <view v-for="item in chartItems" :key="item.id" class="bar-wrap">
            <view class="bar" :class="{ alert: item.temperature > 8 }" :style="{ height: `${barHeight(item.temperature)}rpx` }" />
          </view>
        </view>
        <view v-else class="chart-empty">暂无历史数据</view>
        <view class="chart-foot"><text>较早</text><text>最新</text></view>
      </view>

      <view class="card state-card">
        <view class="section-heading"><view class="section-title">运输状态</view><view class="refresh" @tap="load">刷新</view></view>
        <view class="state-grid">
          <view class="state-item"><view class="state-icon purple">箱</view><view class="state-label">箱体</view><view class="state-value">{{ latest?.box_status === 'BOX_CLOSED' ? '已关闭' : '已开启' }}</view></view>
          <view class="state-item"><view class="state-icon blue">动</view><view class="state-label">运动</view><view class="state-value">{{ latest?.move_status || '--' }}</view></view>
          <view class="state-item" @tap="open('alarms')"><view class="state-icon orange">警</view><view class="state-label">异常</view><view class="state-value">{{ alarmCount }} 条</view></view>
        </view>
      </view>

      <view class="card route-map"><view class="map-road road-a"/><view class="map-road road-b"/><view class="map-road road-c"/><view class="route-path"/><view class="map-point start">起</view><view class="map-point current">←</view><view class="map-point end">终</view><view class="map-caption left">上海仓库</view><view class="map-caption right">杭州医院</view><view class="map-legend">● 起点　● 终点<br/>★ 当前位置　<span>!</span> 告警点</view></view>

      <view class="action-grid">
        <view class="action-card" @tap="open('alarms')"><view class="action-icon warning">!</view><view><view class="action-title">查看异常</view><view class="action-desc">定位温度、开箱与碰撞</view></view><text>›</text></view>
        <view class="action-card" @tap="open('trace')"><view class="action-icon trace">⌁</view><view><view class="action-title">责任追溯</view><view class="action-desc">查看交接节点与报告</view></view><text>›</text></view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.monitor-page { background: linear-gradient(180deg, #e9efff 0, #f5f7fb 470rpx); }.monitor-hero { margin: -28rpx -28rpx 24rpx; padding: 48rpx 32rpx 38rpx; color: #fff; background: linear-gradient(135deg,#7567ff,#4f72d9); border-radius: 0 0 38rpx 38rpx; box-shadow: 0 16rpx 36rpx rgba(74,76,184,.22); }.hero-row { display:flex; align-items:flex-start; justify-content:space-between; gap:20rpx; }.eyebrow { font-size:18rpx; letter-spacing:4rpx; opacity:.72; }.hero-title { margin-top:9rpx; font-size:40rpx; font-weight:800; }.hero-code { margin-top:16rpx; font-size:22rpx; opacity:.78; }.online-pill { display:inline-flex; align-items:center; gap:9rpx; margin-top:24rpx; padding:10rpx 16rpx; border-radius:999rpx; background:rgba(255,255,255,.16); font-size:21rpx; }.online-dot { width:10rpx; height:10rpx; border-radius:50%; background:#7ff0bd; box-shadow:0 0 0 6rpx rgba(127,240,189,.14); }.offline .online-dot { background:#ffd071; }
.metric-card { display:grid; grid-template-columns:1.25fr 1fr; margin-bottom:22rpx; overflow:hidden; border-radius:28rpx; background:#fff; box-shadow:0 12rpx 30rpx rgba(38,54,88,.09); }.primary-metric { padding:30rpx; background:linear-gradient(145deg,#fff,#f8f7ff); }.metric-caption,.side-label { color:#91a1b5; font-size:22rpx; }.temperature { margin:10rpx 0 6rpx; color:#182f48; font-size:62rpx; font-weight:820; }.temperature text { margin-left:5rpx; color:#8493a6; font-size:25rpx; }.range { color:#6f63f7; font-size:20rpx; }.metric-side { display:flex; flex-direction:column; justify-content:space-around; padding:24rpx 26rpx; border-left:1rpx solid #edf0f5; }.side-value { margin-top:7rpx; color:#40566e; font-size:31rpx; font-weight:720; }.side-time { margin-top:7rpx; color:#5a6f86; font-size:20rpx; line-height:1.45; }
.section-subtitle { margin-top:5rpx; color:#a0adbd; font-size:20rpx; }.chart-range { color:#6659f4; font-size:22rpx; font-weight:700; }.chart { position:relative; display:flex; align-items:flex-end; gap:8rpx; height:190rpx; padding:18rpx 4rpx 0; border-bottom:1rpx solid #e5eaf1; }.bar-wrap { z-index:1; display:flex; align-items:flex-end; flex:1; height:100%; }.bar { width:100%; min-height:18rpx; border-radius:8rpx 8rpx 3rpx 3rpx; background:linear-gradient(180deg,#8a80ff,#6558ff); }.bar.alert { background:linear-gradient(180deg,#ff8f98,#dd4c58); }.limit-line { position:absolute; left:0; right:0; top:36rpx; border-top:2rpx dashed #efadb3; }.limit-line text { position:absolute; right:0; top:-25rpx; color:#d96670; font-size:18rpx; }.chart-foot { display:flex; justify-content:space-between; margin-top:11rpx; color:#a5b0bd; font-size:19rpx; }.chart-empty { padding:50rpx 0; color:#9aa8ba; text-align:center; }
.refresh { color:#6558ff; font-size:23rpx; }.state-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:13rpx; }.state-item { padding:20rpx 12rpx; border-radius:20rpx; background:#f5f7fb; text-align:center; }.state-icon { display:flex; align-items:center; justify-content:center; width:52rpx; height:52rpx; margin:0 auto 12rpx; border-radius:16rpx; font-size:20rpx; font-weight:750; }.purple { color:#6558ff; background:#eae7ff; }.blue { color:#2f83b7; background:#e5f3fb; }.orange { color:#bd7720; background:#fff0dc; }.state-label { color:#95a3b4; font-size:20rpx; }.state-value { margin-top:6rpx; overflow:hidden; color:#40566e; font-size:23rpx; font-weight:680; text-overflow:ellipsis; white-space:nowrap; }
.action-grid { display:grid; gap:16rpx; }.action-card { display:grid; grid-template-columns:58rpx 1fr 20rpx; align-items:center; gap:17rpx; padding:22rpx; border:1rpx solid #e7ebf2; border-radius:23rpx; background:#fff; box-shadow:0 8rpx 22rpx rgba(38,54,88,.06); }.action-icon { display:flex; align-items:center; justify-content:center; width:58rpx; height:58rpx; border-radius:18rpx; font-size:29rpx; font-weight:800; }.warning { color:#c97723; background:#fff0dc; }.trace { color:#6558ff; background:#ece9ff; }.action-title { color:#334b63; font-size:26rpx; font-weight:700; }.action-desc { margin-top:4rpx; color:#9ca8b8; font-size:20rpx; }.action-card>text { color:#a6b1bf; font-size:36rpx; }
.monitor-page{background:#fbfcf9}.monitor-hero{background:linear-gradient(135deg,#80d60c,#46a700);box-shadow:0 16rpx 36rpx rgba(75,166,0,.22)}.range,.chart-range,.refresh{color:#54ad06}.bar{background:linear-gradient(180deg,#9be53e,#51ad08)}.purple,.trace{color:#54ad06;background:#eff9e7}.route-map{position:relative;height:285rpx;padding:0;background:linear-gradient(135deg,#f4f6ee,#eef4e8)}.map-road{position:absolute;height:18rpx;border-radius:20rpx;background:#fff;transform-origin:left center}.road-a{left:-20rpx;top:95rpx;width:680rpx;transform:rotate(-12deg)}.road-b{left:130rpx;top:20rpx;width:450rpx;transform:rotate(65deg)}.road-c{left:260rpx;top:210rpx;width:390rpx;transform:rotate(-48deg)}.route-path{position:absolute;left:65rpx;right:75rpx;top:142rpx;height:8rpx;border-radius:20rpx;background:#51ad08;transform:rotate(5deg)}.map-point{position:absolute;z-index:2;display:flex;align-items:center;justify-content:center;width:43rpx;height:43rpx;border:6rpx solid #fff;border-radius:50%;color:#fff;background:#54b006;font-size:18rpx;font-weight:800}.map-point.start{left:55rpx;top:103rpx}.map-point.current{left:330rpx;top:132rpx;width:58rpx;height:58rpx;color:#184600;background:#d8ff9d}.map-point.end{right:58rpx;top:157rpx}.map-caption{position:absolute;z-index:2;color:#31402b;font-size:20rpx}.map-caption.left{left:35rpx;top:165rpx}.map-caption.right{right:28rpx;top:214rpx}.map-legend{position:absolute;right:15rpx;top:15rpx;padding:12rpx;border-radius:12rpx;background:rgba(255,255,255,.92);font-size:16rpx;line-height:1.8}.map-legend span{color:#e33b42}
</style>
