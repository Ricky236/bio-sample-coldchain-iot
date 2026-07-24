<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { appConfig } from '@/config/env'
import { errorMessage } from '@/services/request'
import { taskService } from '@/services/tasks'
import { useSessionStore } from '@/stores/session'
import type { AssignmentCandidate, DeviceBindingCheck, DevicePrecheck } from '@/types/api'

const session = useSessionStore()
const DRAFT_KEY = 'coldchain_create_draft_v2'
const editingTaskId = ref('')
const waybillNo = ref('提交后由后端自动生成')
const sampleName = ref('')
const batch = ref('')
const range = ref('2 ~ 8℃')
const sender = ref('')
const arrivalDate = ref('')
const arrivalTime = ref('')
const device = ref('')
const box = ref('')
const seal = ref('')
const carriers = ref<AssignmentCandidate[]>([])
const receivers = ref<AssignmentCandidate[]>([])
const carrierIndex = ref(-1)
const receiverIndex = ref(-1)
const candidatesLoading = ref(false)
const saved = ref(false)
const submitting = ref(false)
const prechecking = ref(false)
const precheck = ref<DevicePrecheck | null>(null)
const binding = ref<DeviceBindingCheck | null>(null)
const errors = reactive<Record<string, string>>({})

const selectedCarrier = computed(() => carriers.value[carrierIndex.value] || null)
const selectedReceiver = computed(() => receivers.value[receiverIndex.value] || null)
const carrier = computed(() => selectedCarrier.value?.name || '')
const receiver = computed(() => selectedReceiver.value?.organization || selectedReceiver.value?.name || '')
const arrival = computed(() => [arrivalDate.value, arrivalTime.value].filter(Boolean).join(' '))
const basicReady = computed(() => Boolean(
  sampleName.value.trim() && batch.value.trim() && selectedCarrier.value && selectedReceiver.value
  && arrivalDate.value && arrivalTime.value && rangeValues(),
))
const deviceReady = computed(() => Boolean(device.value.trim() && box.value.trim() && seal.value.trim()))
const isEditing = computed(() => Boolean(editingTaskId.value))
const activeStep = computed(() => !basicReady.value ? 1 : !deviceReady.value ? 2 : 3)
const canSubmit = computed(() => basicReady.value && deviceReady.value && (isEditing.value || precheck.value?.passed) && !submitting.value)

function rangeValues() {
  const values = range.value.match(/-?\d+(?:\.\d+)?/g)?.map(Number) || []
  return values.length >= 2 && values[0] < values[1] ? [values[0], values[1]] : null
}
function candidateLabel(item: AssignmentCandidate) {
  return `${item.name}${item.organization ? ` · ${item.organization}` : ''}`
}
async function loadCandidates() {
  candidatesLoading.value = true
  try {
    const [carrierResult, receiverResult] = await Promise.all([
      taskService.listCandidates('carrier'),
      taskService.listCandidates('receiver'),
    ])
    carriers.value = carrierResult.items
    receivers.value = receiverResult.items
  } catch (error) {
    uni.showToast({ title: errorMessage(error), icon: 'none', duration: 3000 })
  } finally { candidatesLoading.value = false }
}
function validateBasic() {
  Object.keys(errors).forEach((key) => delete errors[key])
  if (!sampleName.value.trim()) errors.sampleName = '请填写样本名称'
  if (!batch.value.trim()) errors.batch = '请填写样本批次'
  if (!rangeValues()) errors.range = '请使用如“2 ~ 8℃”的温控范围'
  if (!selectedReceiver.value) errors.receiver = receivers.value.length ? '请选择接收账号' : '暂无接收账号，请先注册接收方'
  if (!selectedCarrier.value) errors.carrier = carriers.value.length ? '请选择承运账号' : '暂无承运账号，请先注册承运方'
  if (!arrivalDate.value || !arrivalTime.value) errors.arrival = '请选择预计送达日期和时间'
  return Object.keys(errors).length === 0
}
function validateDevice() {
  if (!device.value.trim()) errors.device = '请扫码或填写设备编号'; else delete errors.device
  if (!box.value.trim()) errors.box = '请填写箱体编号'; else delete errors.box
  if (!seal.value.trim()) errors.seal = '请填写封签编号'; else delete errors.seal
  return deviceReady.value
}
function parseDeviceCode(raw: string) {
  try {
    const data = JSON.parse(raw) as Record<string, string>
    device.value = data.device_id || data.device || device.value
    box.value = data.box_id || data.box || box.value
    seal.value = data.seal_id || data.seal || seal.value
    return
  } catch { /* 继续解析 URL 或纯设备号 */ }
  const query = raw.includes('?') ? raw.split('?')[1] : ''
  if (!query) { device.value = raw.trim(); return }
  const params: Record<string, string> = {}
  query.split('&').forEach((item) => {
    const [key, value = ''] = item.split('=')
    params[decodeURIComponent(key)] = decodeURIComponent(value)
  })
  device.value = params.device_id || params.device || device.value
  box.value = params.box_id || params.box || box.value
  seal.value = params.seal_id || params.seal || seal.value
}
function scanDevice() {
  uni.scanCode({
    scanType: ['qrCode', 'barCode'],
    success: ({ result }) => { parseDeviceCode(result); validateDevice() },
    fail: () => uni.showToast({ title: '未识别到设备码', icon: 'none' }),
  })
}
async function runPrecheck(allowLocal = false) {
  if (!validateBasic() || !validateDevice()) return uni.showToast({ title: '请先补全运单和设备信息', icon: 'none' })
  const limits = rangeValues()
  if (!limits) return
  prechecking.value = true
  try {
    binding.value = await taskService.checkDeviceBinding(device.value.trim(), box.value.trim(), seal.value.trim())
    if (!binding.value.available) {
      precheck.value = null
      return uni.showToast({ title: `${binding.value.message}：${binding.value.occupied_task_id || ''}`, icon: 'none', duration: 3000 })
    }
    precheck.value = await taskService.precheckDevice(device.value.trim(), limits[0], limits[1], allowLocal)
    uni.showToast({ title: precheck.value.passed ? '设备预检合格' : precheck.value.reason, icon: precheck.value.passed ? 'success' : 'none', duration: 2800 })
  } catch (error) {
    precheck.value = null
    uni.showToast({ title: errorMessage(error), icon: 'none', duration: 3000 })
  } finally { prechecking.value = false }
}
async function createLocalReading() {
  const limits = rangeValues()
  if (!limits || !validateBasic() || !validateDevice()) return uni.showToast({ title: '请先补全信息', icon: 'none' })
  try {
    await taskService.simulateLocalReading(device.value.trim(), Number(((limits[0] + limits[1]) / 2).toFixed(1)))
    await runPrecheck(true)
  } catch (error) { uni.showToast({ title: errorMessage(error), icon: 'none', duration: 2800 }) }
}
function saveDraft() {
  uni.setStorageSync(DRAFT_KEY, {
    sampleName: sampleName.value, batch: batch.value, range: range.value,
    carrierUserId: selectedCarrier.value?.user_id, receiverUserId: selectedReceiver.value?.user_id,
    arrivalDate: arrivalDate.value, arrivalTime: arrivalTime.value,
    device: device.value, box: box.value, seal: seal.value,
  })
  saved.value = true
  uni.showToast({ title: '草稿已保存', icon: 'success' })
}
function makeInput() {
  return {
    sample_name: sampleName.value.trim(), batch: batch.value.trim(), receiver: receiver.value,
    carrier: carrier.value, expected_arrival: arrival.value, device_id: device.value.trim(),
    box_id: box.value.trim(), seal_id: seal.value.trim(), temperature_range: range.value.trim(),
  }
}
async function complete() {
  if (submitting.value || !validateBasic() || !validateDevice()) return
  if (!isEditing.value && !precheck.value?.passed) return uni.showToast({ title: '请先执行并通过设备预检', icon: 'none' })
  const carrierUserId = selectedCarrier.value!.user_id
  const receiverUserId = selectedReceiver.value!.user_id
  submitting.value = true
  try {
    if (isEditing.value) {
      const task = await taskService.updateTask(editingTaskId.value, makeInput())
      await taskService.assignTask(task.task_id, carrierUserId, receiverUserId)
      return uni.showModal({ title: '运单修改成功', content: `运单号：${task.task_id}\n账号分配与基础信息已更新`, showCancel: false, success: () => uni.navigateBack() })
    }
    let task = await taskService.createTask(makeInput())
    await taskService.assignTask(task.task_id, carrierUserId, receiverUserId)
    await taskService.bindDevice(device.value.trim(), task.task_id)
    task = await taskService.saveTaskPrecheck(task.task_id, {
      passed: true, temperature: precheck.value!.temperature,
      seal_ok: precheck.value!.box_status === 'BOX_CLOSED',
      note: `小程序装箱预检通过：${precheck.value!.reason}`,
    })
    waybillNo.value = task.task_id
    uni.removeStorageSync(DRAFT_KEY)
    uni.showModal({
      title: '运单创建成功', content: `固定运单号：${task.task_id}\n设备与责任人已绑定`,
      showCancel: false, success: () => uni.redirectTo({ url: `/pages/task-detail/index?task_id=${encodeURIComponent(task.task_id)}` }),
    })
  } catch (error) { uni.showToast({ title: errorMessage(error), icon: 'none', duration: 3600 }) }
  finally { submitting.value = false }
}
function selectStoredCandidates(carrierId?: string | number | null, receiverId?: string | number | null) {
  carrierIndex.value = carriers.value.findIndex((item) => String(item.user_id) === String(carrierId || ''))
  receiverIndex.value = receivers.value.findIndex((item) => String(item.user_id) === String(receiverId || ''))
}
async function loadEditingTask(taskId: string) {
  const task = await taskService.getTask(taskId)
  if (!['pending_pack', 'pending_handoff'].includes(task.status)) throw new Error('该运单已进入运输流程，不能再修改')
  editingTaskId.value = task.task_id; waybillNo.value = task.task_id
  sampleName.value = task.sample_name || ''; batch.value = task.batch || ''
  range.value = task.temperature_range || `${task.temperature_min ?? 2} ~ ${task.temperature_max ?? 8}℃`
  sender.value = task.sender || session.user?.organization || ''
  device.value = task.device_id || ''; box.value = task.box_id || ''; seal.value = task.seal_id || ''
  const [date = '', time = ''] = String(task.expected_arrival || '').replace('T', ' ').split(' ')
  arrivalDate.value = date; arrivalTime.value = time.slice(0, 5)
  selectStoredCandidates(task.carrier_user_id, task.receiver_user_id)
  uni.setNavigationBarTitle({ title: '编辑运单' })
}
function restoreDraft() {
  const draft = uni.getStorageSync(DRAFT_KEY) as Record<string, string | number> | null
  if (!draft) return
  sampleName.value = String(draft.sampleName || ''); batch.value = String(draft.batch || ''); range.value = String(draft.range || range.value)
  arrivalDate.value = String(draft.arrivalDate || ''); arrivalTime.value = String(draft.arrivalTime || '')
  device.value = String(draft.device || ''); box.value = String(draft.box || ''); seal.value = String(draft.seal || '')
  selectStoredCandidates(draft.carrierUserId, draft.receiverUserId); saved.value = true
}
watch([device, box, seal, range], () => { binding.value = null; precheck.value = null })
onLoad(async (query) => {
  if (!session.requireSession()) return
  if (!['sender', 'admin'].includes(session.user?.role || '')) {
    uni.showToast({ title: '只有发货方可以创建运单', icon: 'none' }); return setTimeout(() => uni.navigateBack(), 600)
  }
  sender.value = session.user?.organization || ''
  await loadCandidates()
  const taskId = typeof query?.task_id === 'string' ? decodeURIComponent(query.task_id) : ''
  try { if (taskId) await loadEditingTask(taskId); else restoreDraft() }
  catch (error) { uni.showToast({ title: errorMessage(error), icon: 'none', duration: 3000 }) }
})
</script>

<template>
  <view class="create-page">
    <view v-if="!isEditing" class="steps">
      <view :class="{ active: activeStep === 1, done: activeStep > 1 }"><b>1</b>创建运单</view><i />
      <view :class="{ active: activeStep === 2, done: activeStep > 2 }"><b>2</b>绑定设备</view><i />
      <view :class="{ active: activeStep === 3, done: precheck?.passed }"><b>3</b>装箱预检</view>
    </view>
    <view class="panel">
      <view class="panel-title"><b>01 {{ isEditing ? '修改运单信息' : '运单信息' }}</b><text>* 为必填项</text></view>
      <view class="field"><label>运单号</label><input v-model="waybillNo" disabled /></view>
      <view class="field"><label>样本名称 *</label><view><input v-model="sampleName" placeholder="例：疫苗样本 A" /><small v-if="errors.sampleName">{{ errors.sampleName }}</small></view></view>
      <view class="field"><label>批次 *</label><view><input v-model="batch" placeholder="例：VAC-20260724-01" /><small v-if="errors.batch">{{ errors.batch }}</small></view></view>
      <view class="field"><label>温控范围 *</label><view><input v-model="range" /><small v-if="errors.range">{{ errors.range }}</small></view></view>
      <view class="field"><label>发货单位</label><input v-model="sender" disabled /></view>
      <view class="field"><label>承运账号 *</label><view><picker :range="carriers" range-key="name" :value="carrierIndex" @change="carrierIndex=Number($event.detail.value)"><view class="picker-value">{{ selectedCarrier ? candidateLabel(selectedCarrier) : candidatesLoading ? '正在加载…' : '请选择已注册承运人' }}</view></picker><small v-if="errors.carrier">{{ errors.carrier }}</small></view></view>
      <view class="field"><label>接收账号 *</label><view><picker :range="receivers" range-key="name" :value="receiverIndex" @change="receiverIndex=Number($event.detail.value)"><view class="picker-value">{{ selectedReceiver ? candidateLabel(selectedReceiver) : candidatesLoading ? '正在加载…' : '请选择已注册接收人' }}</view></picker><small v-if="errors.receiver">{{ errors.receiver }}</small></view></view>
      <view class="field"><label>预计送达 *</label><view><view class="datetime"><picker mode="date" :value="arrivalDate" @change="arrivalDate=String($event.detail.value)"><view>{{ arrivalDate || '选择日期' }}</view></picker><picker mode="time" :value="arrivalTime" @change="arrivalTime=String($event.detail.value)"><view>{{ arrivalTime || '选择时间' }}</view></picker></view><small v-if="errors.arrival">{{ errors.arrival }}</small></view></view>
    </view>
    <view class="panel">
      <view class="panel-title panel-head"><b>02 设备绑定</b><button v-if="!isEditing" @tap="scanDevice">⌗ 扫码绑定</button><text v-else>设备不可修改</text></view>
      <view class="device-grid">
        <view><label>设备 *</label><input v-model="device" :disabled="isEditing" placeholder="CLD-001" /><small v-if="errors.device">{{ errors.device }}</small></view>
        <view><label>箱体 *</label><input v-model="box" placeholder="BOX-001" /><small v-if="errors.box">{{ errors.box }}</small></view>
        <view><label>封签 *</label><input v-model="seal" placeholder="SEAL-001" /><small v-if="errors.seal">{{ errors.seal }}</small></view>
      </view>
      <view class="device-state"><text :class="{ ok: precheck?.online }">● {{ precheck?.online ? '已连接' : '待检测' }}</text><text :class="{ ok: binding?.available }">▣ {{ binding?.available ? '可绑定' : '待校验' }}</text><text :class="{ ok: precheck?.passed }">⌗ {{ precheck?.passed ? '传感器正常' : '待预检' }}</text></view>
    </view>
    <view v-if="!isEditing" class="panel">
      <view class="panel-title panel-head"><b>03 首条监测预检</b><button :disabled="prechecking" @tap="runPrecheck(false)">{{ prechecking ? '检测中…' : '执行真实预检' }}</button></view>
      <view v-if="precheck" class="metrics"><view><label>温度</label><b>{{ precheck.temperature ?? '--' }}℃</b></view><view><label>湿度</label><b>{{ precheck.humidity ?? '--' }}%</b></view><view><label>箱体</label><b>{{ precheck.box_status === 'BOX_CLOSED' ? '已关闭' : '未关闭' }}</b></view><view><label>结果</label><b>{{ precheck.passed ? '✓ 合格' : '! 未通过' }}</b></view></view>
      <view v-else class="empty">点击“执行真实预检”读取后端最新温湿度和箱体状态。</view>
      <view v-if="precheck" :class="precheck.passed ? 'ok-note' : 'warn-note'">{{ precheck.reason }}<text v-if="precheck.reported_at"> · {{ precheck.reported_at }}</text></view>
      <button v-if="appConfig.localSimulation && !precheck?.online" class="local-test" @tap="createLocalReading">无硬件？生成本地测试数据</button>
    </view>
    <view class="actions" :class="{ editing: isEditing }"><button v-if="!isEditing" class="draft" @tap="saveDraft">▤ {{ saved ? '更新草稿' : '存草稿' }}</button><button class="complete" :disabled="!canSubmit" @tap="complete">{{ submitting ? '正在提交…' : isEditing ? '✓ 保存修改' : precheck?.passed ? '✓ 创建运单' : '请先完成预检' }}</button></view>
  </view>
</template>

<style scoped>
.create-page{min-height:100vh;padding:24rpx 30rpx 180rpx;box-sizing:border-box;color:#263222;background:#fbfcf9}.steps{display:flex;align-items:center;justify-content:center;gap:8rpx;margin:10rpx 0 22rpx}.steps>view{display:flex;align-items:center;gap:8rpx;padding:11rpx 17rpx;border:1rpx solid #dfe5d9;border-radius:999rpx;color:#7b8376;font-size:21rpx}.steps b{display:flex;align-items:center;justify-content:center;width:32rpx;height:32rpx;border-radius:50%;background:#edf0eb}.steps .active{color:#fff;border-color:#67c700;background:linear-gradient(90deg,#8de220,#4daf00)}.steps .done{color:#4faa00;border-color:#aada7f;background:#f1fae9}.steps i{width:30rpx;border-top:2rpx dashed #aaca83}.panel{margin-bottom:18rpx;padding:24rpx;border:1rpx solid #dcebc8;border-radius:24rpx;background:#fff;box-shadow:0 8rpx 24rpx rgba(62,105,26,.08)}.panel-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:14rpx}.panel-title b{font-size:27rpx}.panel-title>text{color:#9ba395;font-size:18rpx}.field{display:grid;grid-template-columns:150rpx 1fr;align-items:start;min-height:76rpx;padding:5rpx 0}.field label{padding-top:15rpx;font-size:23rpx}.field input,.picker-value,.datetime>picker{height:56rpx;padding:0 16rpx;box-sizing:border-box;border-radius:10rpx;color:#263222;background:#f4f5f3;font-size:22rpx;line-height:56rpx}.field input[disabled]{color:#5f695a}.field small,.device-grid small{display:block;margin-top:5rpx;color:#dc5c4d;font-size:17rpx}.datetime{display:grid;grid-template-columns:1fr 1fr;gap:10rpx}.panel-head button{height:52rpx;padding:0 18rpx;border:2rpx solid #59b807;border-radius:16rpx;color:#54ae06;background:#fff;font-size:19rpx;line-height:48rpx}.device-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14rpx}.device-grid label{display:block;margin-bottom:7rpx;color:#697164;font-size:20rpx}.device-grid input{width:100%;height:58rpx;padding:0 11rpx;box-sizing:border-box;border-radius:9rpx;background:#f2f3f1;font-size:21rpx}.device-state{display:grid;grid-template-columns:repeat(3,1fr);gap:12rpx;margin-top:16rpx}.device-state text{padding:12rpx 5rpx;border:1rpx solid #e0e5dc;border-radius:10rpx;color:#899184;text-align:center;font-size:17rpx}.device-state text.ok{color:#52ae06;border-color:#cfe7ba;background:#f5fbef}.metrics{display:grid;grid-template-columns:repeat(4,1fr);text-align:center}.metrics>view{border-right:1rpx solid #e2e8dc}.metrics label{display:block;color:#777f72;font-size:18rpx}.metrics b{display:block;margin-top:10rpx;color:#53b006;font-size:25rpx}.empty{padding:28rpx;border:1rpx dashed #cfdac5;border-radius:15rpx;color:#7d8778;background:#fafcf8;font-size:20rpx}.ok-note,.warn-note{margin-top:16rpx;padding:11rpx 14rpx;border-radius:9rpx;font-size:18rpx}.ok-note{color:#4e9f0d;background:#f1f9e9}.warn-note{color:#c97900;background:#fff8e7}.local-test{height:62rpx;margin-top:16rpx;border:1rpx solid #79c43c;border-radius:14rpx;color:#52a711;background:#f6fcef;font-size:20rpx;line-height:62rpx}.actions{position:fixed;z-index:10;left:0;right:0;bottom:0;display:grid;grid-template-columns:1fr 1.7fr;gap:24rpx;padding:22rpx 36rpx calc(22rpx + env(safe-area-inset-bottom));background:rgba(255,255,255,.97);box-shadow:0 -8rpx 25rpx rgba(44,73,22,.08)}.actions.editing{grid-template-columns:1fr}.actions button{height:76rpx;border-radius:999rpx;font-size:24rpx;line-height:76rpx}.draft{border:1rpx solid #8d9588;color:#656d61;background:#fff}.complete{color:#fff;background:linear-gradient(90deg,#7bd50d,#48ad00)}.complete[disabled]{color:#99a293;background:#e8ece5}
</style>
