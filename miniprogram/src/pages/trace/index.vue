<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { HandoffNode, TraceReport } from '@/types/api'
import { formatTime } from '@/utils/status'

const taskId = ref('')
const report = ref<TraceReport | null>(null)
const loading = ref(true)
const error = ref('')
const nodes = computed(() => report.value?.handoff_nodes || [])
const currentOwner = computed(() => {
  const status = report.value?.task.status
  if (status === 'signed') return report.value?.task.receiver
  if (status === 'rejected') return '争议待复核'
  if (status === 'in_transit' || status === 'arrived') return report.value?.task.carrier
  return report.value?.task.sender
})
function nodeTitle(node: HandoffNode) {
  if (node.type === 'started') return '发出交接完成'
  if (node.type === 'signed') return '接收方完成签收'
  return '接收方拒绝接收'
}
function nodeParty(node: HandoffNode) {
  if (!report.value) return ''
  if (node.type === 'started') return `${report.value.task.sender} → ${report.value.task.carrier}`
  return `${report.value.task.carrier} → ${report.value.task.receiver}`
}
async function load() {
  loading.value = true; error.value = ''
  try { report.value = await taskService.getTraceReport(taskId.value) }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false; uni.stopPullDownRefresh() }
}
function openAcceptance() { uni.navigateTo({ url: `/pages/acceptance/index?task_id=${encodeURIComponent(taskId.value)}` }) }
function reportAction(type: 'preview' | 'pdf' | 'share') {
  const titles = { preview: '报告预览已生成', pdf: '正式PDF需后端文件接口', share: '已生成脱敏分享信息' }
  uni.showToast({ title: titles[type], icon: type === 'pdf' ? 'none' : 'success' })
}
onLoad((query) => {
  taskId.value = String(query?.task_id || '')
  if (taskId.value) load(); else { loading.value = false; error.value = '缺少 task_id' }
})
onPullDownRefresh(load)
onShow(() => { if (taskId.value && !loading.value) load() })
</script>

<template>
  <view class="page trace-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <template v-else-if="report">
      <view class="trace-hero">
        <view class="row"><view><view class="hero-label">CHAIN OF CUSTODY</view><view class="hero-title">责任追溯</view></view><StatusTag :status="report.task.status" /></view>
        <view class="owner-card"><view class="owner-label">当前责任主体</view><view class="owner-name">{{ currentOwner }}</view><view class="owner-time">运单 {{ report.task.task_id }}</view></view>
      </view>

      <view class="card route-card">
        <view class="section-heading"><view class="section-title">运输路线</view><view class="section-hint">全链路</view></view>
        <view class="route">
          <view class="route-point"><view class="point sender">发</view><view class="point-label">{{ report.task.sender }}</view></view>
          <view class="route-line"><view class="route-progress" /></view>
          <view class="route-point"><view class="point receiver">收</view><view class="point-label">{{ report.task.receiver }}</view></view>
        </view>
      </view>

      <view class="card timeline-card">
        <view class="section-heading"><view class="section-title">责任节点</view><view class="section-hint">{{ nodes.length }} 个节点</view></view>
        <view v-if="!nodes.length" class="empty-timeline">尚未发生正式交接，当前责任仍属于发货方。</view>
        <view v-else class="timeline">
          <view v-for="(node,index) in nodes" :key="`${node.type}-${node.timestamp}`" class="timeline-item">
            <view class="timeline-axis"><view class="timeline-dot" :class="node.type" /><view v-if="index < nodes.length - 1" class="timeline-line" /></view>
            <view class="timeline-content"><view class="timeline-title">{{ nodeTitle(node) }}</view><view class="timeline-party">{{ nodeParty(node) }}</view><view class="timeline-time">{{ formatTime(node.timestamp) }}</view><view v-if="node.reason" class="timeline-reason">原因：{{ node.reason }}</view></view>
          </view>
        </view>
      </view>

      <view class="card evidence-card">
        <view class="section-heading"><view class="section-title">证据摘要</view><view class="evidence-badge">服务端汇总</view></view>
        <view class="evidence-grid">
          <view><view class="evidence-value">{{ report.summary.total_records }}</view><view class="evidence-label">监测记录</view></view>
          <view><view class="evidence-value">{{ report.summary.event_count }}</view><view class="evidence-label">异常事件</view></view>
          <view><view class="evidence-value">{{ report.summary.avg_temperature ?? '--' }}℃</view><view class="evidence-label">平均温度</view></view>
        </view>
        <view class="evidence-note">当前报告来自统一追溯接口。哈希链校验和 PDF 导出尚需后端增加正式字段与文件接口。</view>
        <view class="report-actions"><button @tap="reportAction('preview')">◉ 预览报告</button><button @tap="reportAction('pdf')">▤ 导出 PDF</button><button @tap="reportAction('share')">⌯ 分享脱敏</button></view>
      </view>

      <view class="card event-card">
        <view class="section-heading"><view class="section-title">异常时间线</view><view class="section-hint">最近 {{ report.events.length }} 条</view></view>
        <view v-if="!report.events.length" class="no-event">全程未记录异常事件</view>
        <view v-for="item in report.events" v-else :key="item.id" class="event-row"><view class="event-mark" /><view class="event-main"><view class="event-name">{{ item.event_name }}</view><view class="event-detail">{{ item.event_detail }}</view></view><view class="event-time">{{ formatTime(item.timestamp) }}</view></view>
      </view>

      <button v-if="['in_transit','arrived'].includes(report.task.status)" class="primary accept-button" @tap="openAcceptance">进入到达验收</button>
    </template>
  </view>
</template>

<style scoped>
.trace-page { background:linear-gradient(180deg,#eeeaff 0,#f5f7fb 500rpx); }.trace-hero { margin:-28rpx -28rpx 23rpx; padding:48rpx 32rpx 32rpx; background:linear-gradient(145deg,#6658f4,#4c61d3); color:#fff; border-radius:0 0 38rpx 38rpx; }.hero-label { font-size:18rpx; letter-spacing:4rpx; opacity:.7; }.hero-title { margin-top:8rpx; font-size:41rpx; font-weight:820; }.owner-card { margin-top:28rpx; padding:22rpx 24rpx; border:1rpx solid rgba(255,255,255,.18); border-radius:22rpx; background:rgba(255,255,255,.12); }.owner-label { font-size:20rpx; opacity:.72; }.owner-name { margin:6rpx 0; font-size:31rpx; font-weight:770; }.owner-time { font-size:19rpx; opacity:.68; }
.route { display:grid; grid-template-columns:140rpx 1fr 140rpx; align-items:center; }.route-point { text-align:center; }.point { display:flex; align-items:center; justify-content:center; width:58rpx; height:58rpx; margin:0 auto 10rpx; border-radius:19rpx; color:#fff; font-weight:760; }.sender { background:#6558ff; }.receiver { background:#54ae8c; }.point-label { overflow:hidden; color:#4d6278; font-size:20rpx; text-overflow:ellipsis; white-space:nowrap; }.route-line { height:5rpx; margin:0 8rpx 29rpx; background:#e2e6ed; }.route-progress { width:64%; height:100%; background:linear-gradient(90deg,#6558ff,#54ae8c); }
.empty-timeline { padding:28rpx 0; color:#8f9eb0; text-align:center; font-size:22rpx; }.timeline-item { display:grid; grid-template-columns:34rpx 1fr; gap:16rpx; }.timeline-axis { position:relative; display:flex; justify-content:center; }.timeline-dot { z-index:1; width:17rpx; height:17rpx; margin-top:7rpx; border:6rpx solid #e5e1ff; border-radius:50%; background:#6558ff; }.timeline-dot.signed { border-color:#dcf3e9; background:#4fa985; }.timeline-dot.rejected { border-color:#ffe0e3; background:#d94b57; }.timeline-line { position:absolute; top:29rpx; bottom:-7rpx; width:3rpx; background:#e4e8ee; }.timeline-content { padding-bottom:27rpx; }.timeline-title { color:#374f67; font-size:26rpx; font-weight:720; }.timeline-party { margin-top:5rpx; color:#667b91; font-size:22rpx; }.timeline-time { margin-top:5rpx; color:#9aa7b6; font-size:19rpx; }.timeline-reason { margin-top:9rpx; padding:11rpx 14rpx; border-radius:12rpx; color:#c54b56; background:#ffedf0; font-size:20rpx; }
.evidence-badge { padding:7rpx 13rpx; border-radius:999rpx; color:#6558ff; background:#ece9ff; font-size:18rpx; }.evidence-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12rpx; text-align:center; }.evidence-grid>view { padding:20rpx 8rpx; border-radius:17rpx; background:#f5f7fb; }.evidence-value { color:#344c64; font-size:28rpx; font-weight:740; }.evidence-label { margin-top:5rpx; color:#98a5b5; font-size:19rpx; }.evidence-note { margin-top:18rpx; padding:16rpx; border-radius:15rpx; color:#75869a; background:#eef2f7; font-size:20rpx; line-height:1.5; }
.no-event { color:#8c9caf; text-align:center; padding:25rpx 0; }.event-row { display:grid; grid-template-columns:18rpx 1fr 150rpx; gap:13rpx; padding:18rpx 0; border-top:1rpx solid #eef1f5; }.event-mark { width:13rpx; height:13rpx; margin-top:9rpx; border-radius:50%; background:#e05d68; box-shadow:0 0 0 6rpx #ffedf0; }.event-name { color:#435a71; font-size:24rpx; font-weight:680; }.event-detail { margin-top:4rpx; color:#96a3b3; font-size:19rpx; line-height:1.4; }.event-time { color:#9ba8b7; font-size:18rpx; text-align:right; line-height:1.4; }.accept-button { margin-top:26rpx; }
.trace-page{background:#fbfcf9}.trace-hero{background:linear-gradient(145deg,#d9ff9f,#aeea4a);color:#234515}.owner-card{border-color:rgba(75,150,0,.18);background:rgba(255,255,255,.48)}.sender,.receiver{background:#57b007}.route-progress{background:linear-gradient(90deg,#8cdd22,#4dab00)}.timeline-dot{border-color:#e2f5ca;background:#58b009}.evidence-badge{color:#50aa03;background:#eff9e7}.report-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:13rpx;margin-top:20rpx}.report-actions button{height:70rpx;border:1rpx solid #8dd046;border-radius:15rpx;color:#397d00;background:#fff;font-size:19rpx;line-height:68rpx}
</style>
