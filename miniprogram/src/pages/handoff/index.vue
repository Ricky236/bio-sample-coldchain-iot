<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onUnload } from '@dcloudio/uni-app'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import { useSessionStore } from '@/stores/session'
import type { HandoffQr, HandoffSession, Task, Telemetry } from '@/types/api'
import { appConfig } from '@/config/env'

const sessionStore = useSessionStore()
const taskId = ref('')
const task = ref<Task | null>(null)
const telemetry = ref<Telemetry | null>(null)
const qr = ref<HandoffQr | null>(null)
const handoff = ref<HandoffSession | null>(null)
const note = ref('')
const loading = ref(true)
const submitting = ref(false)
const faceBusy = ref(false)
const telemetryBusy = ref(false)
const telemetrySource = ref<'hardware' | 'local' | 'none'>('none')
const telemetryMessage = ref('')
const error = ref('')
const confirmVisible = ref(false)
const cameraVisible = ref(false)
const cameraFailed = ref(false)
const cameraFailure = ref('')
const countdown = ref(0)
const incomingToken = ref('')
const isLocalSimulation = appConfig.localSimulation
const runtimePlatform = (() => {
  try { return String(uni.getDeviceInfo().platform || '').toLowerCase() }
  catch { return '' }
})()
const isDevTools = runtimePlatform === 'devtools'
let timer: ReturnType<typeof setInterval> | null = null
let poller: ReturnType<typeof setInterval> | null = null
let telemetryPoller: ReturnType<typeof setInterval> | null = null

const isArrivalHandoff = computed(() => handoff.value?.handoff_type === 'carrier_to_receiver' || Boolean(
  task.value
  && ['in_transit', 'arrived'].includes(task.value.status)
  && String(task.value.carrier_user_id || '') === String(sessionStore.user?.id || ''),
))
const isIssuer = computed(() => Boolean(
  handoff.value
    ? String(handoff.value.issuer_user_id || '') === String(sessionStore.user?.id || '')
    : isArrivalHandoff.value
      ? String(task.value?.carrier_user_id || '') === String(sessionStore.user?.id || '')
      : String(task.value?.owner_user_id || '') === String(sessionStore.user?.id || ''),
))
const currentParty = computed<'issuer' | 'recipient'>(() => isIssuer.value ? 'issuer' : 'recipient')
const issuerVerified = computed(() => Boolean(handoff.value?.faces?.issuer?.verified))
const recipientVerified = computed(() => Boolean(handoff.value?.faces?.recipient?.verified))
const qrVerified = computed(() => handoff.value?.status === 'qr_verified' || Boolean(handoff.value?.qr_verified_at))
const ready = computed(() => issuerVerified.value && recipientVerified.value && qrVerified.value)
const currentFaceVerified = computed(() => currentParty.value === 'issuer' ? issuerVerified.value : recipientVerified.value)
const handoffConfirmed = computed(() => handoff.value?.status === 'confirmed' || (!isArrivalHandoff.value && task.value?.status === 'in_transit'))
const packingComplete = computed(() => ['pending_handoff', 'in_transit', 'arrived', 'signed'].includes(String(task.value?.status || '')))
const primaryText = computed(() => {
  if (handoffConfirmed.value) return '交接完成，运输已开始'
  if (isIssuer.value) {
    if (!qrVerified.value) return isArrivalHandoff.value ? '已到达，请让接收方扫码' : '装箱已完成，请让承运人扫码'
    if (!recipientVerified.value) return isArrivalHandoff.value ? '扫码完成，等待接收方人脸核验' : '扫码完成，等待承运人人脸核验'
    return isArrivalHandoff.value ? '接收方已核验，等待确认到达' : '承运人已核验，等待承运人确认接收'
  }
  if (!qrVerified.value) return '扫描发货方动态二维码'
  if (!recipientVerified.value) return '开始承运人人脸核验'
  if (!ready.value) return '等待交出方身份核验'
  return isArrivalHandoff.value ? '确认到达并进入验收' : '确认接收并开始运输'
})
const primaryDisabled = computed(() => {
  if (handoffConfirmed.value || submitting.value) return true
  if (isIssuer.value) return true
  return recipientVerified.value && !ready.value
})

async function loadTask() {
  task.value = await taskService.getTask(taskId.value)
  await refreshTelemetry()
}

async function refreshTelemetry() {
  if (!task.value) return
  const [hardware, local] = await Promise.all([
    taskService.getHardwareSnapshot(task.value.task_id).catch(() => null),
    taskService.getLatestTelemetry(task.value.task_id).catch(() => null),
  ])
  if (hardware?.matched && hardware.latest) {
    telemetry.value = hardware.latest
    telemetrySource.value = 'hardware'
    telemetryMessage.value = `真实硬件实时数据 · ${hardware.matched_by === 'task_id' ? '任务匹配' : '设备匹配'}`
    return
  }
  telemetry.value = local
  telemetrySource.value = local ? 'local' : 'none'
  telemetryMessage.value = hardware
    ? `真实接口在线，但未找到任务 ${task.value.task_id} / 设备 ${task.value.device_id} 的数据`
    : '真实硬件接口暂不可用，当前使用本地后端数据'
}

async function simulateInitialTelemetry() {
  if (!task.value || telemetryBusy.value) return
  telemetryBusy.value = true
  try {
    const min = Number(task.value.temperature_min ?? 2)
    const max = Number(task.value.temperature_max ?? 8)
    const temperature = Number(((min + max) / 2).toFixed(1))
    await taskService.simulateLocalReading(
      task.value.device_id,
      temperature,
      60,
      task.value.task_id,
    )
    telemetry.value = await taskService.getLatestTelemetry(task.value.task_id)
    telemetrySource.value = 'local'
    telemetryMessage.value = '本地测试数据（仅用于联调）'
    uni.showToast({ title: '测试设备数据已生成', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 3000 })
  } finally { telemetryBusy.value = false }
}

async function issueQr() {
  if (!taskId.value || !isIssuer.value) return
  const type = isArrivalHandoff.value ? 'carrier_to_receiver' : 'sender_to_carrier'
  const targetId = isArrivalHandoff.value ? task.value?.receiver_user_id : task.value?.carrier_user_id
  qr.value = await taskService.createHandoffQr(taskId.value, type, targetId)
  countdown.value = qr.value.ttl_seconds
  handoff.value = await taskService.getHandoff(qr.value.handoff_id)
}

async function refreshHandoff() {
  if (!handoff.value?.handoff_id) return
  try {
    handoff.value = await taskService.getHandoff(handoff.value.handoff_id)
    if (handoff.value.status === 'confirmed') {
      uni.showToast({ title: '交接已经完成', icon: 'success' })
      if (taskId.value) task.value = await taskService.getTask(taskId.value)
    }
  } catch { /* 轮询失败不打断当前页面 */ }
}

function tokenFromPayload(payload: string) {
  const match = payload.match(/[?&]token=([^&]+)/)
  return match ? decodeURIComponent(match[1]) : payload.trim()
}

async function acceptToken(token: string) {
  const verified = await taskService.verifyHandoffQr(tokenFromPayload(token))
  handoff.value = verified
  taskId.value = verified.task_id
  countdown.value = Math.max(0, Math.floor((new Date(verified.expires_at).getTime() - Date.now()) / 1000))
  await loadTask()
}

function scanHandoff() {
  uni.scanCode({
    scanType: ['qrCode'],
    success: async ({ result }) => {
      try { await acceptToken(result); uni.showToast({ title: '动态码有效', icon: 'success' }) }
      catch (e) { uni.showToast({ title: errorMessage(e), icon: 'none', duration: 2500 }) }
    },
    fail: () => uni.showToast({ title: '未识别到交接二维码', icon: 'none' }),
  })
}

function authorizeCamera() {
  return new Promise<void>((resolve, reject) => {
    uni.authorize({
      scope: 'scope.camera',
      success: () => resolve(),
      fail: reject,
    })
  })
}

async function openCamera() {
  if (!handoff.value) return uni.showToast({ title: '请先生成或扫描动态码', icon: 'none' })
  if (currentFaceVerified.value) return uni.showToast({ title: '当前账号已完成人脸检测', icon: 'none' })
  cameraFailure.value = ''
  cameraFailed.value = isDevTools
  cameraVisible.value = true
  if (isDevTools) {
    cameraFailure.value = '微信开发者工具不支持 Camera 组件实时画面，请选择正脸照片联调，或使用真机实时拍摄。'
    return
  }
  try {
    await authorizeCamera()
  } catch {
    cameraVisible.value = false
    uni.showModal({
      title: '需要摄像头权限',
      content: '人脸验证需要使用前置摄像头，请在小程序设置中允许摄像头权限。',
      confirmText: '去设置',
      success: ({ confirm }) => {
        if (!confirm) return
        uni.openSetting({
          success: ({ authSetting }) => {
            if (authSetting['scope.camera']) {
              cameraFailed.value = false
              cameraVisible.value = true
            }
          },
        })
      },
    })
  }
}

function handleCameraError(event?: { detail?: { errMsg?: string } }) {
  cameraFailed.value = true
  cameraFailure.value = event?.detail?.errMsg
    ? `摄像头启动失败：${event.detail.errMsg}`
    : '摄像头启动失败，请检查微信与小程序的摄像头权限。'
}

function readImageBase64(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    uni.getFileSystemManager().readFile({
      filePath,
      encoding: 'base64',
      success: ({ data }) => resolve(String(data)),
      fail: reject,
    })
  })
}

async function verifyFaceFile(filePath: string, source: 'camera' | 'album') {
  if (!handoff.value) throw new Error('交接会话不存在')
  const imageBase64 = await readImageBase64(filePath)
  const result = await taskService.verifyFace(handoff.value.handoff_id, imageBase64)
  if (!result.verified) {
    const score = Math.round(Number(result.quality_score || 0) * 100)
    throw new Error(`人脸检测未通过（质量 ${score} 分），请保持单人正脸、光线充足后重试`)
  }
  await refreshHandoff()
  cameraVisible.value = false
  const score = Math.round(Number(result.quality_score || 0) * 100)
  uni.showToast({ title: `${source === 'album' ? '照片' : '人脸'}检测通过 ${score} 分`, icon: 'success' })
}

async function captureFace() {
  if (faceBusy.value || !handoff.value || cameraFailed.value) return
  faceBusy.value = true
  try {
    const context = uni.createCameraContext()
    const photo = await new Promise<{ tempImagePath: string }>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('拍照超时，请检查摄像头权限后重试')), 8000)
      context.takePhoto({
        quality: 'normal',
        success: (result) => { clearTimeout(timeout); resolve(result) },
        fail: (reason) => { clearTimeout(timeout); reject(reason) },
      })
    })
    await verifyFaceFile(photo.tempImagePath, 'camera')
  } catch (e) {
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 3200 })
  } finally { faceBusy.value = false }
}

async function chooseFaceImage() {
  if (faceBusy.value || !handoff.value) return
  faceBusy.value = true
  try {
    const selectedPath = await new Promise<string>((resolve, reject) => {
      uni.chooseImage({
        count: 1,
        sizeType: ['compressed'],
        sourceType: ['album'],
        success: ({ tempFilePaths }) => {
          const paths = Array.isArray(tempFilePaths) ? tempFilePaths : [tempFilePaths]
          resolve(String(paths[0] || ''))
        },
        fail: reject,
      })
    })
    if (!selectedPath) throw new Error('没有选择照片')
    await verifyFaceFile(selectedPath, 'album')
  } catch (e) {
    const message = errorMessage(e)
    if (!/cancel/i.test(message)) uni.showToast({ title: message, icon: 'none', duration: 3200 })
  } finally { faceBusy.value = false }
}

async function simulateFace() {
  if (faceBusy.value || !handoff.value) return
  faceBusy.value = true
  try {
    await taskService.simulateLocalFace(handoff.value.handoff_id)
    cameraVisible.value = false
    await refreshHandoff()
    uni.showToast({ title: '本地模拟人脸检测通过', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 2800 })
  } finally { faceBusy.value = false }
}

async function submit() {
  if (!handoff.value || submitting.value) return
  submitting.value = true
  try {
    const result = await taskService.confirmHandoff(handoff.value.handoff_id, note.value)
    task.value = result.task
    confirmVisible.value = false
    uni.showToast({ title: '责任交接成功', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 800)
  } catch (e) {
    confirmVisible.value = false
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 2600 })
  } finally { submitting.value = false }
}

function verifyOrConfirm() {
  if (!issuerVerified.value) return uni.showToast({ title: '交出人尚未完成人脸检测', icon: 'none' })
  if (!recipientVerified.value) return uni.showToast({ title: '接收人尚未扫码并完成人脸检测', icon: 'none' })
  confirmVisible.value = true
}

function primaryAction() {
  if (handoffConfirmed.value || isIssuer.value) return
  if (!qrVerified.value) return scanHandoff()
  if (!recipientVerified.value) return openCamera()
  verifyOrConfirm()
}

async function bootstrap(query?: Record<string, string | undefined>) {
  loading.value = true; error.value = ''
  try {
    sessionStore.restore()
    if (query?.token) incomingToken.value = String(query.token)
    if (query?.task_id) taskId.value = String(query.task_id)
    const token = incomingToken.value
    if (token) await acceptToken(token)
    else if (taskId.value) {
      await loadTask()
      if (isIssuer.value) await issueQr()
    } else throw new Error('缺少运单或动态码')
  } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}

onLoad((query) => {
  bootstrap(query as Record<string, string | undefined>)
  timer = setInterval(async () => {
    if (countdown.value > 0) countdown.value -= 1
    else if (isIssuer.value && handoff.value?.status === 'pending') {
      try { await issueQr() } catch { /* 下一轮重试 */ }
    }
  }, 1000)
  poller = setInterval(refreshHandoff, 2500)
  telemetryPoller = setInterval(refreshTelemetry, 5000)
})
onUnload(() => {
  if (timer) clearInterval(timer)
  if (poller) clearInterval(poller)
  if (telemetryPoller) clearInterval(telemetryPoller)
})
</script>

<template>
  <view class="page handoff-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="bootstrap()" />

    <template v-else-if="task">
      <view class="handoff-head"><view>▤　运单号：{{ task.task_id }}</view><text>{{ isArrivalHandoff ? '到达交接' : isIssuer ? '发出交接' : '接收交接' }}</text></view>
      <view class="packing-status" :class="{ complete: packingComplete }">
        <view class="packing-icon">{{ packingComplete ? '✓' : '!' }}</view>
        <view>
          <b>{{ isArrivalHandoff ? '运输已到达交接阶段' : packingComplete ? '装箱已完成' : '装箱尚未完成' }}</b>
          <text>{{ isArrivalHandoff ? '下一步由指定接收方扫码、人脸核验并确认到达' : packingComplete ? '设备绑定与首条监测预检已通过，下一步由承运人扫码接收' : '请返回创建与预检页面完成设备绑定和首条监测预检' }}</text>
        </view>
      </view>
      <view class="countdown">◷　{{ qrVerified ? '动态码已由接收方校验' : handoff ? `动态码剩余 ${countdown}s` : '等待扫描动态码' }}<text v-if="isIssuer" @tap="issueQr">↻ 刷新</text><text v-else-if="!qrVerified" @tap="scanHandoff">▣ 扫码</text></view>

      <view class="qr-card">
        <image v-if="qr?.qr_image_data_url && isIssuer" class="qr-image" :src="qr.qr_image_data_url" mode="aspectFit" />
        <view v-else class="scan-placeholder" @tap="!qrVerified && scanHandoff()"><b>▣</b><text>{{ qrVerified ? '动态码已校验' : '点击扫描交接二维码' }}</text></view>
        <view class="qr-pill">{{ qrVerified ? '接收方已扫码' : '一次性动态码' }} · {{ countdown }}s</view>
        <p>动态码只绑定本运单、本次交接和当前接收账号</p>
      </view>

      <view class="people">
        <view><label>交出人</label><view class="face" :class="{ verified: issuerVerified }" @tap="isIssuer && openCamera()">{{ isIssuer ? sessionStore.user?.name?.slice(0,1) : '发' }}</view><b>{{ isIssuer ? sessionStore.user?.name : isArrivalHandoff ? task.carrier : task.sender }}</b><text :class="{ok:issuerVerified}">{{ issuerVerified ? '检测通过' : isIssuer ? '点击检测人脸' : '等待交出人检测' }}</text><p>登录身份 + 摄像头正脸检测</p></view>
        <view><label>接收人</label><view class="face" :class="{ verified: recipientVerified }" @tap="!isIssuer && openCamera()">{{ !isIssuer ? sessionStore.user?.name?.slice(0,1) : (isArrivalHandoff ? task.receiver : task.carrier).slice(0,1) }}</view><b>{{ !isIssuer ? sessionStore.user?.name : isArrivalHandoff ? task.receiver : task.carrier }}</b><text :class="{ok:recipientVerified}">{{ recipientVerified ? '检测通过' : isIssuer ? '等待扫码检测' : '点击检测人脸' }}</text><p>接收账号需先扫描动态码</p></view>
      </view>

      <view class="verify-flow"><view :class="{off:!qrVerified}">▣<text>动态码{{ qrVerified?'有效':'待扫' }}</text></view><i/><view :class="{off:!(issuerVerified&&recipientVerified)}">♟<text>双方人脸</text></view><i/><view :class="{off:!ready}">✓<text>责任转移</text></view></view>

      <view class="card summary-card"><view class="row"><view class="task-code">任务 {{ task.task_id }}</view><StatusTag :status="task.status" /></view><view class="sample-name">{{ task.sample_name }}</view><view class="route"><text>{{ task.sender }}</text><text class="arrow">→</text><text>{{ task.receiver }}</text></view></view>

      <view class="card check-card">
        <view class="section-heading"><view class="section-title">交接核对</view><view class="section-hint">后端实时校验</view></view>
        <view class="check-row"><view class="check-icon">✓</view><view class="check-content"><view class="check-label">绑定设备</view><view class="check-value">{{ task.device_id }}</view></view></view>
        <view class="check-row"><view class="check-icon" :class="{warning:!recipientVerified}">{{ recipientVerified?'✓':'!' }}</view><view class="check-content"><view class="check-label">{{ isArrivalHandoff ? '接收人员' : '承运人员' }}</view><view class="check-value">{{ isArrivalHandoff ? task.receiver : task.carrier }} · {{ recipientVerified?'身份已核验':'等待扫码' }}</view></view></view>
        <view class="check-row"><view class="check-icon" :class="{ warning: !telemetry }">{{ telemetry ? '✓' : '!' }}</view><view class="check-content"><view class="check-label">初始设备数据 <text v-if="telemetrySource !== 'none'" class="source-tag" :class="telemetrySource">{{ telemetrySource === 'hardware' ? '真实硬件' : '本地测试' }}</text></view><view v-if="telemetry" class="check-value">{{ telemetry.temperature }}℃ · 湿度 {{ telemetry.humidity }}% · {{ telemetry.box_status }}</view><view v-else class="check-value warning-text">暂无匹配数据，请确认设备状态</view><view v-if="telemetryMessage" class="test-hint">{{ telemetryMessage }}</view><view v-if="isLocalSimulation && !telemetry" class="test-hint">无硬件联调时，可生成一条绑定到当前运单的正常数据。</view></view><button v-if="isLocalSimulation && !telemetry" class="telemetry-test" :disabled="telemetryBusy" @tap="simulateInitialTelemetry">{{ telemetryBusy ? '生成中…' : '生成测试数据' }}</button></view>
      </view>

      <view class="card note-card"><view class="section-heading"><view class="section-title">交接备注</view><view class="section-hint">将上传并留痕</view></view><textarea v-model="note" maxlength="200" placeholder="记录本次交接需要说明的事项…" class="textarea" /><view class="note-footer"><text>随交接记录保存</text><text>{{ note.length }}/200</text></view></view>

      <button class="primary submit-button" :disabled="primaryDisabled" @tap="primaryAction">{{ primaryText }}</button>

      <view v-if="cameraVisible" class="camera-mask">
        <view class="camera-panel">
          <view class="camera-title">当前账号：{{ sessionStore.user?.name }} · {{ isIssuer ? '交出人' : '接收人' }}</view>
          <camera v-if="!cameraFailed" class="camera" device-position="front" flash="off" @error="handleCameraError"><cover-view class="face-guide">请将单人正脸保持在框内</cover-view></camera>
          <view v-else class="camera-fallback"><b>摄像头暂不可用</b><text>{{ cameraFailure }}</text><text class="fallback-hint">开发者工具可选择一张单人正脸照片完成接口联调；正式交接请使用真机实时拍摄。</text></view>
          <view class="camera-actions">
            <button @tap="cameraVisible=false">取消</button>
            <button v-if="!cameraFailed" class="capture" :disabled="faceBusy" @tap="captureFace">{{ faceBusy?'检测中…':'拍摄并检测' }}</button>
            <button v-if="cameraFailed || isLocalSimulation" class="album" :disabled="faceBusy" @tap="chooseFaceImage">{{ faceBusy?'检测中…':'选择正脸照片' }}</button>
            <button v-if="isLocalSimulation" class="simulate" :disabled="faceBusy" @tap="simulateFace">本地模拟</button>
          </view>
          <p>当前为 OpenCV 单人正脸检测，不等同于商业级活体身份比对</p>
        </view>
      </view>

      <ConfirmDialog :visible="confirmVisible" title="确认责任转移？" :content="`运单 ${task.task_id} 的动态码和双方人脸检测均已通过，确认后进入运输中。`" confirm-text="确认转移" :loading="submitting" @cancel="confirmVisible=false" @confirm="submit" />
    </template>
  </view>
</template>

<style scoped>
.handoff-page{background:#fbfcf9}.handoff-head,.countdown,.verify-flow,.check-row,.camera-actions{display:flex;align-items:center}.handoff-head,.countdown{justify-content:space-between}.handoff-head{padding:20rpx 24rpx;border:1rpx solid #dce9cf;border-radius:18rpx;background:#fff;font-size:23rpx}.handoff-head text{padding:10rpx 18rpx;border-radius:15rpx;color:#53ac06;background:#eff9e7}.countdown{margin:16rpx 0;padding:13rpx 20rpx;border-radius:999rpx;color:#5d771d;background:#fff9df;font-size:20rpx}.countdown text{color:#4da900}.qr-card{padding:27rpx;border:1rpx solid #dfe8d6;border-radius:24rpx;background:#fff;text-align:center}.qr-image{width:330rpx;height:330rpx}.scan-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;width:330rpx;height:330rpx;margin:auto;border:4rpx dashed #78bd42;border-radius:20rpx;color:#4ca900;background:#f6fcef}.scan-placeholder b{font-size:70rpx}.scan-placeholder text{margin-top:18rpx;font-size:22rpx}.qr-pill{display:inline-block;margin-top:15rpx;padding:7rpx 17rpx;border-radius:999rpx;color:#58ad0a;background:#eff8e7;font-size:19rpx}.qr-card p{margin:9rpx 0 0;color:#8a9286;font-size:19rpx}.people{display:grid;grid-template-columns:1fr 1fr;gap:18rpx;margin-top:20rpx}.people>view{position:relative;padding:25rpx 16rpx;border:1rpx solid #dfe8d6;border-radius:22rpx;background:#fff;text-align:center}.people label{position:absolute;left:0;top:0;padding:7rpx 22rpx;border-radius:22rpx 0 18rpx;color:#4da500;background:#eff9e7;font-size:18rpx}.face{display:flex;align-items:center;justify-content:center;width:90rpx;height:90rpx;margin:18rpx auto 10rpx;border:7rpx solid #dfe5db;border-radius:50%;color:#77906a;background:#eef2eb;font-size:38rpx}.face.verified{border-color:#68c20f;box-shadow:0 0 0 7rpx #eff9e7}.people b,.people text,.people p{display:block}.people b{font-size:25rpx}.people text{margin:8rpx auto;width:max-content;padding:4rpx 11rpx;border-radius:999rpx;color:#868e82;background:#edf0eb;font-size:17rpx}.people text.ok{color:#4ca500;background:#eff9e7}.people p{margin:9rpx 0 0;color:#7e8779;font-size:17rpx}.verify-flow{justify-content:space-around;margin:20rpx 0;padding:18rpx;border:1rpx solid #e1e8db;border-radius:20rpx;background:#fff}.verify-flow view{display:flex;flex-direction:column;align-items:center;color:#57ad09;font-size:28rpx}.verify-flow view.off{color:#aab3a4}.verify-flow text{margin-top:7rpx;font-size:18rpx}.verify-flow i{width:70rpx;border-top:2rpx dashed #78b83d}.summary-card{border-color:#dfeacc;background:linear-gradient(145deg,#fff,#f8fdf3)}.task-code{color:#8c9bb0;font-size:24rpx}.sample-name{margin:12rpx 0 22rpx;color:#102a43;font-size:38rpx;font-weight:780}.route{display:flex;gap:14rpx;color:#63778f;font-size:26rpx}.arrow{color:#9caabd}.check-row{gap:20rpx;padding:22rpx 0;border-top:1rpx solid #eef1f6}.check-icon{display:flex;align-items:center;justify-content:center;width:52rpx;height:52rpx;flex:0 0 auto;border-radius:17rpx;color:#fff;background:#62b894;font-size:24rpx;font-weight:700}.check-icon.warning{background:#f2b84b}.check-content{flex:1;min-width:0}.check-label{color:#94a2b4;font-size:22rpx}.check-value{margin-top:5rpx;color:#40566e;font-size:27rpx;font-weight:620}.warning-text{color:#b47b18}.textarea{width:100%;height:180rpx;padding:24rpx;box-sizing:border-box;border:1rpx solid #e8ecf3;border-radius:18rpx;color:#40566e;background:#f5f7fb;line-height:1.6}.note-footer{display:flex;justify-content:space-between;margin-top:12rpx;color:#a0adbd;font-size:20rpx}.submit-button{margin-top:28rpx}.camera-mask{position:fixed;z-index:100;inset:0;display:flex;align-items:flex-end;background:rgba(0,0,0,.68)}.camera-panel{width:100%;padding:24rpx 24rpx calc(24rpx + env(safe-area-inset-bottom));border-radius:32rpx 32rpx 0 0;background:#fff}.camera{position:relative;width:100%;height:650rpx;border-radius:24rpx;overflow:hidden}.face-guide{margin:220rpx auto 0;width:300rpx;height:360rpx;border:6rpx solid #78dc17;border-radius:48%;color:#fff;text-align:center;line-height:40rpx}.camera-actions{gap:20rpx;margin-top:20rpx}.camera-actions button{flex:1}.camera-actions .capture{color:#fff;background:#55b600}.camera-panel p{color:#8a9286;text-align:center;font-size:18rpx}
.packing-status{display:flex;align-items:center;gap:18rpx;margin-top:16rpx;padding:20rpx 22rpx;border:1rpx solid #f0d7a2;border-radius:18rpx;color:#9b6b17;background:#fff8e8}.packing-status.complete{border-color:#cae8ac;color:#438f08;background:#f2faeb}.packing-icon{display:flex;align-items:center;justify-content:center;width:52rpx;height:52rpx;flex:0 0 auto;border-radius:50%;color:#fff;background:#e2a73b;font-size:27rpx;font-weight:800}.packing-status.complete .packing-icon{background:#62b894}.packing-status b,.packing-status text{display:block}.packing-status b{font-size:25rpx}.packing-status text{margin-top:5rpx;font-size:19rpx;line-height:1.45}
.camera-actions{gap:12rpx}.camera-actions button{padding:0 8rpx;font-size:20rpx}.camera-actions .simulate{color:#4e9f0d;background:#eff9e7}
.camera-title{margin-bottom:18rpx;color:#344a60;font-size:25rpx;font-weight:700;text-align:center}.camera-fallback{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:420rpx;padding:35rpx;border:2rpx dashed #9acb70;border-radius:24rpx;color:#48633b;background:#f6fcef;text-align:center}.camera-fallback b{font-size:32rpx}.camera-fallback text{margin-top:20rpx;font-size:22rpx;line-height:1.6}.camera-fallback .fallback-hint{color:#83917c;font-size:19rpx}.camera-actions .album{color:#fff;background:#55b600}
.test-hint{margin-top:8rpx;color:#8a9784;font-size:18rpx;font-weight:400;line-height:1.45}.telemetry-test{width:156rpx;height:58rpx;flex:0 0 auto;padding:0;border:1rpx solid #70bd32;border-radius:15rpx;color:#50a70c;background:#f2faeb;font-size:18rpx;line-height:56rpx}.telemetry-test[disabled]{opacity:.55}
.source-tag{display:inline-block;margin-left:8rpx;padding:2rpx 9rpx;border-radius:999rpx;font-size:16rpx}.source-tag.hardware{color:#398e00;background:#e9f8db}.source-tag.local{color:#a87310;background:#fff3d9}
</style>
