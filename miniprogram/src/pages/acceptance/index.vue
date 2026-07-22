<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { TraceReport } from '@/types/api'

const taskId = ref('')
const report = ref<TraceReport | null>(null)
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const reason = ref('')
const action = ref<'sign' | 'reject' | null>(null)

const allowed = computed(() => report.value ? ['in_transit', 'arrived'].includes(report.value.task.status) : false)
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
const riskTone = computed(() => riskScore.value >= 90 ? 'safe' : riskScore.value >= 70 ? 'review' : 'danger')

async function load() {
  loading.value = true; error.value = ''
  try { report.value = await taskService.getTraceReport(taskId.value) }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
function choose(next: 'sign' | 'reject') {
  if (!allowed.value) { uni.showToast({ title: '当前任务状态不可验收', icon: 'none' }); return }
  if (next === 'reject' && !reason.value.trim()) { uni.showToast({ title: '请先填写拒收原因', icon: 'none' }); return }
  action.value = next
}
function markDecision(type: 'review' | 'isolate') { uni.showToast({ title: type === 'review' ? '已转质量复核' : '已标记隔离处理', icon: 'success' }) }
function evidence(type: 'photo' | 'sign' | 'note') {
  if (type === 'photo') return uni.chooseImage({ count: 3 })
  uni.showToast({ title: type === 'sign' ? '签名板待真机调用' : '请在说明框填写备注', icon: 'none' })
}
async function submit() {
  if (!action.value || submitting.value) return
  submitting.value = true
  try {
    if (action.value === 'sign') await taskService.signTask(taskId.value)
    else await taskService.rejectTask(taskId.value, reason.value)
    action.value = null
    await load()
    uni.showToast({ title: '验收结果已保存', icon: 'success' })
  } catch (e) { action.value = null; uni.showToast({ title: errorMessage(e), icon: 'none', duration: 2600 }) }
  finally { submitting.value = false }
}

onLoad((query) => {
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
      <view class="accept-hero">
        <view class="row"><view><view class="hero-label">ARRIVAL INSPECTION</view><view class="hero-title">到达验收</view></view><StatusTag :status="report.task.status" /></view>
        <view class="hero-task">{{ report.task.sample_name }} · {{ report.task.task_id }}</view>
      </view>

      <view class="risk-card" :class="riskTone">
        <view class="score-ring"><view class="score">{{ riskScore }}</view><view class="score-unit">风险分</view></view>
        <view class="risk-content"><view class="risk-label">系统验收建议</view><view class="risk-result">{{ recommendation }}</view><view class="risk-note">依据现有温度、开箱和碰撞事件计算，仅作为质量复核参考</view></view>
      </view>

      <view class="card summary-card">
        <view class="section-heading"><view class="section-title">全程摘要</view><view class="section-hint">{{ report.summary.total_records }} 条记录</view></view>
        <view class="summary-grid">
          <view class="summary-item"><view class="summary-value">{{ report.summary.min_temperature ?? '--' }}℃</view><view class="summary-label">最低温度</view></view>
          <view class="summary-item"><view class="summary-value" :class="{ warning: (report.summary.max_temperature || 0) > 8 }">{{ report.summary.max_temperature ?? '--' }}℃</view><view class="summary-label">最高温度</view></view>
          <view class="summary-item"><view class="summary-value">{{ report.summary.avg_temperature ?? '--' }}℃</view><view class="summary-label">平均温度</view></view>
          <view class="summary-item"><view class="summary-value">{{ report.summary.event_count }}</view><view class="summary-label">异常事件</view></view>
        </view>
      </view>

      <view class="card checklist-card">
        <view class="section-heading"><view class="section-title">验收检查</view><view class="section-hint">自动汇总</view></view>
        <view class="check-row"><view class="check-dot" :class="{ issue: (report.summary.max_temperature || 0) > 8 }">{{ (report.summary.max_temperature || 0) > 8 ? '!' : '✓' }}</view><view><view class="check-title">温控记录</view><view class="check-desc">{{ (report.summary.max_temperature || 0) > 8 ? '存在温度越限，需要人工复核' : '全程温度未超过上限' }}</view></view></view>
        <view class="check-row"><view class="check-dot" :class="{ issue: alertTypes.has('BOX_OPEN') }">{{ alertTypes.has('BOX_OPEN') ? '!' : '✓' }}</view><view><view class="check-title">箱体与封签</view><view class="check-desc">{{ alertTypes.has('BOX_OPEN') ? '记录到运输中开箱事件' : '未记录异常开箱' }}</view></view></view>
        <view class="check-row"><view class="check-dot" :class="{ issue: alertTypes.has('IMPACT') }">{{ alertTypes.has('IMPACT') ? '!' : '✓' }}</view><view><view class="check-title">运输冲击</view><view class="check-desc">{{ alertTypes.has('IMPACT') ? '存在明显碰撞，请检查样本盒' : '未检测到严重冲击' }}</view></view></view>
      </view>

      <view class="card reason-card">
        <view class="section-heading"><view class="section-title">拒收/复核说明</view><view class="section-hint">拒收时必填</view></view>
        <textarea v-model="reason" maxlength="200" class="reason-input" placeholder="例如：温度越限且封签异常，需要质量部门复核" />
        <view class="count">{{ reason.length }}/200</view>
      </view>

      <view v-if="allowed" class="decision-grid"><button class="accept" @tap="choose('sign')">✓　确认接收<text>货品符合要求</text></button><button class="review" @tap="markDecision('review')">♙　待质量复核<text>需质量人员复核</text></button><button class="isolate" @tap="markDecision('isolate')">♢　隔离处理<text>隔离并进一步处理</text></button><button class="reject" @tap="choose('reject')">×　拒绝接收<text>不符合接收要求</text></button></view>
      <view v-else class="result-strip">当前任务已完成验收，结果为：{{ report.task.status === 'signed' ? '已签收' : report.task.status === 'rejected' ? '已拒收' : '不可操作' }}</view>
      <view class="card evidence-actions"><view class="section-title">验收证据</view><view><button @tap="evidence('photo')">▣<text>拍照</text></button><button @tap="evidence('sign')">✎<text>签名</text></button><button @tap="evidence('note')">▤<text>备注</text></button></view></view>

      <ConfirmDialog :visible="Boolean(action)" :title="action === 'sign' ? '确认接收货物？' : '确认拒绝接收？'" :content="action === 'sign' ? '提交后任务将完成签收，并记录当前时间。' : `拒收原因：${reason}`" :confirm-text="action === 'sign' ? '确认接收' : '确认拒收'" :loading="submitting" @cancel="action = null" @confirm="submit" />
    </template>
  </view>
</template>

<style scoped>
.acceptance-page { background:linear-gradient(180deg,#eef4ff 0,#f5f7fb 430rpx); }.accept-hero { padding:30rpx 6rpx 28rpx; }.hero-label { color:#8a7fff; font-size:18rpx; font-weight:760; letter-spacing:4rpx; }.hero-title { margin-top:8rpx; color:#173149; font-size:42rpx; font-weight:820; }.hero-task { margin-top:13rpx; color:#7d8da2; font-size:23rpx; }
.risk-card { display:grid; grid-template-columns:135rpx 1fr; align-items:center; gap:25rpx; margin-bottom:22rpx; padding:28rpx; border-radius:29rpx; color:#fff; background:linear-gradient(135deg,#f0a84b,#dd7c39); box-shadow:0 14rpx 30rpx rgba(201,123,49,.2); }.risk-card.safe { background:linear-gradient(135deg,#62b894,#3c9678); }.risk-card.danger { background:linear-gradient(135deg,#eb6b75,#c94250); }.score-ring { display:flex; flex-direction:column; align-items:center; justify-content:center; width:120rpx; height:120rpx; border:6rpx solid rgba(255,255,255,.5); border-radius:50%; background:rgba(255,255,255,.12); }.score { font-size:42rpx; font-weight:840; }.score-unit { font-size:18rpx; opacity:.8; }.risk-label { font-size:20rpx; opacity:.78; }.risk-result { margin:5rpx 0; font-size:36rpx; font-weight:800; }.risk-note { font-size:19rpx; line-height:1.45; opacity:.75; }
.summary-grid { display:grid; grid-template-columns:1fr 1fr; gap:14rpx; }.summary-item { padding:20rpx; border-radius:18rpx; background:#f5f7fb; }.summary-value { color:#344d65; font-size:31rpx; font-weight:740; }.summary-value.warning { color:#d94c58; }.summary-label { margin-top:6rpx; color:#96a4b5; font-size:20rpx; }.check-row { display:flex; align-items:center; gap:18rpx; padding:20rpx 0; border-top:1rpx solid #edf1f5; }.check-dot { display:flex; align-items:center; justify-content:center; width:48rpx; height:48rpx; flex:0 0 auto; border-radius:16rpx; color:#fff; background:#52ae89; font-size:23rpx; font-weight:800; }.check-dot.issue { background:#e06972; }.check-title { color:#41586f; font-size:25rpx; font-weight:680; }.check-desc { margin-top:4rpx; color:#96a4b5; font-size:20rpx; }.reason-input { width:100%; height:160rpx; padding:20rpx; box-sizing:border-box; border:1rpx solid #e5eaf0; border-radius:18rpx; color:#425971; background:#f6f8fb; line-height:1.55; }.count { margin-top:9rpx; color:#9da9b8; font-size:19rpx; text-align:right; }
.button-row { display:grid; grid-template-columns:1fr 1.4fr; gap:16rpx; margin-top:25rpx; }.reject-button,.sign-button { height:88rpx; border:0; border-radius:19rpx; font-size:28rpx; font-weight:700; line-height:88rpx; }.reject-button { color:#d24a56; background:#ffeaec; }.result-strip { padding:22rpx; border-radius:19rpx; color:#5c7086; background:#eaf0f6; text-align:center; font-size:23rpx; }
.acceptance-page{background:#fbfcf9}.hero-label{color:#55ae06}.risk-card{color:#274019;border:1rpx solid #cde9a6;background:linear-gradient(135deg,#efffda,#d7ffa3);box-shadow:0 14rpx 30rpx rgba(77,159,13,.13)}.risk-card.safe{background:linear-gradient(135deg,#efffda,#d7ffa3)}.risk-card.danger{color:#6d3e00;background:linear-gradient(135deg,#fff8dd,#ffe9aa)}.score-ring{border-color:#82d71a;background:#fff}.decision-grid{display:grid;grid-template-columns:1fr 1fr;gap:17rpx;margin:22rpx 0}.decision-grid button{height:100rpx;border-radius:17rpx;font-size:25rpx;font-weight:750;line-height:1.25}.decision-grid button text{display:block;margin-top:7rpx;font-size:17rpx;font-weight:400}.accept{color:#fff;background:linear-gradient(135deg,#8bdf1d,#4eae00)}.review{color:#9a7100;background:linear-gradient(135deg,#fff7cf,#ffd85c)}.isolate{color:#fff;background:linear-gradient(135deg,#ffb34c,#f18a19)}.reject{border:2rpx solid #ef4549;color:#db3338;background:#fff}.evidence-actions>view:last-child{display:grid;grid-template-columns:repeat(3,1fr);gap:15rpx;margin-top:20rpx}.evidence-actions button{height:80rpx;border:1rpx solid #dfe6da;border-radius:15rpx;color:#51aa03;background:#fff;font-size:28rpx;line-height:1.1}.evidence-actions button text{display:block;margin-top:7rpx;color:#394333;font-size:18rpx}
</style>
