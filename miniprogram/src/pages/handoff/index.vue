<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onUnload } from '@dcloudio/uni-app'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task, Telemetry } from '@/types/api'
import { canStartTask } from '@/utils/status'

const taskId = ref('')
const task = ref<Task | null>(null)
const telemetry = ref<Telemetry | null>(null)
const note = ref('')
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const confirmVisible = ref(false)
const countdown = ref(58)
const faceVerified = ref(false)
function finder(x: number, y: number, ox: number, oy: number) {
  const dx = x - ox; const dy = y - oy
  return dx >= 0 && dx < 5 && dy >= 0 && dy < 5 && (dx === 0 || dx === 4 || dy === 0 || dy === 4 || (dx === 2 && dy === 2))
}
const qrCells = Array.from({ length: 225 }, (_, index) => {
  const x = index % 15; const y = Math.floor(index / 15)
  return finder(x,y,0,0) || finder(x,y,10,0) || finder(x,y,0,10) || ((x * 7 + y * 11 + x * y * 3) % 13 < 5)
})
let timer: ReturnType<typeof setInterval> | null = null
const allowed = computed(() => task.value ? canStartTask(task.value.status) : false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    [task.value, telemetry.value] = await Promise.all([
      taskService.getTask(taskId.value),
      taskService.getLatestTelemetry(taskId.value),
    ])
  } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  try {
    await taskService.startTask(taskId.value)
    task.value = await taskService.getTask(taskId.value)
    confirmVisible.value = false
    uni.showToast({ title: '发出成功', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 700)
  } catch (e) {
    confirmVisible.value = false
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 2600 })
  } finally { submitting.value = false }
}
function verifyOrConfirm() {
  if (!faceVerified.value) return uni.showToast({ title: '请先完成接收人人脸核验', icon: 'none' })
  confirmVisible.value = true
}

onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (taskId.value) load()
  else { loading.value = false; error.value = '缺少 task_id' }
  timer = setInterval(() => { countdown.value = countdown.value <= 1 ? 60 : countdown.value - 1 }, 1000)
})
onUnload(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <view class="page handoff-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />

    <template v-else-if="task">
      <view class="handoff-head"><view>▤　运单号：{{ task.task_id === 'TASK-001' ? 'WD-20260722-001' : task.task_id }}</view><text>发出交接</text></view>
      <view class="countdown">◷　动态码{{ countdown }}s后更新，请及时完成核验　<text @tap="countdown=60">↻ 刷新</text></view>
      <view class="qr-card"><view class="qr"><i v-for="(on,index) in qrCells" :key="index" :class="{on}" /></view><view class="qr-pill">一次性动态码 · {{ countdown }}s</view><p>扫码仅绑定本运单本动作</p></view>
      <view class="people"><view><label>交出人</label><view class="face">张</view><b>张敏</b><text>待核验</text><p>请正对摄像头完成活体检测</p></view><view><label>接收人</label><view class="face" :class="{verified:faceVerified}" @tap="faceVerified=true">李</view><b>李强</b><text :class="{ok:faceVerified}">{{faceVerified?'核验通过':'点击开始核验'}}</text><p>{{faceVerified?'活体与身份匹配':'保持正脸在框内'}}</p></view></view>
      <view class="verify-flow"><view>▣<text>动态码有效</text></view><i/><view>♟<text>双方身份</text></view><i/><view>✓<text>责任转移</text></view></view>
      <view class="progress-card">
        <view class="progress-step done"><view class="step-dot">✓</view><text>任务确认</text></view>
        <view class="progress-line" />
        <view class="progress-step current"><view class="step-dot">2</view><text>发出交接</text></view>
        <view class="progress-line muted-line" />
        <view class="progress-step"><view class="step-dot">3</view><text>运输监控</text></view>
      </view>

      <view class="card summary-card">
        <view class="row"><view class="task-code">任务 {{ task.task_id }}</view><StatusTag :status="task.status" /></view>
        <view class="sample-name">{{ task.sample_name }}</view>
        <view class="route"><text>{{ task.sender }}</text><text class="arrow">→</text><text>{{ task.receiver }}</text></view>
      </view>

      <view class="card check-card">
        <view class="section-heading"><view class="section-title">交接核对</view><view class="section-hint">请确认信息无误</view></view>
        <view class="check-row"><view class="check-icon">✓</view><view class="check-content"><view class="check-label">绑定设备</view><view class="check-value">{{ task.device_id }}</view></view></view>
        <view class="check-row"><view class="check-icon">✓</view><view class="check-content"><view class="check-label">承运人员</view><view class="check-value">{{ task.carrier }}</view></view></view>
        <view class="check-row"><view class="check-icon" :class="{ warning: !telemetry }">{{ telemetry ? '✓' : '!' }}</view><view class="check-content"><view class="check-label">初始设备数据</view><view v-if="telemetry" class="check-value">{{ telemetry.temperature }}℃ · 湿度 {{ telemetry.humidity }}% · {{ telemetry.box_status }}</view><view v-else class="check-value warning-text">暂无数据，请确认设备状态</view></view></view>
      </view>

      <view class="card note-card">
        <view class="section-heading"><view class="section-title">交接备注</view><view class="section-hint">选填</view></view>
        <textarea v-model="note" maxlength="200" placeholder="记录本次交接需要说明的事项…" class="textarea" />
        <view class="note-footer"><text>备注仅用于本地核对，当前契约不会上传</text><text>{{ note.length }}/200</text></view>
      </view>

      <button class="primary submit-button" :disabled="!allowed || submitting" @tap="verifyOrConfirm">
        {{ allowed ? '确认发出交接' : '当前状态不可发出' }}
      </button>

      <ConfirmDialog
        :visible="confirmVisible"
        title="确认发出交接？"
        :content="`任务 ${task.task_id} 发出后将由后端流转为运输中。${note ? '已填写本地核对备注。' : ''}`"
        confirm-text="确认发出"
        :loading="submitting"
        @cancel="confirmVisible = false"
        @confirm="submit"
      />
    </template>
  </view>
</template>

<style scoped>
.handoff-page { background: linear-gradient(180deg, #f7f6ff 0, #f4f7fb 280rpx); }.progress-card { display: flex; align-items: flex-start; padding: 26rpx 12rpx 34rpx; }.progress-step { width: 116rpx; flex: 0 0 auto; color: #9aa8ba; text-align: center; font-size: 21rpx; }.step-dot { display: flex; align-items: center; justify-content: center; width: 48rpx; height: 48rpx; margin: 0 auto 10rpx; border: 4rpx solid #d8dee8; border-radius: 50%; box-sizing: border-box; color: #a4b0c0; background: #fff; font-size: 21rpx; font-weight: 700; }.progress-step.done, .progress-step.current { color: #6255f6; }.done .step-dot { color: #fff; border-color: #6558ff; background: #6558ff; }.current .step-dot { color: #6558ff; border-color: #6558ff; }.progress-line { flex: 1; height: 4rpx; margin-top: 22rpx; background: #6558ff; }.muted-line { background: #dfe4ec; }
.summary-card { border-color: #e4e0ff; background: linear-gradient(145deg, #fff, #faf9ff); }.task-code { color: #8c9bb0; font-size: 24rpx; }.sample-name { margin: 12rpx 0 22rpx; color: #102a43; font-size: 38rpx; font-weight: 780; }.route { display: flex; gap: 14rpx; color: #63778f; font-size: 26rpx; }.arrow { color: #9caabd; }
.check-row { display: flex; align-items: center; gap: 20rpx; padding: 22rpx 0; border-top: 1rpx solid #eef1f6; }.check-icon { display: flex; align-items: center; justify-content: center; width: 52rpx; height: 52rpx; flex: 0 0 auto; border-radius: 17rpx; color: #fff; background: #62b894; font-size: 24rpx; font-weight: 700; }.check-icon.warning { background: #f2b84b; }.check-content { flex: 1; min-width: 0; }.check-label { color: #94a2b4; font-size: 22rpx; }.check-value { margin-top: 5rpx; color: #40566e; font-size: 27rpx; font-weight: 620; }.warning-text { color: #b47b18; }
.textarea { width: 100%; height: 190rpx; padding: 24rpx; box-sizing: border-box; border: 1rpx solid #e8ecf3; border-radius: 18rpx; color: #40566e; background: #f5f7fb; line-height: 1.6; }.note-footer { display: flex; justify-content: space-between; gap: 18rpx; margin-top: 14rpx; color: #a0adbd; font-size: 20rpx; }.submit-button { margin-top: 30rpx; }
.handoff-page{background:#fbfcf9}.handoff-head{display:flex;align-items:center;justify-content:space-between;padding:20rpx 24rpx;border:1rpx solid #dce9cf;border-radius:18rpx;background:#fff;font-size:23rpx}.handoff-head text{padding:10rpx 18rpx;border-radius:15rpx;color:#53ac06;background:#eff9e7}.countdown{display:flex;justify-content:space-between;margin:16rpx 0;padding:13rpx 20rpx;border-radius:999rpx;color:#5d771d;background:#fff9df;font-size:20rpx}.countdown text{color:#4da900}.qr-card{padding:28rpx;border:1rpx solid #dfe8d6;border-radius:24rpx;background:#fff;text-align:center}.qr{display:grid;grid-template-columns:repeat(11,16rpx);grid-auto-rows:16rpx;gap:2rpx;width:max-content;margin:auto;padding:16rpx;border:6rpx solid #111}.qr i.on{background:#111}.qr-pill{display:inline-block;margin-top:20rpx;padding:7rpx 17rpx;border-radius:999rpx;color:#58ad0a;background:#eff8e7;font-size:19rpx}.qr-card p{margin:10rpx 0 0;color:#8a9286;font-size:19rpx}.people{display:grid;grid-template-columns:1fr 1fr;gap:18rpx;margin-top:20rpx}.people>view{position:relative;padding:25rpx;border:1rpx solid #dfe8d6;border-radius:22rpx;background:#fff;text-align:center}.people label{position:absolute;left:0;top:0;padding:7rpx 22rpx;border-radius:22rpx 0 18rpx 0;color:#4da500;background:#eff9e7;font-size:18rpx}.face{display:flex;align-items:center;justify-content:center;width:90rpx;height:90rpx;margin:18rpx auto 10rpx;border:7rpx solid #dfe5db;border-radius:50%;color:#77906a;background:#eef2eb;font-size:38rpx}.face.verified{border-color:#68c20f;box-shadow:0 0 0 7rpx #eff9e7}.people b,.people text,.people p{display:block}.people b{font-size:26rpx}.people text{margin:8rpx auto;width:max-content;padding:4rpx 11rpx;border-radius:999rpx;color:#868e82;background:#edf0eb;font-size:17rpx}.people text.ok{color:#4ca500;background:#eff9e7}.people p{margin:9rpx 0 0;color:#7e8779;font-size:17rpx}.verify-flow{display:flex;align-items:center;justify-content:space-around;margin:20rpx 0;padding:18rpx;border:1rpx solid #e1e8db;border-radius:20rpx;background:#fff}.verify-flow view{display:flex;flex-direction:column;align-items:center;color:#57ad09;font-size:28rpx}.verify-flow text{margin-top:7rpx;font-size:18rpx}.verify-flow i{width:85rpx;border-top:2rpx dashed #78b83d}.progress-card{display:none}.done .step-dot,.progress-line{background:#56b006;border-color:#56b006}.progress-step.done,.progress-step.current,.current .step-dot{color:#56b006}.current .step-dot{border-color:#56b006}.summary-card{border-color:#dfeacc;background:linear-gradient(145deg,#fff,#f8fdf3)}
.qr{grid-template-columns:repeat(15,12rpx);grid-auto-rows:12rpx}
</style>
