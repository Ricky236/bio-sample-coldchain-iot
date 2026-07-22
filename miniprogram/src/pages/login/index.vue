<script setup lang="ts">
import { onMounted } from 'vue'
import { useSessionStore, type DemoUser } from '@/stores/session'

const session = useSessionStore()
const identities: Array<DemoUser & { code: string; description: string; tone: string }> = [
  { name: '发出方', role: 'sender', code: '发', description: '核对并发出', tone: 'purple' },
  { name: '承运方', role: 'carrier', code: '运', description: '运输与设备', tone: 'blue' },
  { name: '接收方', role: 'receiver', code: '收', description: '到达与接收', tone: 'green' },
  { name: '管理员', role: 'admin', code: '管', description: '完整演示数据', tone: 'orange' },
]

onMounted(() => {
  session.restore()
  if (session.user) uni.reLaunch({ url: '/pages/tasks/index' })
})

function choose(user: DemoUser) {
  session.selectIdentity(user)
  uni.reLaunch({ url: '/pages/tasks/index' })
}
</script>

<template>
  <view class="page login-page">
    <view class="glow glow-left" />
    <view class="glow glow-right" />

    <view class="brand-row">
      <view class="brand">
        <view class="brand-icon"><text>冷</text><view class="spark spark-one" /><view class="spark spark-two" /></view>
        <view><view class="brand-name">BIO COLD CHAIN</view><view class="brand-cn">可信冷链</view></view>
      </view>
      <view class="mvp-badge"><view class="live-dot" />MVP 演示</view>
    </view>

    <view class="hero-copy">
      <view class="eyebrow">WELCOME BACK</view>
      <view class="hero-title">让样本安全抵达<text class="title-dot">.</text></view>
      <view class="hero-subtitle">选择身份，进入新版冷链责任追溯工作台</view>
    </view>

    <view class="visual-card">
      <view class="visual-grid" />
      <view class="route-path"><view class="path-progress" /><view class="moving-point" /></view>
      <view class="station station-start"><view class="station-icon">发</view><text>样本发出</text></view>
      <view class="station station-end"><view class="station-icon">收</view><text>安全送达</text></view>
      <view class="cold-package">
        <view class="package-handle" />
        <view class="package-lid" />
        <view class="package-body"><view class="cross">+</view><view class="temp">2–8℃</view></view>
      </view>
      <view class="visual-caption"><text>全程温控</text><text>可信追溯</text></view>
    </view>

    <view class="identity-panel">
      <view class="panel-heading">
        <view><view class="panel-title">选择演示身份</view><view class="panel-subtitle">进入对应任务工作台</view></view>
        <view class="step-number">01</view>
      </view>

      <view class="identity-grid">
        <view v-for="item in identities" :key="item.role" class="identity-card" @tap="choose(item)">
          <view class="identity-icon" :class="item.tone">{{ item.code }}</view>
          <view class="identity-name">{{ item.name }}</view>
          <view class="identity-description">{{ item.description }}</view>
          <view class="identity-arrow">↗</view>
        </view>
      </view>
    </view>

    <view class="trust-row">
      <view class="trust-item"><view class="trust-icon">温</view><text>实时温控</text></view>
      <view class="trust-divider" />
      <view class="trust-item"><view class="trust-icon">交</view><text>电子交接</text></view>
      <view class="trust-divider" />
      <view class="trust-item"><view class="trust-icon">溯</view><text>全程追溯</text></view>
    </view>

    <view class="security-note"><view class="security-dot" /><text>仅使用虚构身份与样本数据</text></view>
  </view>
</template>

<style scoped>
.login-page { position: relative; overflow: hidden; padding: 34rpx 30rpx 28rpx; background: linear-gradient(180deg, #eef3ff 0, #f7f8fc 48%, #f4f7fb 100%); }.glow { position: absolute; border-radius: 50%; filter: blur(4rpx); pointer-events: none; }.glow-left { left: -170rpx; top: 180rpx; width: 400rpx; height: 400rpx; background: rgba(183,176,255,.17); }.glow-right { right: -160rpx; top: -100rpx; width: 360rpx; height: 360rpx; background: rgba(142,184,255,.18); }
.brand-row { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; }.brand { display: flex; align-items: center; gap: 16rpx; }.brand-icon { position: relative; display: flex; align-items: center; justify-content: center; width: 68rpx; height: 68rpx; border-radius: 21rpx; color: #fff; background: linear-gradient(145deg, #786aff, #5146f6); box-shadow: 0 10rpx 24rpx rgba(91,77,247,.25); font-size: 27rpx; font-weight: 750; }.spark { position: absolute; width: 6rpx; height: 6rpx; border-radius: 50%; background: rgba(255,255,255,.8); }.spark-one { left: 13rpx; top: 13rpx; }.spark-two { right: 12rpx; bottom: 14rpx; }.brand-name { color: #6558ff; font-size: 18rpx; font-weight: 800; letter-spacing: 3rpx; }.brand-cn { margin-top: 4rpx; color: #53677e; font-size: 21rpx; font-weight: 650; }.mvp-badge { display: flex; align-items: center; gap: 9rpx; padding: 10rpx 16rpx; border: 1rpx solid rgba(255,255,255,.9); border-radius: 999rpx; color: #77879b; background: rgba(255,255,255,.62); font-size: 19rpx; }.live-dot { width: 10rpx; height: 10rpx; border-radius: 50%; background: #5ac394; box-shadow: 0 0 0 5rpx rgba(90,195,148,.13); }
.hero-copy { position: relative; z-index: 1; padding: 50rpx 4rpx 28rpx; }.eyebrow { margin-bottom: 12rpx; color: #8a7fff; font-size: 19rpx; font-weight: 750; letter-spacing: 5rpx; }.hero-title { color: #102a43; font-size: 48rpx; font-weight: 850; line-height: 1.28; }.title-dot { color: #6558ff; }.hero-subtitle { margin-top: 12rpx; color: #8797aa; font-size: 25rpx; }
.visual-card { position: relative; z-index: 1; height: 242rpx; overflow: hidden; border: 1rpx solid rgba(255,255,255,.8); border-radius: 30rpx; background: linear-gradient(135deg, #7567ff 0%, #5c64e9 52%, #508fd7 100%); box-shadow: 0 18rpx 38rpx rgba(77,79,183,.22); }.visual-grid { position: absolute; inset: 0; opacity: .14; background-image: linear-gradient(rgba(255,255,255,.5) 1rpx, transparent 1rpx), linear-gradient(90deg, rgba(255,255,255,.5) 1rpx, transparent 1rpx); background-size: 52rpx 52rpx; }.route-path { position: absolute; left: 84rpx; right: 84rpx; top: 84rpx; height: 4rpx; border-radius: 4rpx; background: rgba(255,255,255,.28); }.path-progress { width: 58%; height: 100%; border-radius: 4rpx; background: #fff; }.moving-point { position: absolute; left: 56%; top: -8rpx; width: 20rpx; height: 20rpx; border: 5rpx solid rgba(255,255,255,.35); border-radius: 50%; background: #fff; box-shadow: 0 0 0 7rpx rgba(255,255,255,.12); }.station { position: absolute; top: 52rpx; display: flex; flex-direction: column; align-items: center; gap: 8rpx; color: rgba(255,255,255,.76); font-size: 18rpx; }.station-start { left: 30rpx; }.station-end { right: 30rpx; }.station-icon { display: flex; align-items: center; justify-content: center; width: 48rpx; height: 48rpx; border: 3rpx solid rgba(255,255,255,.6); border-radius: 50%; color: #fff; background: rgba(255,255,255,.13); font-size: 18rpx; font-weight: 750; }.cold-package { position: absolute; left: 50%; bottom: 30rpx; width: 132rpx; height: 112rpx; transform: translateX(-50%); }.package-handle { position: absolute; left: 41rpx; top: 0; width: 50rpx; height: 28rpx; border: 9rpx solid rgba(255,255,255,.9); border-bottom: 0; border-radius: 17rpx 17rpx 0 0; box-sizing: border-box; }.package-lid { position: absolute; left: -8rpx; top: 23rpx; width: 148rpx; height: 25rpx; border-radius: 10rpx; background: #f4f5ff; box-shadow: 0 5rpx 12rpx rgba(43,45,111,.2); }.package-body { position: absolute; left: 0; top: 38rpx; display: flex; align-items: center; justify-content: center; width: 132rpx; height: 72rpx; border-radius: 8rpx 8rpx 18rpx 18rpx; background: rgba(255,255,255,.94); }.cross { color: #6558ff; font-size: 42rpx; font-weight: 400; }.temp { position: absolute; right: 9rpx; bottom: 7rpx; padding: 2rpx 6rpx; border-radius: 6rpx; color: #fff; background: #6558ff; font-size: 13rpx; }.visual-caption { position: absolute; left: 24rpx; right: 24rpx; bottom: 18rpx; display: flex; justify-content: space-between; color: rgba(255,255,255,.72); font-size: 17rpx; }
.identity-panel { position: relative; z-index: 2; margin-top: 22rpx; padding: 28rpx 24rpx 24rpx; border: 1rpx solid #e6ebf2; border-radius: 30rpx; background: rgba(255,255,255,.96); box-shadow: 0 16rpx 38rpx rgba(38,53,84,.09); }.panel-heading { display: flex; align-items: center; justify-content: space-between; padding: 0 4rpx 22rpx; }.panel-title { color: #173149; font-size: 31rpx; font-weight: 780; }.panel-subtitle { margin-top: 5rpx; color: #9aa8b9; font-size: 21rpx; }.step-number { color: #d0ccff; font-size: 44rpx; font-weight: 850; }.identity-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16rpx; }.identity-card { position: relative; min-height: 156rpx; padding: 22rpx; box-sizing: border-box; border: 1rpx solid #edf0f5; border-radius: 22rpx; background: #f8f9fc; }.identity-icon { display: flex; align-items: center; justify-content: center; width: 52rpx; height: 52rpx; border-radius: 16rpx; color: #6255f6; background: #eae7ff; font-size: 22rpx; font-weight: 750; }.identity-icon.blue { color: #397fbd; background: #e4f2ff; }.identity-icon.green { color: #29996b; background: #e4f6ee; }.identity-icon.orange { color: #bf7b27; background: #fff0dc; }.identity-name { margin-top: 14rpx; color: #324a62; font-size: 26rpx; font-weight: 720; }.identity-description { margin-top: 4rpx; color: #9da9b8; font-size: 19rpx; }.identity-arrow { position: absolute; right: 18rpx; top: 20rpx; color: #b5afef; font-size: 26rpx; }
.trust-row { position: relative; z-index: 1; display: flex; align-items: center; justify-content: center; gap: 18rpx; margin: 24rpx 8rpx 0; padding: 18rpx 14rpx; border-radius: 20rpx; background: rgba(255,255,255,.52); }.trust-item { display: flex; align-items: center; gap: 7rpx; color: #718298; font-size: 19rpx; }.trust-icon { display: flex; align-items: center; justify-content: center; width: 34rpx; height: 34rpx; border-radius: 11rpx; color: #6558ff; background: #eae7ff; font-size: 16rpx; font-weight: 750; }.trust-divider { width: 1rpx; height: 28rpx; background: #dfe4ec; }.security-note { position: relative; z-index: 1; display: flex; align-items: center; justify-content: center; gap: 10rpx; padding: 20rpx 0 6rpx; color: #a1adbd; font-size: 19rpx; }.security-dot { width: 9rpx; height: 9rpx; border: 3rpx solid #b3adff; border-radius: 50%; }
.login-page{background:radial-gradient(circle at 90% 2%,rgba(195,255,85,.26),transparent 360rpx),linear-gradient(180deg,#f7ffe9 0,#fbfcf9 48%,#f6faef 100%)}.glow-left{background:rgba(176,239,74,.15)}.glow-right{background:rgba(148,226,36,.18)}.brand-icon{background:linear-gradient(145deg,#91df22,#4cab00);box-shadow:0 10rpx 24rpx rgba(75,166,0,.25)}.brand-name,.eyebrow,.title-dot{color:#55ad06}.visual-card{background:linear-gradient(135deg,#91df20 0%,#5fbd06 52%,#379600 100%);box-shadow:0 18rpx 38rpx rgba(74,151,11,.22)}.cross{color:#54ae06}.temp{background:#54ae06}.step-number{color:#c7eca2}.identity-card{border-color:#e1ead9;background:#fbfdf9}.identity-icon,.trust-icon{color:#50aa03;background:#eff9e7}.identity-icon.blue,.identity-icon.green,.identity-icon.orange{color:#50aa03;background:#eff9e7}.identity-arrow{color:#7cc23d}.security-dot{border-color:#70bd2d}
</style>
