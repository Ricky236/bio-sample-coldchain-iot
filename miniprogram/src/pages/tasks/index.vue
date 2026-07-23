<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import StatePanel from '@/components/StatePanel.vue'
import { useSessionStore } from '@/stores/session'
import { taskService } from '@/services/tasks'
import { errorMessage } from '@/services/request'
import type { Task } from '@/types/api'

const session = useSessionStore()
const tasks = ref<Task[]>([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const activeFilter = ref<'all' | 'pending_handoff' | 'in_transit' | 'signed'>('all')

const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return tasks.value.filter((task) => {
    const statusOK = activeFilter.value === 'all' || task.status === activeFilter.value
    const queryOK = !query || [task.task_id, task.sample_name, task.device_id].some((v) => v.toLowerCase().includes(query))
    return statusOK && queryOK
  })
})
const currentTask = computed(() => tasks.value.find((task) => task.status === 'in_transit') || tasks.value[0] || null)
const roleName = computed(() => session.user?.role === 'sender' ? '发货方' : session.user?.role === 'carrier' ? '承运方' : session.user?.role === 'receiver' ? '接收方' : '管理员')

function count(status: typeof activeFilter.value) {
  return status === 'all' ? tasks.value.length : tasks.value.filter((task) => task.status === status).length
}
async function load() {
  loading.value = true; error.value = ''
  try { tasks.value = await taskService.listTasks() }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false; uni.stopPullDownRefresh() }
}
function openTask(task = currentTask.value) {
  if (!task) return uni.showToast({ title: '暂无运单', icon: 'none' })
  uni.navigateTo({ url: `/pages/task-detail/index?task_id=${encodeURIComponent(task.task_id)}` })
}
function openPage(page: 'monitor' | 'alarms' | 'handoff' | 'acceptance' | 'trace') {
  if (!currentTask.value) return uni.showToast({ title: '暂无可操作运单', icon: 'none' })
  uni.navigateTo({ url: `/pages/${page}/index?task_id=${encodeURIComponent(currentTask.value.task_id)}` })
}
function createWaybill() { uni.navigateTo({ url: '/pages/create/index' }) }
function openProfile() { uni.navigateTo({ url: '/pages/profile/index' }) }
function tokenFromPayload(payload: string) {
  const match = payload.match(/[?&]token=([^&]+)/)
  return match ? decodeURIComponent(match[1]) : payload.trim()
}
function scanHandoff() {
  uni.scanCode({
    scanType: ['qrCode'],
    success: ({ result }) => {
      const token = tokenFromPayload(result)
      if (!token) return uni.showToast({ title: '未识别到交接二维码', icon: 'none' })
      uni.navigateTo({ url: `/pages/handoff/index?token=${encodeURIComponent(token)}` })
    },
    fail: () => uni.showToast({ title: '未识别到交接二维码', icon: 'none' }),
  })
}

onLoad(() => { if (session.requireSession()) load() })
onShow(() => { if (session.isAuthenticated && !loading.value) load() })
onPullDownRefresh(load)
</script>

<template>
  <view class="home-page">
    <view class="topbar">
      <view class="identity">
        <view class="avatar">{{ session.user?.name?.slice(0, 1) || '冷' }}</view>
        <view><view class="name-line"><text class="name">{{ session.user?.name }}</text><text class="role">· {{ roleName }}</text></view><view class="greeting">早上好！</view></view>
      </view>
      <view class="bell" @tap="openPage('alarms')">♢<view class="dot" /></view>
    </view>

    <view class="search"><view class="loc">⌾</view><input v-model="keyword" placeholder="搜索运单号 / 样本 / 设备" /><view class="magnifier" /></view>

    <view class="section-head"><text>当前运单</text><text class="more" @tap="activeFilter = 'all'">查看全部 ›</text></view>
    <StatePanel v-if="loading" state="loading" />
    <StatePanel v-else-if="error" state="error" :message="error" @retry="load" />
    <view v-else-if="currentTask" class="waybill-card" @tap="openTask()">
      <view class="chips"><text>运单号</text><view><text>2~8℃</text><text>{{ currentTask.device_id }}</text></view></view>
      <view class="waybill-no">{{ currentTask.task_id === 'TASK-001' ? 'WD-20260722-001' : currentTask.task_id }}</view>
      <view class="sample"><text class="sample-label">样本</text>{{ currentTask.sample_name }}</view>
      <view class="transport">★　{{ currentTask.status === 'in_transit' ? '运输中' : '待交接' }} · 4.2℃</view>
      <view class="coldbox"><view class="lid" /><view class="box">❄</view><view class="meter">4.2℃</view></view>
      <view class="steps"><view class="done">✓<text>建档</text></view><view class="done">✓<text>预检</text></view><view class="done">✓<text>交接</text></view><view class="active">▣<text>在途</text></view><view>□<text>验收</text></view></view>
    </view>
    <view v-else class="empty-home">
      <StatePanel state="empty" :message="session.user?.role === 'sender' ? '还没有运单，创建第一张冷链运单吧' : '暂时没有分配给您的运单'" />
      <button v-if="session.user?.role === 'sender'" class="empty-create" @tap="createWaybill">＋ 新建运单</button>
    </view>

    <view class="pager"><i /><i /><i /></view>
    <view class="section-head quick-title"><text>快捷操作</text></view>
    <view class="quick-actions">
      <view @tap="createWaybill"><b>▤</b><text>新建运单</text></view>
      <view @tap="openTask()"><b>□</b><text>装箱预检</text></view>
      <view @tap="openPage('handoff')"><b>↔</b><text>动态交接</text></view>
      <view @tap="openPage('acceptance')"><b>✓</b><text>到达验收</text></view>
    </view>

    <view class="section-head list-title"><text>进行中的任务</text><text class="more">查看全部 ›</text></view>
    <view class="filters"><text :class="{ active: activeFilter === 'all' }" @tap="activeFilter = 'all'">全部</text><text :class="{ active: activeFilter === 'pending_handoff' }" @tap="activeFilter = 'pending_handoff'">待装箱</text><text :class="{ active: activeFilter === 'in_transit' }" @tap="activeFilter = 'in_transit'">运输中</text><text :class="{ active: activeFilter === 'signed' }" @tap="activeFilter = 'signed'">待验收</text></view>
    <view class="task-grid">
      <view v-for="task in filtered" :key="task.task_id" class="mini-task" @tap="openTask(task)"><view class="mini-icon">{{ task.status === 'signed' ? '✓' : '▣' }}</view><view class="mini-copy"><b>{{ task.task_id === 'TASK-001' ? 'WD-20260722-001' : task.task_id }}</b><text>{{ task.sample_name }}</text><small>♨ 4.2℃ · {{ task.status === 'in_transit' ? '运输中' : '待装箱' }}</small><em>♢ 风险低</em></view><view class="arrow">›</view></view>
    </view>

    <view class="safe-space" />
    <view class="bottom-nav">
      <view class="nav active"><b>▣</b><text>任务</text></view>
      <view class="nav" @tap="openPage('monitor')"><b>▥</b><text>监控</text></view>
      <view class="scan" @tap="scanHandoff">⌗</view>
      <view class="nav" @tap="openPage('alarms')"><b>♧</b><text>告警</text></view>
      <view class="nav" @tap="openProfile"><b>♙</b><text>我的</text></view>
    </view>
  </view>
</template>

<style scoped>
.home-page{min-height:100vh;padding:36rpx 34rpx 0;box-sizing:border-box;color:#152312;background:radial-gradient(circle at 12% 3%,rgba(185,242,80,.18),transparent 300rpx),#fbfcf9}.topbar,.identity,.name-line,.search,.section-head,.chips,.chips>view,.steps,.quick-actions,.filters,.mini-task,.bottom-nav,.nav{display:flex;align-items:center}.topbar,.section-head,.chips{justify-content:space-between}.identity{gap:20rpx}.avatar{display:flex;align-items:center;justify-content:center;width:82rpx;height:82rpx;border:5rpx solid #d9efb7;border-radius:50%;color:#fff;background:linear-gradient(145deg,#78985d,#345423);font-size:32rpx;font-weight:800}.name-line{gap:8rpx}.name{font-size:31rpx;font-weight:800}.role{color:#52604d;font-size:24rpx}.greeting{margin-top:5rpx;color:#55a806;font-size:27rpx}.bell{position:relative;color:#4ca500;font-size:52rpx}.dot{position:absolute;right:-1rpx;top:1rpx;width:13rpx;height:13rpx;border:3rpx solid #fff;border-radius:50%;background:#61bd00}.search{height:88rpx;margin:28rpx 0 30rpx;padding:0 24rpx;border:1rpx solid #dfe5da;border-radius:44rpx;background:#fff;box-shadow:0 10rpx 28rpx rgba(48,94,12,.05)}.search input{flex:1;font-size:25rpx}.loc{display:flex;align-items:center;justify-content:center;width:56rpx;height:56rpx;margin-right:14rpx;border-radius:50%;color:#fff;background:linear-gradient(135deg,#91e51d,#3ba900);font-size:32rpx}.magnifier{position:relative;width:28rpx;height:28rpx;border:4rpx solid #586057;border-radius:50%}.magnifier:after{position:absolute;right:-11rpx;bottom:-8rpx;width:14rpx;height:4rpx;background:#586057;content:'';transform:rotate(45deg)}.section-head{font-size:31rpx;font-weight:800}.more{color:#7a8177;font-size:23rpx;font-weight:400}.waybill-card{position:relative;overflow:hidden;margin-top:16rpx;padding:26rpx 26rpx 20rpx;border-radius:27rpx;color:#fff;background:radial-gradient(circle at 80% 30%,rgba(205,255,118,.5),transparent 220rpx),linear-gradient(135deg,#75d000,#43a600);box-shadow:0 18rpx 34rpx rgba(79,171,0,.22)}.chips text{padding:6rpx 14rpx;border:1rpx solid rgba(255,255,255,.45);border-radius:999rpx;font-size:19rpx}.chips>view{gap:10rpx}.waybill-no{margin-top:16rpx;font-size:34rpx;font-weight:850;letter-spacing:1rpx}.sample{margin-top:14rpx;font-size:25rpx;font-weight:650}.sample-label{margin-right:12rpx;padding:4rpx 12rpx;border-radius:999rpx;background:rgba(255,255,255,.18);font-size:18rpx}.transport{margin:15rpx 0 25rpx;font-size:23rpx}.coldbox{position:absolute;right:50rpx;top:100rpx}.box{display:flex;align-items:center;justify-content:center;width:120rpx;height:75rpx;border-radius:7rpx 7rpx 16rpx 16rpx;color:#fff;background:rgba(46,125,0,.65);font-size:38rpx}.lid{position:absolute;left:-7rpx;top:-10rpx;width:134rpx;height:23rpx;border-radius:8rpx;background:#e8f6d2}.meter{position:absolute;right:-38rpx;bottom:-12rpx;padding:9rpx 8rpx;border:5rpx solid #e9f6df;border-radius:8rpx;color:#263421;background:#fff;font-size:15rpx}.steps{justify-content:space-between;padding-top:20rpx;border-top:1rpx dashed rgba(255,255,255,.45)}.steps>view{display:flex;flex-direction:column;align-items:center;justify-content:center;width:70rpx;height:70rpx;border-radius:50%;color:rgba(255,255,255,.8);background:rgba(255,255,255,.16);font-size:23rpx}.steps text{margin-top:3rpx;font-size:16rpx}.steps .active{color:#43a700;background:#fff}.pager{display:flex;justify-content:center;gap:12rpx;padding:13rpx}.pager i{width:9rpx;height:9rpx;border-radius:50%;background:#dfe9d4}.pager i:first-child{width:28rpx;border-radius:9rpx;background:#65bb0c}.quick-title{margin-top:4rpx}.quick-actions{justify-content:space-around;margin-top:14rpx;padding:21rpx 8rpx;border:1rpx solid #e7ece1;border-radius:24rpx;background:#fff}.quick-actions>view{display:flex;flex-direction:column;align-items:center;gap:8rpx;color:#36412f;font-size:20rpx}.quick-actions b{display:flex;align-items:center;justify-content:center;width:58rpx;height:58rpx;border-radius:50%;color:#52ac00;background:#f0f9e7;font-size:31rpx}.list-title{margin-top:26rpx}.filters{gap:3rpx;margin:12rpx 0}.filters text{padding:10rpx 23rpx;border:1rpx solid #e1e6dc;border-radius:999rpx;color:#687164;font-size:19rpx}.filters .active{color:#fff;border-color:#55b500;background:linear-gradient(90deg,#79d900,#4caf00)}.task-grid{display:grid;grid-template-columns:1fr 1fr;gap:12rpx}.mini-task{min-width:0;padding:16rpx;border:1rpx solid #e6eadf;border-radius:18rpx;background:#fff}.mini-icon{display:flex;align-items:center;justify-content:center;width:55rpx;height:55rpx;flex:0 0 auto;border-radius:50%;color:#52ad00;background:#edf8e4;font-size:26rpx}.mini-copy{min-width:0;margin-left:12rpx}.mini-copy b,.mini-copy text,.mini-copy small,.mini-copy em{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mini-copy b{font-size:19rpx}.mini-copy text,.mini-copy small{margin-top:3rpx;color:#60695d;font-size:17rpx}.mini-copy em{margin-top:4rpx;color:#56ac08;font-size:16rpx;font-style:normal}.arrow{margin-left:auto;color:#55b20a;font-size:34rpx}.safe-space{height:155rpx}.bottom-nav{position:fixed;z-index:20;left:18rpx;right:18rpx;bottom:calc(12rpx + env(safe-area-inset-bottom));justify-content:space-around;height:104rpx;border:1rpx solid #e4e9df;border-radius:35rpx;background:rgba(255,255,255,.97);box-shadow:0 15rpx 40rpx rgba(39,70,15,.15)}.nav{flex-direction:column;gap:4rpx;color:#70776d;font-size:19rpx}.nav b{font-size:29rpx}.nav.active{color:#52b000}.scan{display:flex;align-items:center;justify-content:center;width:92rpx;height:92rpx;margin-top:-42rpx;border:9rpx solid #dcf8ac;border-radius:50%;color:#fff;background:linear-gradient(145deg,#c5ff55,#67cc00);box-shadow:0 9rpx 25rpx rgba(92,194,0,.38);font-size:49rpx}
.empty-home{position:relative;padding-bottom:20rpx}.empty-create{width:300rpx;height:72rpx;margin:-70rpx auto 35rpx;border:0;border-radius:999rpx;color:#fff;background:linear-gradient(90deg,#7bd50d,#48ad00);font-size:24rpx;line-height:72rpx}
</style>
