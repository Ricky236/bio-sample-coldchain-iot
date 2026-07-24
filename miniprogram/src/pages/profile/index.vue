<script setup lang="ts">
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
const session = useSessionStore()
const role = computed(() => session.user?.role === 'sender' ? '发货方' : session.user?.role === 'carrier' ? '承运方' : session.user?.role === 'receiver' ? '接收方' : '管理员')
function go(path: string) { uni.navigateTo({ url: path }) }
function back() { uni.navigateBack() }
function notify(title: string) { uni.showToast({ title, icon: 'none' }) }
function showCodeHelp() { uni.showModal({ title: '动态码说明', content: '二维码60秒刷新，只绑定当前运单与本次交接动作。', showCancel: false }) }
</script>

<template>
  <view class="profile-page">
    <view class="profile-head"><view class="avatar">{{ session.user?.name?.slice(0,1) || '用' }}<view>▣</view></view><view class="identity"><view><b>{{ session.user?.name }}</b><text>{{ role }}</text></view><p>{{ session.user?.organization }}</p><small>{{ session.user?.phone }}</small></view></view>
    <view class="auth-card"><view><b>◎</b><view><strong>账户身份</strong><text>已登录</text></view></view><i/><view><b>✓</b><view><strong>角色权限</strong><p>权限由注册资料确定</p></view></view></view>
    <view class="group"><label>业务</label><view @tap="back"><b>◆</b><text>我的运单</text><i>›</i></view><view @tap="go('/pages/tasks/index')"><b>▤</b><text>交接记录</text><i>›</i></view><view @tap="go('/pages/monitor-pick/index')"><b>▥</b><text>运输监控</text><i>›</i></view></view>
    <view class="group"><label>安全</label><view @tap="notify('账户状态正常')"><b>♢</b><text>账户与核验状态</text><i>›</i></view><view @tap="showCodeHelp"><b>▣</b><text>动态码说明</text><i>›</i></view></view>
    <view class="group"><label>系统</label><view @tap="notify('当前已是最新版本')"><b>ⓘ</b><text>关于</text><i>›</i></view><view class="logout" @tap="session.logout()"><b>⇥</b><text>退出登录</text><i>›</i></view></view>
  </view>
</template>

<style scoped>
.profile-page{min-height:100vh;padding:44rpx 38rpx 60rpx;box-sizing:border-box;background:radial-gradient(circle at 78% 0,rgba(193,255,78,.24),transparent 330rpx),#fbfcf9;color:#283123}.profile-head{display:flex;align-items:center;margin-bottom:32rpx}.avatar{position:relative;display:flex;align-items:center;justify-content:center;width:124rpx;height:124rpx;border:6rpx solid #b8ea75;border-radius:50%;color:#fff;background:linear-gradient(145deg,#9ab27e,#46682e);font-size:48rpx}.avatar>view{position:absolute;right:-2rpx;bottom:3rpx;display:flex;align-items:center;justify-content:center;width:36rpx;height:36rpx;border:4rpx solid #fff;border-radius:50%;background:#63bf08;font-size:15rpx}.identity{margin-left:24rpx}.identity b{font-size:38rpx}.identity text{margin-left:18rpx;padding:7rpx 18rpx;border-radius:999rpx;color:#fff;background:#58b500;font-size:19rpx}.identity p{margin:10rpx 0 0;color:#626b5e;font-size:23rpx}.identity small{display:block;margin-top:5rpx;color:#929c8e;font-size:18rpx}.auth-card{display:flex;align-items:center;justify-content:space-around;margin-bottom:24rpx;padding:26rpx;border-radius:25rpx;background:linear-gradient(105deg,#c7ff70,#edffc9);box-shadow:0 10rpx 27rpx rgba(80,151,21,.14)}.auth-card>view{display:flex;align-items:center;gap:15rpx}.auth-card b{display:flex;align-items:center;justify-content:center;width:65rpx;height:65rpx;border-radius:50%;color:#fff;background:#5cb806;font-size:28rpx}.auth-card strong{font-size:24rpx}.auth-card text{margin-left:8rpx;padding:3rpx 9rpx;border-radius:999rpx;color:#4d9e07;background:rgba(255,255,255,.65);font-size:16rpx}.auth-card p{margin:5rpx 0 0;font-size:17rpx}.auth-card i{height:72rpx;border-left:1rpx solid #a7d76c}.group{margin-bottom:20rpx;padding:18rpx 28rpx;border:1rpx solid #e3e9de;border-radius:24rpx;background:#fff;box-shadow:0 8rpx 25rpx rgba(49,79,26,.07)}.group label{display:block;margin-bottom:5rpx;color:#898f85;font-size:21rpx}.group>view{display:grid;grid-template-columns:50rpx 1fr 20rpx;align-items:center;min-height:78rpx;border-bottom:1rpx solid #e8ece5}.group>view:last-child{border:0}.group b{color:#56b005;font-size:29rpx}.group text{font-size:25rpx}.group i{color:#8b9288;font-size:32rpx}.group .logout b,.group .logout text{color:#ef4f54}
</style>
