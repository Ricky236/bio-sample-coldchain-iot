<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { AlarmEvent, Task, Telemetry } from '@/types/api'
import { formatTime } from '@/utils/status'

const taskId = ref('')
const alarmId = ref(0)
const alarm = ref<AlarmEvent | null>(null)
const task = ref<Task | null>(null)
const snapshot = ref<Telemetry | null>(null)
const loading = ref(true)
const error = ref('')
const submitting = ref(false)
const state = ref<'pending' | 'processing' | 'review' | 'closed'>('pending')
const note = ref('')
const review = ref('')
const photos = ref<string[]>([])
const canHandle = computed(() => alarm.value?.can_handle !== false)
const custodyText = computed(() => {
  if (!alarm.value && !task.value) return '—'
  const label = alarm.value?.responsible_label
  const name = alarm.value?.responsible_name
  if (label || name) return `${label || '责任人'} · ${name || '—'}`
  const role = alarm.value?.responsible_role
  if (role === 'carrier') return `承运 · ${task.value?.carrier || '—'}`
  if (role === 'receiver') return `接收 · ${task.value?.receiver || '—'}`
  if (role === 'owner') return `发货 · ${task.value?.sender || '—'}`
  // 与追溯页一致：在途/到达→承运，签收→接收，其余→发货
  const status = task.value?.status
  if (status === 'signed') return `接收 · ${task.value?.receiver || '—'}`
  if (status === 'in_transit' || status === 'arrived') return `承运 · ${task.value?.carrier || '—'}`
  return `发货 · ${task.value?.sender || '—'}`
})
const value = computed(() => {
  if (!alarm.value) return '--'
  const detail = alarm.value.event_detail || alarm.value.description || ''
  if (alarm.value.event_type === 'TEMP_ALERT') return detail || '温度异常'
  if (alarm.value.event_type === 'BOX_OPEN') return detail || '箱盖开启'
  return detail || alarm.value.event_name || '--'
})

const boxText = computed(() => {
  const v = snapshot.value?.box_status
  if (v === 'BOX_CLOSED') return '已关闭'
  if (v === 'BOX_OPEN') return '已开启'
  return v || '--'
})
const tempStatusText = computed(() => {
  const v = snapshot.value?.temp_status
  if (v === 'TEMP_ALERT') return '温度告警'
  if (v === 'TEMP_OK') return '温度正常'
  return v || '--'
})
const locationText = computed(() => {
  const s = snapshot.value
  if (!s || typeof s.lat !== 'number' || typeof s.lng !== 'number') return '暂无定位'
  if (!Number.isFinite(s.lat) || !Number.isFinite(s.lng)) return '暂无定位'
  return `${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}`
})
const snapshotRows = computed(() => {
  const s = snapshot.value
  if (!s) return [] as Array<{ label: string; value: string }>
  return [
    { label: '采样时间', value: formatTime(s.timestamp || s.created_at) },
    { label: '设备编号', value: s.device_id || '--' },
    { label: '运单编号', value: s.task_id || task.value?.task_id || '--' },
    { label: '箱内温度', value: `${s.temperature ?? '--'} ℃` },
    { label: '湿度', value: `${s.humidity ?? '--'} %` },
    { label: '温度状态', value: tempStatusText.value },
    { label: '箱体状态', value: boxText.value },
    { label: '运动状态', value: s.move_status || '--' },
    { label: '事件类型', value: s.event_display || s.event_type || '--' },
    { label: '光敏原始值', value: String(s.light_raw ?? '--') },
    { label: '加速度总量', value: s.acc_total == null ? '--' : String(s.acc_total) },
    { label: '运动评分', value: s.motion_score == null ? '--' : String(s.motion_score) },
    { label: '电量', value: s.battery == null ? '--' : `${s.battery}%` },
    { label: '定位', value: locationText.value },
    { label: '定位精度', value: s.accuracy == null ? '--' : `${s.accuracy} m` },
    { label: '数据序号', value: s.sequence == null ? String(s.id || '--') : String(s.sequence) },
  ]
})

function pickSnapshot(alarmItem: AlarmEvent, history: Telemetry[]) {
  if (!history.length) return null
  const byId = history.find((item) => Number(item.id) === Number(alarmItem.data_id))
  if (byId) return byId
  const alarmTs = new Date(alarmItem.timestamp || alarmItem.created_at || 0).getTime()
  if (!Number.isFinite(alarmTs) || alarmTs <= 0) return history[0]
  let best = history[0]
  let bestDiff = Math.abs(new Date(best.timestamp || best.created_at || 0).getTime() - alarmTs)
  for (const item of history) {
    const diff = Math.abs(new Date(item.timestamp || item.created_at || 0).getTime() - alarmTs)
    if (diff < bestDiff) {
      best = item
      bestDiff = diff
    }
  }
  return best
}

function statusToFlow(status?: string | null) {
  if (status === 'resolved') return 'closed' as const
  if (status === 'acknowledged') return 'processing' as const
  return 'pending' as const
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [taskData, list, historyData, hardware] = await Promise.all([
      taskService.getTask(taskId.value),
      taskService.getAlarms(taskId.value, 100),
      taskService.getTelemetryHistory(taskId.value, 100),
      taskService.getHardwareSnapshot(taskId.value).catch(() => null),
    ])
    task.value = taskData
    alarm.value = list.items.find((x) => Number(x.id) === Number(alarmId.value)) || list.items[0] || null
    const history = [
      ...(hardware?.history || []),
      ...(historyData.items || []),
    ]
    if (alarm.value) {
      snapshot.value = pickSnapshot(alarm.value, history) || hardware?.latest || null
      state.value = statusToFlow(alarm.value.alarm_status)
      if (alarm.value.resolution) {
        const parts = String(alarm.value.resolution).split(' | ')
        note.value = parts[0] || ''
        review.value = parts.slice(1).join(' | ')
      }
    }
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function choosePhotos() {
  uni.chooseImage({
    count: 2,
    success: ({ tempFilePaths }) => {
      photos.value = [...photos.value, ...tempFilePaths].slice(0, 2)
    },
  })
}

async function ackAlarm() {
  if (!alarm.value || submitting.value) return
  if (!canHandle.value) {
    return uni.showToast({ title: `仅责任人可处置（${custodyText.value}）`, icon: 'none', duration: 2800 })
  }
  submitting.value = true
  try {
    const updated = await taskService.ackAlarm(alarm.value.id)
    alarm.value = { ...alarm.value, ...updated, can_handle: true }
    state.value = 'processing'
    uni.showToast({ title: '告警已确认', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: errorMessage(e), icon: 'none' })
  } finally {
    submitting.value = false
  }
}

async function resolveAlarm() {
  if (!alarm.value || submitting.value) return
  if (!canHandle.value) {
    return uni.showToast({ title: `仅责任人可处置（${custodyText.value}）`, icon: 'none', duration: 2800 })
  }
  if (!note.value.trim()) return uni.showToast({ title: '请填写处置说明', icon: 'none' })
  submitting.value = true
  try {
    const resolution = [note.value.trim(), review.value.trim()].filter(Boolean).join(' | ')
    const updated = await taskService.resolveAlarm(alarm.value.id, resolution)
    alarm.value = { ...alarm.value, ...updated, can_handle: true }
    state.value = 'closed'
    uni.showToast({ title: '告警已闭环', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: errorMessage(e), icon: 'none' })
  } finally {
    submitting.value = false
  }
}

function advance() {
  if (state.value === 'pending') return ackAlarm()
  if (state.value === 'processing' || state.value === 'review') return resolveAlarm()
}

onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  alarmId.value = Number(query?.alarm_id || 0)
  if (taskId.value) load()
  else {
    loading.value = false
    error.value = '缺少 task_id'
  }
})
</script>

<template>
  <view class="dispose-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error || !alarm" state="error" :message="error || '告警不存在'" @retry="load" />
    <template v-else>
      <view class="alert-card">
        <view class="alert-head"><b>▲　{{ alarm.event_type }} {{ alarm.event_name }}</b><text>需处置</text></view>
        <view class="value">{{ value }}</view>
        <view class="meta">发生 {{ formatTime(alarm.timestamp) }}</view>
        <view class="meta">设备 {{ alarm.device_id || task?.device_id || '—' }} · {{ task?.task_id }}</view>
        <view class="meta">责任段　{{ custodyText }}</view>
      </view>

      <view class="snapshot-card">
        <view class="snapshot-title">告警触发时状态</view>
        <view v-if="snapshotRows.length" class="snapshot-grid">
          <view v-for="row in snapshotRows" :key="row.label" class="snapshot-row">
            <text class="snapshot-label">{{ row.label }}</text>
            <text class="snapshot-value">{{ row.value }}</text>
          </view>
        </view>
        <view v-else class="snapshot-empty">未找到告警触发时的设备数据快照</view>
      </view>

      <view v-if="!canHandle" class="perm-notice">当前登录账号不是本告警责任人（{{ custodyText }}），仅可查看。</view>

      <view class="flow">
        <view :class="{ active: state === 'pending' }">待确认</view><b>›</b>
        <view :class="{ active: state === 'processing' || state === 'review' }">处理中</view><b>›</b>
        <view :class="{ active: state === 'closed' }">已关闭</view>
      </view>

      <view class="form-card">
        <view class="title">处置说明</view>
        <textarea v-model="note" maxlength="200" placeholder="例如：已补冰袋并复测至4.5℃" :disabled="state === 'closed' || !canHandle" />
        <view class="count">{{ note.length }}/200</view>
        <view class="title">上传现场照片</view>
        <view class="photos">
          <image v-for="src in photos" :key="src" :src="src" mode="aspectFill" />
          <view
            v-if="photos.length < 2 && state !== 'closed'"
            class="upload"
            :class="{ disabled: !canHandle }"
            @tap="canHandle && choosePhotos()"
          >▧<text>上传照片</text></view>
        </view>
        <view class="title">复核结论</view>
        <textarea v-model="review" maxlength="200" placeholder="请输入复核结论（选填）" :disabled="state === 'closed' || !canHandle" />
        <view class="actions">
          <button :disabled="submitting || state !== 'pending' || !canHandle" @tap="ackAlarm">确认告警</button>
          <button class="primary" :disabled="submitting || state === 'closed' || !canHandle" @tap="advance">
            {{ state === 'pending' ? '开始处置' : state === 'closed' ? '已关闭' : '关闭告警' }}
          </button>
        </view>
      </view>

      <view class="audit">♢　不可删除 · 操作者与时间留痕<br /><text>责任人 {{ custodyText }}　|　操作时间 {{ new Date().toLocaleString() }}</text></view>
    </template>
  </view>
</template>

<style scoped>
.dispose-page{min-height:100vh;padding:26rpx 30rpx 45rpx;box-sizing:border-box;background:linear-gradient(90deg,#f7ffe9,#fff 22%,#fff 78%,#f7ffe9)}.alert-card,.flow,.form-card,.audit,.snapshot-card,.perm-notice{border:1rpx solid #e2eadb;border-radius:24rpx;background:#fff;box-shadow:0 9rpx 25rpx rgba(56,91,29,.07)}.alert-card{padding:29rpx;color:#d93b3f;background:linear-gradient(135deg,#fff4f3,#fff)}.alert-head{display:flex;justify-content:space-between}.alert-head b{font-size:27rpx}.alert-head text{padding:7rpx 15rpx;border-radius:999rpx;background:#ffe5e3;font-size:19rpx}.value{margin:20rpx 0;font-size:58rpx;font-weight:800}.meta{margin-top:14rpx;color:#5f6960;font-size:22rpx}.snapshot-card{margin:20rpx 0;padding:24rpx}.snapshot-title{margin-bottom:16rpx;padding-left:13rpx;border-left:6rpx solid #5cb808;font-size:25rpx;font-weight:700;color:#31402b}.snapshot-grid{display:grid;gap:12rpx}.snapshot-row{display:flex;justify-content:space-between;gap:20rpx;padding:10rpx 0;border-bottom:1rpx solid #eef2ea}.snapshot-label{color:#7a8574;font-size:22rpx;flex-shrink:0}.snapshot-value{color:#31402b;font-size:22rpx;font-weight:600;text-align:right;word-break:break-all}.snapshot-empty{padding:24rpx 0;color:#8a9483;font-size:22rpx;text-align:center}.perm-notice{margin:20rpx 0 0;padding:20rpx 24rpx;color:#8a6a18;background:#fff8e8;font-size:22rpx;line-height:1.5}.flow{display:flex;align-items:center;justify-content:space-around;margin:20rpx 0;padding:18rpx}.flow view{padding:11rpx 21rpx;border:1rpx solid #cfd5ca;border-radius:999rpx;color:#626a5e;font-size:20rpx}.flow view.active{color:#fff;border-color:#5cba08;background:#5cba08}.flow b{color:#a3aaa0}.form-card{padding:26rpx}.title{margin:16rpx 0 12rpx;padding-left:13rpx;border-left:6rpx solid #5cb808;font-size:25rpx;font-weight:700}.form-card textarea{width:100%;height:135rpx;padding:17rpx;box-sizing:border-box;border:1rpx solid #ccd3c8;border-radius:10rpx;font-size:22rpx}.count{text-align:right;color:#949b90;font-size:18rpx}.photos{display:flex;gap:18rpx}.photos image,.upload{width:120rpx;height:120rpx;border-radius:10rpx}.upload{display:flex;flex-direction:column;align-items:center;justify-content:center;border:2rpx dashed #cbd2c7;color:#a0a79d;font-size:30rpx}.upload text{margin-top:5rpx;font-size:17rpx}.upload.disabled{opacity:.45}.actions{display:grid;grid-template-columns:1fr 1.4fr;gap:20rpx;margin-top:24rpx}.actions button{height:68rpx;border:2rpx solid #58b707;border-radius:12rpx;color:#4eaa00;background:#fff;font-size:21rpx;line-height:64rpx}.actions .primary{color:#fff;background:linear-gradient(90deg,#79d40d,#4cad00)}.audit{margin-top:20rpx;padding:21rpx;color:#53624e;background:#f5faef;font-size:20rpx;line-height:1.8}.audit text{color:#798275;font-size:18rpx}
</style>
