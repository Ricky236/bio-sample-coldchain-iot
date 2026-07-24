<script setup lang="ts">
import { computed } from 'vue'
import type { Telemetry } from '@/types/api'

const props = withDefaults(defineProps<{
  items?: Telemetry[] | null
  title?: string
  /** send=发出交接页，receive=到达验收页，transit=运输中 */
  stage?: 'send' | 'receive' | 'transit'
  /** 是否已完成发出交接；未传时：stage=send 视为未发出 */
  departed?: boolean
}>(), {
  items: () => [],
  title: '运输位置与轨迹',
  stage: 'transit',
})

type GpsPoint = { latitude: number; longitude: number; id?: number; time?: string }

function isGps(item: Telemetry): boolean {
  return typeof item.lat === 'number' && typeof item.lng === 'number'
    && Number.isFinite(item.lat) && Number.isFinite(item.lng)
}

const points = computed<GpsPoint[]>(() => {
  const list = (props.items || []).filter(isGps)
  const ordered = [...list].sort((a, b) => {
    const ta = new Date(a.timestamp || a.created_at || 0).getTime()
    const tb = new Date(b.timestamp || b.created_at || 0).getTime()
    return ta - tb
  })
  return ordered.map((item) => ({
    latitude: Number(item.lat),
    longitude: Number(item.lng),
    id: item.id,
    time: item.timestamp || item.created_at || '',
  }))
})

const hasGps = computed(() => points.value.length > 0)
const startPoint = computed(() => points.value[0] || null)
const endPoint = computed(() => points.value[points.value.length - 1] || null)
/** 未发出前不展示「发出/交接/接收」标签，只展示设备实时位置 */
const isDeparted = computed(() => (
  typeof props.departed === 'boolean' ? props.departed : props.stage !== 'send'
))

function fmt(point: GpsPoint | null) {
  if (!point) return '暂无定位'
  return `${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`
}

const stageRows = computed(() => {
  const start = startPoint.value
  const end = endPoint.value
  if (!isDeparted.value) {
    return [
      { key: 'live', label: '设备当前位置', value: fmt(end || start), active: true },
    ]
  }
  const mid = points.value.length >= 3
    ? points.value[Math.floor(points.value.length / 2)]
    : end
  return [
    { key: 'send', label: '发出位置', value: fmt(start), active: props.stage === 'send' },
    { key: 'handoff', label: props.stage === 'send' ? '交接位置' : '途中位置', value: fmt(mid || end), active: props.stage === 'transit' },
    { key: 'receive', label: '接收位置', value: props.stage === 'send' && points.value.length < 2 ? '待运输到达后生成' : fmt(end), active: props.stage === 'receive' },
  ]
})

const mapHint = computed(() => {
  if (!hasGps.value) return '等待设备上报位置'
  if (!isDeparted.value) return '发出前仅显示设备实时定位'
  return `${points.value.length} 个定位点`
})

const mapCenter = computed(() => {
  const p = endPoint.value || startPoint.value
  if (!p) return { latitude: 31.23, longitude: 121.47 }
  return { latitude: p.latitude, longitude: p.longitude }
})

const polyline = computed(() => {
  // 未发出：不画历史轨迹线，避免被理解成已发运路径
  if (!isDeparted.value || points.value.length < 2) return [] as Array<Record<string, unknown>>
  return [{
    points: points.value.map((p) => ({ latitude: p.latitude, longitude: p.longitude })),
    color: '#54AD06',
    width: 4,
    dottedLine: false,
  }]
})

const markers = computed(() => {
  const pts = points.value
  if (!pts.length) return [] as Array<Record<string, unknown>>
  if (!isDeparted.value) {
    const cur = pts[pts.length - 1]
    return [{
      id: 1,
      latitude: cur.latitude,
      longitude: cur.longitude,
      width: 28,
      height: 28,
      title: '当前位置',
    }]
  }
  const list: Array<Record<string, unknown>> = [
    { id: 1, latitude: pts[0].latitude, longitude: pts[0].longitude, width: 24, height: 24, title: '发出' },
  ]
  if (pts.length >= 3) {
    const mid = pts[Math.floor(pts.length / 2)]
    list.push({ id: 2, latitude: mid.latitude, longitude: mid.longitude, width: 22, height: 22, title: '途中' })
  }
  if (pts.length >= 2) {
    const last = pts[pts.length - 1]
    list.push({
      id: 3,
      latitude: last.latitude,
      longitude: last.longitude,
      width: 28,
      height: 28,
      title: props.stage === 'receive' ? '接收' : '当前位置',
    })
  }
  return list
})
</script>

<template>
  <view class="card route-track-card">
    <view class="map-head">
      <view class="section-title">{{ title }}</view>
      <view class="map-hint">{{ mapHint }}</view>
    </view>

    <view class="stage-list">
      <view v-for="row in stageRows" :key="row.key" class="stage-row" :class="{ active: row.active }">
        <view class="stage-label">{{ row.label }}</view>
        <view class="stage-value">{{ row.value }}</view>
      </view>
    </view>

    <map
      v-if="hasGps"
      class="live-map"
      :latitude="mapCenter.latitude"
      :longitude="mapCenter.longitude"
      :scale="14"
      :polyline="polyline"
      :markers="markers"
    />
    <view v-else class="map-empty">{{ isDeparted ? '暂无运输轨迹，设备上报经纬度后将在此显示发出、途中与接收位置' : '暂无定位，设备上报经纬度后将显示当前位置' }}</view>
  </view>
</template>

<style scoped>
.route-track-card {
  position: relative;
  overflow: hidden;
  padding: 22rpx;
  background: linear-gradient(135deg, #f4f6ee, #eef4e8);
}
.map-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14rpx;
}
.map-hint { color: #6f7b68; font-size: 20rpx; }
.stage-list {
  display: grid;
  gap: 10rpx;
  margin-bottom: 16rpx;
}
.stage-row {
  display: grid;
  grid-template-columns: 140rpx 1fr;
  gap: 12rpx;
  padding: 14rpx 16rpx;
  border-radius: 14rpx;
  background: rgba(255, 255, 255, 0.86);
}
.stage-row.active {
  border: 1rpx solid #b7e57a;
  background: #f3fbe6;
}
.stage-label {
  color: #6f7b68;
  font-size: 22rpx;
}
.stage-value {
  color: #2f4630;
  font-size: 22rpx;
  font-weight: 620;
  word-break: break-all;
}
.live-map {
  width: 100%;
  height: 300rpx;
  border-radius: 18rpx;
  overflow: hidden;
}
.map-empty {
  padding: 36rpx 20rpx;
  border-radius: 16rpx;
  color: #7a866f;
  background: rgba(255, 255, 255, 0.75);
  text-align: center;
  font-size: 22rpx;
  line-height: 1.5;
}
</style>
