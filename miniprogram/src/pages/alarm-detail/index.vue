<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { AlarmEvent, Task } from '@/types/api'
import { formatTime } from '@/utils/status'

const taskId = ref('')
const alarmId = ref(0)
const alarm = ref<AlarmEvent | null>(null)
const task = ref<Task | null>(null)
const loading = ref(true)
const error = ref('')
const state = ref<'pending'|'processing'|'review'|'closed'>('pending')
const note = ref('')
const review = ref('')
const photos = ref<string[]>([])
const value = computed(() => alarm.value?.event_type === 'TEMP_ALERT' ? '9.8℃' : alarm.value?.event_type === 'BOX_OPEN' ? '箱盖开启' : '冲击 2.6g')

async function load() {
  try {
    const [taskData, list] = await Promise.all([taskService.getTask(taskId.value), taskService.getAlarms(taskId.value,100)])
    task.value = taskData; alarm.value = list.items.find((x) => x.id === alarmId.value) || list.items[0] || null
  } catch(e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
function choosePhotos() {
  uni.chooseImage({ count: 2, success: ({ tempFilePaths }) => { photos.value = [...photos.value,...tempFilePaths].slice(0,2) } })
}
function move(next:'processing'|'review'|'closed') {
  if (next !== 'processing' && !note.value.trim()) return uni.showToast({ title:'请填写处置说明',icon:'none' })
  state.value = next
  uni.showToast({ title: next === 'closed' ? '告警已闭环' : '状态已更新', icon:'success' })
}
function dispute() { uni.showToast({ title:'争议已记录',icon:'none' }) }
function advance() {
  if (state.value === 'pending') return move('processing')
  if (state.value === 'processing') return move('review')
  return move('closed')
}
onLoad((query) => { taskId.value=String(query?.task_id||''); alarmId.value=Number(query?.alarm_id||0); if(taskId.value) load(); else {loading.value=false;error.value='缺少 task_id'} })
</script>

<template>
  <view class="dispose-page">
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error || !alarm" state="error" :message="error || '告警不存在'" @retry="load" />
    <template v-else>
      <view class="alert-card"><view class="alert-head"><b>▲　{{ alarm.event_type }} {{ alarm.event_name }}</b><text>高优先级</text></view><view class="value">{{ value }}</view><view class="meta">发生 {{ formatTime(alarm.timestamp) }}　|　持续 12min</view><view class="meta">⌖ 位置　运输路线当前位置</view><view class="meta">责任段　承运 · {{ task?.carrier }}</view></view>
      <view class="flow"><view :class="{active:state==='pending'}">待确认</view><b>›</b><view :class="{active:state==='processing'}">处理中</view><b>›</b><view :class="{active:state==='review'}">待复核</view><b>›</b><view :class="{active:state==='closed'}">已关闭</view></view>
      <view class="form-card"><view class="title">处置说明</view><textarea v-model="note" maxlength="200" placeholder="例如：已补冰袋并复测至4.5℃" /><view class="count">{{note.length}}/200</view><view class="title">上传现场照片</view><view class="photos"><image v-for="src in photos" :key="src" :src="src" mode="aspectFill" /><view v-if="photos.length<2" class="upload" @tap="choosePhotos">▧<text>上传照片</text></view></view><view class="title">复核结论</view><textarea v-model="review" maxlength="200" placeholder="请输入复核结论（选填）" /><view class="actions"><button @tap="move('processing')">确认告警</button><button class="primary" @tap="advance">{{state==='processing'?'提交处置':state==='review'?'复核关闭':'开始处置'}}</button><button @tap="dispute">升级争议</button></view></view>
      <view class="audit">♢　不可删除 · 操作者与时间留痕<br/><text>操作者 {{ task?.carrier }}　|　操作时间 {{ new Date().toLocaleString() }}</text></view>
    </template>
  </view>
</template>

<style scoped>
.dispose-page{min-height:100vh;padding:26rpx 30rpx 45rpx;box-sizing:border-box;background:linear-gradient(90deg,#f7ffe9,#fff 22%,#fff 78%,#f7ffe9)}.alert-card,.flow,.form-card,.audit{border:1rpx solid #e2eadb;border-radius:24rpx;background:#fff;box-shadow:0 9rpx 25rpx rgba(56,91,29,.07)}.alert-card{padding:29rpx;color:#d93b3f;background:linear-gradient(135deg,#fff4f3,#fff)}.alert-head{display:flex;justify-content:space-between}.alert-head b{font-size:27rpx}.alert-head text{padding:7rpx 15rpx;border-radius:999rpx;background:#ffe5e3;font-size:19rpx}.value{margin:20rpx 0;font-size:58rpx;font-weight:800}.meta{margin-top:14rpx;color:#5f6960;font-size:22rpx}.flow{display:flex;align-items:center;justify-content:space-around;margin:20rpx 0;padding:18rpx}.flow view{padding:11rpx 21rpx;border:1rpx solid #cfd5ca;border-radius:999rpx;color:#626a5e;font-size:20rpx}.flow view.active{color:#fff;border-color:#5cba08;background:#5cba08}.flow b{color:#a3aaa0}.form-card{padding:26rpx}.title{margin:16rpx 0 12rpx;padding-left:13rpx;border-left:6rpx solid #5cb808;font-size:25rpx;font-weight:700}.form-card textarea{width:100%;height:135rpx;padding:17rpx;box-sizing:border-box;border:1rpx solid #ccd3c8;border-radius:10rpx;font-size:22rpx}.count{text-align:right;color:#949b90;font-size:18rpx}.photos{display:flex;gap:18rpx}.photos image,.upload{width:120rpx;height:120rpx;border-radius:10rpx}.upload{display:flex;flex-direction:column;align-items:center;justify-content:center;border:2rpx dashed #cbd2c7;color:#a0a79d;font-size:30rpx}.upload text{margin-top:5rpx;font-size:17rpx}.actions{display:grid;grid-template-columns:1fr 1.4fr 1fr;gap:20rpx;margin-top:24rpx}.actions button{height:68rpx;border:2rpx solid #58b707;border-radius:12rpx;color:#4eaa00;background:#fff;font-size:21rpx;line-height:64rpx}.actions .primary{color:#fff;background:linear-gradient(90deg,#79d40d,#4cad00)}.audit{margin-top:20rpx;padding:21rpx;color:#53624e;background:#f5faef;font-size:20rpx;line-height:1.8}.audit text{color:#798275;font-size:18rpx}
</style>
