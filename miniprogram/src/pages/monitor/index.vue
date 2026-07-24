<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { onHide, onLoad, onPullDownRefresh, onShow, onReady } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task, Telemetry } from '@/types/api'
import { formatTime } from '@/utils/status'

const POLL_MS = 8000
const PAD = { left: 36, right: 12, top: 12, bottom: 28 }

type RangeKey = '1h' | '6h' | 'all'
const rangeKey = ref<RangeKey>('1h')
const rangeOptions: { key: RangeKey; label: string }[] = [
  { key: '1h', label: '1小时' },
  { key: '6h', label: '6小时' },
  { key: 'all', label: '全程' },
]

const taskId = ref('')
const task = ref<Task | null>(null)
const latest = ref<Telemetry | null>(null)
const history = ref<Telemetry[]>([])
const alarmCount = ref(0)
const loading = ref(true)
const error = ref('')
const emptyHint = ref('')
const canvasReady = ref(false)
const canvasW = ref(320)
const canvasH = ref(140)
let timer: ReturnType<typeof setInterval> | null = null
let polling = false
let lastChartKey = ''
let drawTimer: ReturnType<typeof setTimeout> | null = null

/** 运单设定温控；未设置时不画安全带，文案显示「未设置」 */
const safeMin = computed(() => {
  const value = task.value?.temperature_min
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})
const safeMax = computed(() => {
  const value = task.value?.temperature_max
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})
const rangeLabel = computed(() => {
  if (safeMin.value == null || safeMax.value == null) return '未设置温控范围'
  return `${safeMin.value}℃—${safeMax.value}℃`
})
const rangeShort = computed(() => {
  if (safeMin.value == null || safeMax.value == null) return '未设置'
  return `${safeMin.value}~${safeMax.value}°C`
})

function pointTime(item: Telemetry) {
  const raw = item.timestamp || item.created_at
  const ts = new Date(raw).getTime()
  return Number.isNaN(ts) ? 0 : ts
}

function downsample(items: Telemetry[], maxPoints: number) {
  if (items.length <= maxPoints) return items
  const result: Telemetry[] = []
  const step = (items.length - 1) / (maxPoints - 1)
  for (let i = 0; i < maxPoints; i += 1) result.push(items[Math.round(i * step)])
  return result
}

function chartKey(items: Telemetry[]) {
  if (!items.length) return ''
  const first = items[0]
  const last = items[items.length - 1]
  return `${items.length}:${first.id}:${last.id}:${last.temperature}:${rangeKey.value}:${yDomain.value.min}:${yDomain.value.max}:${safeMin.value}:${safeMax.value}`
}

const chartItems = computed(() => {
  // Sort by actual sample time — local sync ids are not guaranteed chronological.
  const oldestFirst = [...history.value].sort((a, b) => pointTime(a) - pointTime(b))
  const now = Date.now()
  const filtered = oldestFirst.filter((item) => {
    const ts = pointTime(item)
    if (!ts) return false
    if (rangeKey.value === '1h') return now - ts <= 3600_000
    if (rangeKey.value === '6h') return now - ts <= 6 * 3600_000
    return true
  })
  return downsample(filtered, 48)
})

const yDomain = computed(() => {
  const temps = chartItems.value.map((item) => item.temperature)
  if (!temps.length) {
    const lo = safeMin.value ?? 0
    const hi = safeMax.value ?? 10
    return { min: Math.min(0, Math.floor(lo) - 1), max: Math.max(10, Math.ceil(hi) + 1) }
  }
  const bounds = [...temps]
  if (safeMin.value != null) bounds.push(safeMin.value)
  if (safeMax.value != null) bounds.push(safeMax.value)
  const dataMin = Math.min(...bounds)
  const dataMax = Math.max(...bounds)
  let min = Math.min(0, Math.floor(dataMin) - 1)
  let max = Math.max(10, Math.ceil(dataMax) + 1)
  if (max - min < 10) max = min + 10
  return { min, max }
})

const yTicks = computed(() => {
  const { min, max } = yDomain.value
  const preferred = [0, 2, 5, 8, 10, 15, 20, 25, 30, 32, 35, 38, 40]
  if (safeMin.value != null) preferred.push(safeMin.value)
  if (safeMax.value != null) preferred.push(safeMax.value)
  const ticks = preferred.filter((tick) => tick >= min && tick <= max)
  if (!ticks.includes(min)) ticks.unshift(min)
  if (!ticks.includes(max)) ticks.push(max)
  return [...new Set(ticks)].sort((a, b) => a - b)
})

const xLabels = computed(() => {
  const items = chartItems.value
  if (!items.length) return [] as string[]
  const picks = [0, 0.25, 0.5, 0.75, 1].map((ratio) =>
    items[Math.min(items.length - 1, Math.round((items.length - 1) * ratio))],
  )
  return picks.map((item) => {
    const d = new Date(item.timestamp || item.created_at)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    return `${hh}:${mm}`
  })
})

const isFresh = computed(() =>
  latest.value ? Date.now() - pointTime(latest.value) < 10 * 60_000 : false,
)
const hasTelemetry = computed(() => Boolean(latest.value) || history.value.length > 0)
const boxText = computed(() => {
  if (!latest.value) return '--'
  return latest.value.box_status === 'BOX_CLOSED' ? '已关闭' : latest.value.box_status === 'BOX_OPEN' ? '已开启' : latest.value.box_status
})
const gpsPoints = computed(() => history.value
  .filter((item) => typeof item.lat === 'number' && typeof item.lng === 'number' && Number.isFinite(item.lat) && Number.isFinite(item.lng))
  .map((item) => ({ latitude: Number(item.lat), longitude: Number(item.lng), temperature: item.temperature, id: item.id }))
  .reverse())
const hasGps = computed(() => gpsPoints.value.length > 0)
/** 锁定地图中心，避免轮询微调坐标导致 native map 反复重绘闪烁 */
const mapCenter = ref({ latitude: 31.23, longitude: 121.47 })
const mapReady = ref(false)
const mapPolyline = computed(() => {
  if (gpsPoints.value.length < 2) return [] as Array<Record<string, unknown>>
  return [{
    points: gpsPoints.value.map((p) => ({
      latitude: Number(p.latitude.toFixed(5)),
      longitude: Number(p.longitude.toFixed(5)),
    })),
    color: '#54AD06',
    width: 4,
    dottedLine: false,
  }]
})
const mapMarkers = computed(() => {
  const pts = gpsPoints.value
  if (!pts.length) return [] as Array<Record<string, unknown>>
  const start = pts[0]
  const end = pts[pts.length - 1]
  return [
    {
      id: 1,
      latitude: Number(start.latitude.toFixed(5)),
      longitude: Number(start.longitude.toFixed(5)),
      width: 24,
      height: 24,
      title: '起点',
    },
    {
      id: 2,
      latitude: Number(end.latitude.toFixed(5)),
      longitude: Number(end.longitude.toFixed(5)),
      width: 28,
      height: 28,
      title: '当前位置',
    },
  ]
})

function syncMapCenter(force = false) {
  const pts = gpsPoints.value
  if (!pts.length) return
  const last = pts[pts.length - 1]
  if (!mapReady.value || force) {
    mapCenter.value = { latitude: last.latitude, longitude: last.longitude }
    mapReady.value = true
    return
  }
  const dLat = Math.abs(last.latitude - mapCenter.value.latitude)
  const dLng = Math.abs(last.longitude - mapCenter.value.longitude)
  // 位移很小时不挪地图中心，减少闪烁
  if (dLat > 0.0008 || dLng > 0.0008) {
    mapCenter.value = { latitude: last.latitude, longitude: last.longitude }
  }
}

function historySig(items: Telemetry[]) {
  if (!items.length) return ''
  const first = items[0]
  const last = items[items.length - 1]
  return `${items.length}:${first?.id}:${last?.id}:${last?.temperature}:${last?.timestamp || last?.created_at}`
}

function latestSig(item: Telemetry | null) {
  if (!item) return ''
  return `${item.id}:${item.temperature}:${item.humidity}:${item.timestamp || item.created_at}:${item.box_status}`
}

function yToPx(temp: number) {
  const { min, max } = yDomain.value
  const plotH = canvasH.value - PAD.top - PAD.bottom
  const clamped = Math.min(max, Math.max(min, temp))
  return PAD.top + ((max - clamped) / Math.max(max - min, 0.1)) * plotH
}

function drawTempChart(force = false) {
  if (!canvasReady.value || !chartItems.value.length) return
  const key = chartKey(chartItems.value)
  if (!force && key && key === lastChartKey) return
  lastChartKey = key

  const ctx = uni.createCanvasContext('tempTrendChart')
  const width = canvasW.value
  const height = canvasH.value
  const plotW = width - PAD.left - PAD.right
  const items = chartItems.value
  const { min, max } = yDomain.value

  // 不先 clearRect：draw(false) 会整帧替换，避免先空白再绘制造成闪烁
  if (safeMin.value != null && safeMax.value != null && safeMax.value > min && safeMin.value < max) {
    const safeTop = yToPx(Math.min(safeMax.value, max))
    const safeBottom = yToPx(Math.max(safeMin.value, min))
    ctx.setFillStyle('rgba(125, 211, 30, 0.12)')
    ctx.fillRect(PAD.left, safeTop, plotW, Math.max(0, safeBottom - safeTop))
  }

  yTicks.value.forEach((tick) => {
    const y = yToPx(tick)
    const isSafeBound = tick === safeMin.value || tick === safeMax.value
    ctx.setStrokeStyle(isSafeBound ? 'rgba(84, 173, 6, 0.55)' : 'rgba(170, 182, 168, 0.45)')
    if (typeof ctx.setLineDash === 'function') ctx.setLineDash(isSafeBound ? [6, 5] : [4, 6], 0)
    ctx.setLineWidth(isSafeBound ? 1.5 : 1)
    ctx.beginPath()
    ctx.moveTo(PAD.left, y)
    ctx.lineTo(PAD.left + plotW, y)
    ctx.stroke()
    ctx.setFillStyle('#9aa59a')
    ctx.setFontSize(10)
    ctx.setTextAlign('right')
    ctx.fillText(String(tick), PAD.left - 6, y + 3)
  })
  if (typeof ctx.setLineDash === 'function') ctx.setLineDash([], 0)

  if (items.length === 1) {
    const x = PAD.left + plotW
    const y = yToPx(items[0].temperature)
    ctx.setFillStyle('#54ad06')
    ctx.beginPath()
    ctx.arc(x, y, 4, 0, Math.PI * 2)
    ctx.fill()
  } else {
    ctx.setStrokeStyle('#54ad06')
    ctx.setLineWidth(2.5)
    ctx.setLineCap('round')
    ctx.setLineJoin('round')
    ctx.beginPath()
    items.forEach((item, index) => {
      const x = PAD.left + (index / (items.length - 1)) * plotW
      const y = yToPx(item.temperature)
      if (index === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()

    const last = items[items.length - 1]
    const lx = PAD.left + plotW
    const ly = yToPx(last.temperature)
    ctx.setFillStyle('#54ad06')
    ctx.beginPath()
    ctx.arc(lx, ly, 4.5, 0, Math.PI * 2)
    ctx.fill()
    ctx.setFillStyle('#fff')
    ctx.beginPath()
    ctx.arc(lx, ly, 2, 0, Math.PI * 2)
    ctx.fill()
  }

  xLabels.value.forEach((label, index) => {
    const ratio = xLabels.value.length === 1 ? 1 : index / (xLabels.value.length - 1)
    const x = PAD.left + ratio * plotW
    ctx.setFillStyle('#9aa59a')
    ctx.setFontSize(10)
    ctx.setTextAlign(index === 0 ? 'left' : index === xLabels.value.length - 1 ? 'right' : 'center')
    ctx.fillText(label, x, height - 8)
  })

  ctx.draw()
}

function scheduleDraw(force = false) {
  if (drawTimer) clearTimeout(drawTimer)
  drawTimer = setTimeout(() => {
    drawTimer = null
    drawTempChart(force)
  }, 50)
}

function setRange(key: RangeKey) {
  rangeKey.value = key
  lastChartKey = ''
  scheduleDraw(true)
}

function initCanvasSize() {
  const info = uni.getSystemInfoSync()
  const horizontalPad = (28 + 20) * 2 * (info.windowWidth / 750)
  canvasW.value = Math.max(260, Math.floor(info.windowWidth - horizontalPad))
  canvasH.value = Math.floor(canvasW.value * 0.42)
}

function mapLoadError(e: unknown) {
  const raw = errorMessage(e)
  if (/1033|530|Failed to connect|NETWORK|timeout|超时|连接失败|UNABLE_TO_RESOLVE|request:fail/i.test(raw)) {
    return '网络暂时不可用，请稍后重试'
  }
  return raw
}

async function load(options: { silent?: boolean } = {}) {
  if (!taskId.value) return
  if (options.silent && polling) return
  const silent = Boolean(options.silent)
  const quiet = { showLoading: false as const }
  if (silent) polling = true
  if (!silent) {
    loading.value = true
    error.value = ''
    emptyHint.value = ''
  }
  try {
    // 静默轮询只拉最新值，避免每几秒重拉整段历史导致地图/图表整页闪烁
    if (silent && task.value) {
      const [latestData, hardware] = await Promise.all([
        taskService.getLatestTelemetry(taskId.value, quiet),
        taskService.getHardwareSnapshot(taskId.value).catch(() => null),
      ])
      const nextLatest = (hardware?.matched && hardware.latest) ? hardware.latest : latestData
      if (nextLatest && latestSig(nextLatest) !== latestSig(latest.value)) {
        latest.value = nextLatest
        const last = history.value[0]
        const newer = !last || pointTime(nextLatest) > pointTime(last) || (
          pointTime(nextLatest) === pointTime(last) && nextLatest.temperature !== last.temperature
        )
        if (newer) {
          const withoutDup = history.value.filter((item) => item.id !== nextLatest.id)
          history.value = [nextLatest, ...withoutDup].slice(0, 120)
          scheduleDraw()
          syncMapCenter(false)
        }
      }
      if (hardware?.recent_alarms?.length) {
        alarmCount.value = hardware.recent_alarms.length
      }
      return
    }

    const [taskData, latestData, historyData, alarmData, hardware] = await Promise.all([
      taskService.getTask(taskId.value, quiet),
      taskService.getLatestTelemetry(taskId.value, quiet),
      taskService.getTelemetryHistory(taskId.value, 100, quiet),
      taskService.getAlarms(taskId.value, 100, quiet),
      taskService.getHardwareSnapshot(taskId.value).catch(() => null),
    ])
    task.value = taskData
    const hardwareHistory = hardware?.matched && hardware.latest
      ? (hardware.history?.length ? hardware.history : [hardware.latest])
      : []
    const localHistory = historyData.items || []
    let nextHistory: Telemetry[] = []
    let nextLatest: Telemetry | null = null
    if (hardwareHistory.length >= localHistory.length && hardwareHistory.length > 0) {
      nextLatest = hardware?.latest || latestData
      nextHistory = hardwareHistory
    } else if (localHistory.length > 0) {
      nextLatest = latestData || hardware?.latest || localHistory[0]
      nextHistory = localHistory
    } else if (hardware?.latest || latestData) {
      nextLatest = hardware?.latest || latestData
      nextHistory = hardwareHistory.length ? hardwareHistory : (nextLatest ? [nextLatest] : [])
    }

    const historyChanged = historySig(nextHistory) !== historySig(history.value)
    const latestChanged = latestSig(nextLatest) !== latestSig(latest.value)
    if (historyChanged) history.value = nextHistory
    if (latestChanged) latest.value = nextLatest
    alarmCount.value = hardware?.recent_alarms?.length || alarmData.items?.length || 0
    error.value = ''
    emptyHint.value = (!latest.value && !history.value.length)
      ? '任务已加载，但还没有监测数据。请确认设备已开始上报，或稍后下拉刷新。'
      : ''
    if (historyChanged || !mapReady.value) syncMapCenter(true)
    if (historyChanged || latestChanged) scheduleDraw(true)
  } catch (e) {
    if (!silent || !task.value) error.value = mapLoadError(e)
  } finally {
    loading.value = false
    polling = false
    uni.stopPullDownRefresh()
  }
}

function startPoll() {
  stopPoll()
  timer = setInterval(() => {
    if (taskId.value && !loading.value) load({ silent: true })
  }, POLL_MS)
}
function stopPoll() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function open(path: string) {
  uni.navigateTo({ url: `/pages/${path}/index?task_id=${encodeURIComponent(taskId.value)}` })
}

function switchTask() {
  uni.redirectTo({
    url: `/pages/monitor-pick/index?task_id=${encodeURIComponent(taskId.value)}`,
  })
}

watch([chartItems, canvasReady], () => {
  scheduleDraw()
})

onReady(() => {
  initCanvasSize()
  canvasReady.value = true
  scheduleDraw(true)
})
onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (taskId.value) {
    load().then(startPoll)
  } else {
    uni.redirectTo({ url: '/pages/monitor-pick/index' })
  }
})
onPullDownRefresh(() => {
  mapReady.value = false
  load()
})
onShow(() => {
  // 已有数据时只保证轮询在跑，避免 onShow 反复全量刷新闪烁（地图交互也会触发 onShow）
  if (!taskId.value) return
  if (!task.value) load().then(startPoll)
  else startPoll()
})
onHide(stopPoll)
onUnmounted(() => {
  stopPoll()
  if (drawTimer) clearTimeout(drawTimer)
})
</script>

<template>
  <view class="page monitor-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <template v-else-if="task">
      <view class="monitor-hero">
        <view class="hero-row"><view><view class="eyebrow">LIVE COLD CHAIN</view><view class="hero-title">运输实时监控</view></view><StatusTag :status="task.status" /></view>
        <view class="hero-code" @tap="switchTask">{{ task.task_id }} · {{ task.device_id || '未绑定设备' }} · 切换 ›</view>
        <view class="online-pill" :class="{ offline: !isFresh }"><view class="online-dot" />{{ hasTelemetry ? (isFresh ? '设备在线' : '数据可能延迟') : '等待首条监测' }}</view>
        <view class="switch-task" @tap="switchTask">选择其他任务</view>
      </view>

      <view v-if="emptyHint" class="empty-banner">{{ emptyHint }}</view>

      <view class="metric-card">
        <view class="primary-metric"><view class="metric-caption">箱内温度</view><view class="temperature">{{ latest?.temperature ?? '--' }}<text>℃</text></view><view class="range">建议范围 {{ rangeLabel }}</view></view>
        <view class="metric-side">
          <view><view class="side-label">湿度</view><view class="side-value">{{ latest?.humidity ?? '--' }}%</view></view>
          <view><view class="side-label">更新</view><view class="side-time">{{ formatTime(latest?.timestamp || latest?.created_at || null) }}</view></view>
        </view>
      </view>

      <view class="range-tabs">
        <view
          v-for="option in rangeOptions"
          :key="option.key"
          class="range-tab"
          :class="{ active: rangeKey === option.key }"
          @tap="setRange(option.key)"
        >{{ option.label }}</view>
      </view>

      <view class="card chart-card">
        <view class="trend-head">
          <text class="trend-title">温度趋势 (°C)</text>
          <text class="trend-range">{{ rangeShort }}</text>
        </view>
        <view v-if="chartItems.length" class="trend-canvas-wrap">
          <canvas
            canvas-id="tempTrendChart"
            id="tempTrendChart"
            class="trend-canvas"
            :width="canvasW"
            :height="canvasH"
            :style="{ width: `${canvasW}px`, height: `${canvasH}px` }"
          />
        </view>
        <view v-else class="chart-empty">暂无历史数据</view>
      </view>

      <view class="card state-card">
        <view class="section-heading"><view class="section-title">运输状态</view><view class="refresh" @tap="load()">刷新</view></view>
        <view class="state-grid">
          <view class="state-item"><view class="state-icon purple">箱</view><view class="state-label">箱体</view><view class="state-value">{{ boxText }}</view></view>
          <view class="state-item"><view class="state-icon blue">动</view><view class="state-label">运动</view><view class="state-value">{{ latest?.move_status || '--' }}</view></view>
          <view class="state-item" @tap="open('alarms')"><view class="state-icon orange">警</view><view class="state-label">异常</view><view class="state-value">{{ alarmCount }} 条</view></view>
        </view>
      </view>

      <view class="card route-map">
        <view class="map-head">
          <view class="section-title">运输轨迹</view>
          <view class="map-hint">{{ hasGps ? `已定位 ${gpsPoints.length} 处` : '等待设备上报位置' }}</view>
        </view>
        <map
          v-if="hasGps"
          class="live-map"
          :latitude="mapCenter.latitude"
          :longitude="mapCenter.longitude"
          :scale="14"
          :polyline="mapPolyline"
          :markers="mapMarkers"
          show-location
        />
        <view v-else class="map-fallback">
          <view class="map-road road-a"/><view class="map-road road-b"/><view class="map-road road-c"/><view class="route-path"/>
          <view class="map-point start">起</view><view class="map-point current">?</view><view class="map-point end">终</view>
          <view class="map-caption left">{{ task.sender || '发货方' }}</view>
          <view class="map-caption right">{{ task.receiver || '接收方' }}</view>
          <view class="map-empty-tip">暂无定位轨迹，等待设备上报位置后显示</view>
        </view>
      </view>

      <view class="action-grid">
        <view class="action-card" @tap="open('alarms')"><view class="action-icon warning">!</view><view><view class="action-title">查看异常</view><view class="action-desc">定位温度、开箱与碰撞</view></view><text>›</text></view>
        <view class="action-card" @tap="open('trace')"><view class="action-icon trace">⌁</view><view><view class="action-title">责任追溯</view><view class="action-desc">查看交接节点与报告</view></view><text>›</text></view>
      </view>
    </template>
  </view>
</template>

<style scoped>
.monitor-page { background: linear-gradient(180deg, #e9efff 0, #f5f7fb 470rpx); }.monitor-hero { margin: -28rpx -28rpx 24rpx; padding: 48rpx 32rpx 38rpx; color: #fff; background: linear-gradient(135deg,#7567ff,#4f72d9); border-radius: 0 0 38rpx 38rpx; box-shadow: 0 16rpx 36rpx rgba(74,76,184,.22); }.hero-row { display:flex; align-items:flex-start; justify-content:space-between; gap:20rpx; }.eyebrow { font-size:18rpx; letter-spacing:4rpx; opacity:.72; }.hero-title { margin-top:9rpx; font-size:40rpx; font-weight:800; }.hero-code { margin-top:16rpx; font-size:22rpx; opacity:.78; }.online-pill { display:inline-flex; align-items:center; gap:9rpx; margin-top:24rpx; padding:10rpx 16rpx; border-radius:999rpx; background:rgba(255,255,255,.16); font-size:21rpx; }.online-dot { width:10rpx; height:10rpx; border-radius:50%; background:#7ff0bd; box-shadow:0 0 0 6rpx rgba(127,240,189,.14); }.offline .online-dot { background:#ffd071; }.poll-tip { margin-top:12rpx; font-size:18rpx; opacity:.75; }
.empty-banner { margin-bottom:18rpx; padding:18rpx 20rpx; border-radius:18rpx; color:#8a6a16; background:#fff7df; font-size:22rpx; line-height:1.5; }
.metric-card { display:grid; grid-template-columns:1.25fr 1fr; margin-bottom:22rpx; overflow:hidden; border-radius:28rpx; background:#fff; box-shadow:0 12rpx 30rpx rgba(38,54,88,.09); }.primary-metric { padding:30rpx; background:linear-gradient(145deg,#fff,#f8f7ff); }.metric-caption,.side-label { color:#91a1b5; font-size:22rpx; }.temperature { margin:10rpx 0 6rpx; color:#182f48; font-size:62rpx; font-weight:820; }.temperature text { margin-left:5rpx; color:#8493a6; font-size:25rpx; }.range { color:#6f63f7; font-size:20rpx; }.metric-side { display:flex; flex-direction:column; justify-content:space-around; padding:24rpx 26rpx; border-left:1rpx solid #edf0f5; }.side-value { margin-top:7rpx; color:#40566e; font-size:31rpx; font-weight:720; }.side-time { margin-top:7rpx; color:#5a6f86; font-size:20rpx; line-height:1.45; }
.range-tabs{display:flex;gap:14rpx;margin:0 0 18rpx}.range-tab{flex:1;height:64rpx;border:1rpx solid #e4eadc;border-radius:999rpx;color:#4d5748;background:#fff;font-size:24rpx;font-weight:650;line-height:64rpx;text-align:center}.range-tab.active{color:#fff;border-color:#54ad06;background:linear-gradient(90deg,#7bd50d,#48ad00);box-shadow:0 8rpx 18rpx rgba(84,173,6,.22)}
.chart-card{padding:24rpx 20rpx 18rpx}.trend-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8rpx}.trend-title{color:#3d4738;font-size:26rpx;font-weight:720}.trend-range{color:#54ad06;font-size:24rpx;font-weight:700}.trend-canvas-wrap{width:100%;overflow:hidden}.trend-canvas{display:block;width:100%}.chart-empty{padding:60rpx 0;color:#9aa59a;text-align:center;font-size:24rpx}
.refresh{color:#54ad06;font-size:23rpx}.state-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13rpx}.state-item{padding:20rpx 12rpx;border-radius:20rpx;background:#f5f7fb;text-align:center}.state-icon{display:flex;align-items:center;justify-content:center;width:52rpx;height:52rpx;margin:0 auto 12rpx;border-radius:16rpx;font-size:20rpx;font-weight:750}.purple{color:#54ad06;background:#eff9e7}.blue{color:#2f83b7;background:#e5f3fb}.orange{color:#bd7720;background:#fff0dc}.state-label{color:#95a3b4;font-size:20rpx}.state-value{margin-top:6rpx;overflow:hidden;color:#40566e;font-size:23rpx;font-weight:680;text-overflow:ellipsis;white-space:nowrap}
.action-grid{display:grid;gap:16rpx}.action-card{display:grid;grid-template-columns:58rpx 1fr 20rpx;align-items:center;gap:17rpx;padding:22rpx;border:1rpx solid #e7ebf2;border-radius:23rpx;background:#fff;box-shadow:0 8rpx 22rpx rgba(38,54,88,.06)}.action-icon{display:flex;align-items:center;justify-content:center;width:58rpx;height:58rpx;border-radius:18rpx;font-size:29rpx;font-weight:800}.warning{color:#c97723;background:#fff0dc}.trace{color:#54ad06;background:#eff9e7}.action-title{color:#334b63;font-size:26rpx;font-weight:700}.action-desc{margin-top:4rpx;color:#9ca8b8;font-size:20rpx}.action-card>text{color:#a6b1bf;font-size:36rpx}
.monitor-page{background:#fbfcf9}.monitor-hero{background:linear-gradient(135deg,#80d60c,#46a700);box-shadow:0 16rpx 36rpx rgba(75,166,0,.22)}.range{color:#54ad06}.empty-banner{color:#6d7a2a;background:#f3f9e0}.switch-task{display:inline-flex;margin-top:18rpx;padding:10rpx 20rpx;border:1rpx solid rgba(255,255,255,.45);border-radius:999rpx;background:rgba(255,255,255,.14);font-size:22rpx}.hero-code{text-decoration:underline;text-underline-offset:4rpx}.route-map{position:relative;overflow:hidden;padding:20rpx;background:linear-gradient(135deg,#f4f6ee,#eef4e8)}.map-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14rpx}.map-hint{color:#6f7b68;font-size:20rpx}.live-map{width:100%;height:320rpx;border-radius:18rpx;overflow:hidden}.map-fallback{position:relative;height:260rpx}.map-road{position:absolute;height:18rpx;border-radius:20rpx;background:#fff;transform-origin:left center}.road-a{left:-20rpx;top:95rpx;width:680rpx;transform:rotate(-12deg)}.road-b{left:130rpx;top:20rpx;width:450rpx;transform:rotate(65deg)}.road-c{left:260rpx;top:210rpx;width:390rpx;transform:rotate(-48deg)}.route-path{position:absolute;left:65rpx;right:75rpx;top:142rpx;height:8rpx;border-radius:20rpx;background:#51ad08;transform:rotate(5deg)}.map-point{position:absolute;z-index:2;display:flex;align-items:center;justify-content:center;width:43rpx;height:43rpx;border:6rpx solid #fff;border-radius:50%;color:#fff;background:#54b006;font-size:18rpx;font-weight:800}.map-point.start{left:55rpx;top:103rpx}.map-point.current{left:330rpx;top:132rpx;width:58rpx;height:58rpx;color:#184600;background:#d8ff9d}.map-point.end{right:58rpx;top:157rpx}.map-caption{position:absolute;z-index:2;color:#31402b;font-size:20rpx}.map-caption.left{left:35rpx;top:165rpx}.map-caption.right{right:28rpx;top:214rpx}.map-empty-tip{position:absolute;left:20rpx;right:20rpx;bottom:12rpx;z-index:3;padding:10rpx 14rpx;border-radius:12rpx;color:#5f6d55;background:rgba(255,255,255,.92);font-size:18rpx;text-align:center}
</style>
