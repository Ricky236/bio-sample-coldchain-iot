<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { errorMessage } from '@/services/request'
import { taskService } from '@/services/tasks'
import { useSessionStore } from '@/stores/session'
import type { EvidenceFile, TraceReport } from '@/types/api'

const session = useSessionStore()
const taskId = ref('')
const report = ref<TraceReport | null>(null)
const loading = ref(true)
const submitting = ref(false)
const uploading = ref(false)
const error = ref('')
const reason = ref('')
const action = ref<'sign' | 'reject' | null>(null)
const evidenceFiles = ref<EvidenceFile[]>([])

const isAssignedReceiver = computed(() => Boolean(
  report.value
  && (
    session.user?.role === 'admin'
    || (
      session.user?.role === 'receiver'
      && String(report.value.task.receiver_user_id || '') === String(session.user?.id || '')
    )
  ),
))
const allowed = computed(() => Boolean(report.value?.task.status === 'arrived' && isAssignedReceiver.value))
const permissionHint = computed(() => {
  if (!report.value) return ''
  if (!report.value.task.receiver_user_id) return '该运单未绑定接收方账号，请发货方先补充接收人'
  if (!isAssignedReceiver.value) return '当前账号不是该运单指定的接收方，只能查看验收报告'
  if (report.value.task.status === 'in_transit') return '请先由承运人发起到达交接，接收方扫码并完成人脸核验'
  return ''
})
const alertTypes = computed(() => new Set(report.value?.events.map((item) => item.event_type) || []))
const riskScore = computed(() => {
  let score = 100
  if ((report.value?.summary.max_temperature || 0) > 8) score -= 12
  if (alertTypes.value.has('BOX_OPEN')) score -= 8
  if (alertTypes.value.has('IMPACT') || alertTypes.value.has('FREE_FALL')) score -= 5
  if (report.value?.events.length) score -= 3
  return Math.max(score, 0)
})
const recommendation = computed(() => riskScore.value >= 90 ? '可接收' : riskScore.value >= 70 ? '需要复核' : '建议隔离')

async function load() {
  loading.value = true; error.value = ''
  try { report.value = await taskService.getTraceReport(taskId.value) }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
function choose(next: 'sign' | 'reject') {
  if (!allowed.value) return uni.showToast({ title: '当前任务状态不可验收', icon: 'none' })
  if (next === 'reject' && !reason.value.trim()) return uni.showToast({ title: '请填写拒收原因', icon: 'none' })
  action.value = next
}
async function chooseEvidence() {
  if (!allowed.value) return uni.showToast({ title: permissionHint.value || '当前不可上传验收证据', icon: 'none' })
  if (uploading.value) return
  try {
    const paths = await new Promise<string[]>((resolve, reject) => {
      uni.chooseImage({
        count: 3, sizeType: ['compressed'], sourceType: ['camera', 'album'],
        success: ({ tempFilePaths }) => resolve((Array.isArray(tempFilePaths) ? tempFilePaths : [tempFilePaths]).map(String)),
        fail: reject,
      })
    })
    uploading.value = true
    for (const path of paths) {
      const file = await taskService.uploadEvidence(path, taskId.value, 'acceptance_photo', 'task', taskId.value)
      evidenceFiles.value.push(file)
    }
    uni.showToast({ title: `已上传 ${paths.length} 份证据`, icon: 'success' })
  } catch (e) {
    const message = errorMessage(e)
    if (!/cancel/i.test(message)) uni.showToast({ title: message, icon: 'none', duration: 3000 })
  } finally { uploading.value = false }
}
async function submit() {
  if (!action.value || submitting.value) return
  if (!allowed.value) {
    action.value = null
    return uni.showToast({ title: permissionHint.value || '当前账号无验收权限', icon: 'none', duration: 3000 })
  }
  submitting.value = true
  try {
    if (action.value === 'sign') await taskService.signTask(taskId.value)
    else await taskService.rejectTask(taskId.value, reason.value)
    action.value = null
    await load()
    uni.showToast({ title: '验收结果已保存', icon: 'success' })
  } catch (e) {
    action.value = null
    uni.showToast({ title: errorMessage(e), icon: 'none', duration: 3000 })
  } finally { submitting.value = false }
}
onLoad((query) => {
  if (!session.requireSession()) return
  taskId.value = String(query?.task_id || '')
  if (taskId.value) load(); else { loading.value = false; error.value = '缺少 task_id' }
})
onShow(() => { if (taskId.value && !loading.value) load() })
</script>

<template>
  <view class="page acceptance-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <template v-else-if="report">
      <view class="hero">
        <view><text>ARRIVAL INSPECTION</text><b>到达验收</b><small>{{ report.task.sample_name }} · {{ report.task.task_id }}</small></view>
        <StatusTag :status="report.task.status" />
      </view>
      <view class="risk"><view class="score">{{ riskScore }}<small>风险分</small></view><view><label>系统验收建议</label><b>{{ recommendation }}</b><p>依据温度、开箱和碰撞事件自动计算，仅作为质量复核参考</p></view></view>
      <view v-if="permissionHint" class="permission-hint">! {{ permissionHint }}</view>
      <view class="card">
        <view class="section-heading"><view class="section-title">全程摘要</view><view class="section-hint">{{ report.summary.total_records }} 条记录</view></view>
        <view class="metrics"><view><b>{{ report.summary.min_temperature ?? '--' }}℃</b><text>最低温度</text></view><view><b>{{ report.summary.max_temperature ?? '--' }}℃</b><text>最高温度</text></view><view><b>{{ report.summary.avg_temperature ?? '--' }}℃</b><text>平均温度</text></view><view><b>{{ report.summary.event_count }}</b><text>异常事件</text></view></view>
      </view>
      <view class="card">
        <view class="section-title">验收检查</view>
        <view class="check"><i :class="{ issue: (report.summary.max_temperature || 0) > 8 }">{{ (report.summary.max_temperature || 0) > 8 ? '!' : '✓' }}</i><view><b>温控记录</b><text>{{ (report.summary.max_temperature || 0) > 8 ? '存在温度越限，需要复核' : '温度记录正常' }}</text></view></view>
        <view class="check"><i :class="{ issue: alertTypes.has('BOX_OPEN') }">{{ alertTypes.has('BOX_OPEN') ? '!' : '✓' }}</i><view><b>箱体与封签</b><text>{{ alertTypes.has('BOX_OPEN') ? '记录到运输中开箱事件' : '未记录异常开箱' }}</text></view></view>
        <view class="check"><i :class="{ issue: alertTypes.has('IMPACT') }">{{ alertTypes.has('IMPACT') ? '!' : '✓' }}</i><view><b>运输冲击</b><text>{{ alertTypes.has('IMPACT') ? '存在明显碰撞' : '未检测到严重冲击' }}</text></view></view>
      </view>
      <view class="card">
        <view class="section-heading"><view class="section-title">验收证据</view><view class="section-hint">JPEG/PNG，单文件不超过 5MB</view></view>
        <button class="upload" :disabled="uploading || !allowed" @tap="chooseEvidence">▣ {{ uploading ? '正在上传…' : '拍照或从相册选择' }}</button>
        <view v-if="evidenceFiles.length" class="files"><view v-for="file in evidenceFiles" :key="file.file_id"><text>✓ {{ file.file_name }}</text><small>{{ file.file_id }}</small></view></view>
      </view>
      <view class="card">
        <view class="section-heading"><view class="section-title">拒收/复核说明</view><view class="section-hint">拒收时必填</view></view>
        <textarea v-model="reason" maxlength="200" placeholder="填写异常情况、复核意见或拒收原因" />
        <view class="count">{{ reason.length }}/200</view>
      </view>
      <view v-if="allowed" class="decisions"><button class="accept" @tap="choose('sign')">✓ 确认接收</button><button class="reject" @tap="choose('reject')">× 拒绝接收</button></view>
      <view v-else class="result">验收流程已结束：{{ report.task.status === 'signed' ? '已签收' : report.task.status === 'rejected' ? '已拒收' : report.task.status }}</view>
      <ConfirmDialog :visible="Boolean(action)" :title="action === 'sign' ? '确认接收货物？' : '确认拒绝接收？'" :content="action === 'sign' ? `已上传 ${evidenceFiles.length} 份本次验收证据。` : `拒收原因：${reason}`" :confirm-text="action === 'sign' ? '确认接收' : '确认拒收'" :loading="submitting" @cancel="action = null" @confirm="submit" />
    </template>
  </view>
</template>

<style scoped>
.acceptance-page{background:#fbfcf9}.hero{display:flex;align-items:flex-start;justify-content:space-between;padding:25rpx 5rpx}.hero text,.hero b,.hero small{display:block}.hero text{color:#55ae06;font-size:18rpx;font-weight:760;letter-spacing:3rpx}.hero b{margin:7rpx 0;color:#173149;font-size:40rpx}.hero small{color:#7d8da2;font-size:22rpx}.risk{display:grid;grid-template-columns:125rpx 1fr;align-items:center;gap:24rpx;margin-bottom:22rpx;padding:27rpx;border:1rpx solid #cde9a6;border-radius:27rpx;color:#274019;background:linear-gradient(135deg,#efffda,#d7ffa3)}.score{display:flex;flex-direction:column;align-items:center;justify-content:center;width:112rpx;height:112rpx;border:5rpx solid #82d71a;border-radius:50%;background:#fff;font-size:37rpx;font-weight:800}.score small{font-size:17rpx}.risk label,.risk b{display:block}.risk b{margin:5rpx 0;font-size:32rpx}.risk p{margin:0;color:#62804e;font-size:18rpx}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:13rpx;margin-top:18rpx}.metrics view{padding:18rpx;border-radius:16rpx;background:#f5f8f2}.metrics b,.metrics text{display:block}.metrics b{color:#344d65;font-size:28rpx}.metrics text{margin-top:5rpx;color:#96a4b5;font-size:19rpx}.check{display:flex;align-items:center;gap:18rpx;padding:20rpx 0;border-top:1rpx solid #edf1ed}.check:first-of-type{margin-top:15rpx}.check i{display:flex;align-items:center;justify-content:center;width:48rpx;height:48rpx;border-radius:15rpx;color:#fff;background:#54ae89}.check i.issue{background:#e06972}.check b,.check text{display:block}.check b{color:#41586f;font-size:24rpx}.check text{margin-top:5rpx;color:#96a4b5;font-size:19rpx}.upload{height:75rpx;margin-top:18rpx;border:1rpx solid #73c437;border-radius:16rpx;color:#51aa03;background:#f7fdf1;font-size:22rpx;line-height:75rpx}.files{margin-top:12rpx}.files view{display:flex;justify-content:space-between;padding:12rpx;border-bottom:1rpx solid #edf1eb;color:#4d9d10;font-size:18rpx}.files small{color:#9aa394}textarea{width:100%;height:145rpx;margin-top:15rpx;padding:18rpx;box-sizing:border-box;border:1rpx solid #e5eae2;border-radius:16rpx;background:#f6f8f5}.count{text-align:right;color:#9da9a0;font-size:18rpx}.decisions{display:grid;grid-template-columns:1.4fr 1fr;gap:16rpx;margin:22rpx 0}.decisions button{height:86rpx;border-radius:18rpx;font-size:25rpx;font-weight:700;line-height:86rpx}.accept{color:#fff;background:linear-gradient(135deg,#8bdf1d,#4eae00)}.reject{border:2rpx solid #ef4549;color:#db3338;background:#fff}.result{padding:22rpx;border-radius:18rpx;color:#5c7086;background:#eaf0f6;text-align:center}
.permission-hint{margin-bottom:22rpx;padding:20rpx 24rpx;border:1rpx solid #f1d294;border-radius:18rpx;color:#a56a08;background:#fff7e5;font-size:22rpx;line-height:1.55}
</style>
