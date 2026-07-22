<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { appConfig } from '@/config/env'
import { authService } from '@/services/auth'
import { errorMessage } from '@/services/request'
import { useSessionStore } from '@/stores/session'
import type { RegisterInput } from '@/types/api'

const session = useSessionStore()
const mode = ref<'login' | 'register'>('login')
const submitting = ref(false)
const agreed = ref(false)
const showPassword = ref(false)
const loginForm = reactive({ phone: '', password: '' })
const registerForm = reactive<RegisterInput & { confirmPassword: string }>({
  name: '', phone: '', organization: '', role: 'sender', password: '', confirmPassword: '',
})
const roles = [
  { value: 'sender', label: '发货单位', desc: '创建运单与装箱预检' },
  { value: 'carrier', label: '承运人员', desc: '接收交接与运输监控' },
  { value: 'receiver', label: '收货单位', desc: '到货验收与质量复核' },
] as const
const title = computed(() => mode.value === 'login' ? '欢迎登录' : '创建账户')

onLoad(() => {
  session.restore()
  if (session.user) uni.reLaunch({ url: '/pages/tasks/index' })
})
function switchMode(next: 'login' | 'register') {
  mode.value = next; agreed.value = false
}
function validPhone(phone: string) { return /^1\d{10}$/.test(phone.replace(/\s/g, '')) }
function forgotPassword() { uni.showToast({ title: '请联系系统管理员重置密码', icon: 'none' }) }
function validate() {
  if (mode.value === 'login') {
    if (!validPhone(loginForm.phone)) return '请输入正确的11位手机号'
    if (loginForm.password.length < 6) return '密码至少需要6位'
  } else {
    if (!registerForm.name.trim()) return '请输入真实姓名'
    if (!validPhone(registerForm.phone)) return '请输入正确的11位手机号'
    if (!registerForm.organization.trim()) return '请输入所属单位'
    if (registerForm.password.length < 6) return '密码至少需要6位'
    if (registerForm.password !== registerForm.confirmPassword) return '两次输入的密码不一致'
  }
  if (!agreed.value) return '请阅读并同意用户协议和隐私政策'
  return ''
}
async function submit() {
  const message = validate()
  if (message) return uni.showToast({ title: message, icon: 'none' })
  submitting.value = true
  try {
    const result = mode.value === 'login'
      ? await authService.login(loginForm)
      : await authService.register({
        name: registerForm.name, phone: registerForm.phone, organization: registerForm.organization,
        role: registerForm.role, password: registerForm.password,
      })
    session.setSession(result)
    uni.showToast({ title: mode.value === 'login' ? '登录成功' : '注册成功', icon: 'success' })
    setTimeout(() => uni.reLaunch({ url: '/pages/tasks/index' }), 400)
  } catch (error) { uni.showToast({ title: errorMessage(error), icon: 'none', duration: 2500 }) }
  finally { submitting.value = false }
}
</script>

<template>
  <view class="auth-page">
    <view class="orb orb-one"/><view class="orb orb-two"/>
    <view class="brand"><view class="brand-icon">冷</view><view><b>BIO COLD CHAIN</b><text>可信冷链责任追溯</text></view></view>
    <view class="welcome"><text>SECURE ACCESS</text><view>{{ title }}<i>.</i></view><p>{{ mode === 'login' ? '登录后，系统将根据账户权限进入工作台' : '完成实名认证信息，建立您的责任主体账户' }}</p></view>

    <view class="auth-card">
      <view class="tabs"><view :class="{ active: mode === 'login' }" @tap="switchMode('login')">登录</view><view :class="{ active: mode === 'register' }" @tap="switchMode('register')">注册</view></view>

      <template v-if="mode === 'login'">
        <view class="field"><label>手机号码</label><view class="input-wrap"><text>+86</text><input v-model="loginForm.phone" type="number" maxlength="11" placeholder="请输入注册手机号" /></view></view>
        <view class="field"><label>登录密码</label><view class="input-wrap"><text>●</text><input v-model="loginForm.password" :password="!showPassword" maxlength="32" placeholder="请输入登录密码" /><b @tap="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</b></view></view>
        <view class="helper"><text>账号由个人注册获得</text><text @tap="forgotPassword">忘记密码？</text></view>
      </template>

      <template v-else>
        <view class="form-grid"><view class="field"><label>姓名</label><view class="input-wrap"><input v-model="registerForm.name" maxlength="20" placeholder="请输入真实姓名" /></view></view><view class="field"><label>手机号码</label><view class="input-wrap"><input v-model="registerForm.phone" type="number" maxlength="11" placeholder="用于登录和身份核验" /></view></view></view>
        <view class="field"><label>所属单位</label><view class="input-wrap"><input v-model="registerForm.organization" maxlength="40" placeholder="例如：高校实验室 / 冷链物流公司" /></view></view>
        <view class="field"><label>账户角色</label><view class="role-list"><view v-for="item in roles" :key="item.value" :class="{ selected: registerForm.role === item.value }" @tap="registerForm.role = item.value"><b>{{ item.label }}</b><text>{{ item.desc }}</text><i>{{ registerForm.role === item.value ? '✓' : '' }}</i></view></view></view>
        <view class="form-grid"><view class="field"><label>设置密码</label><view class="input-wrap"><input v-model="registerForm.password" password maxlength="32" placeholder="至少6位" /></view></view><view class="field"><label>确认密码</label><view class="input-wrap"><input v-model="registerForm.confirmPassword" password maxlength="32" placeholder="再次输入" /></view></view></view>
      </template>

      <view class="agreement" @tap="agreed = !agreed"><view :class="{ checked: agreed }">{{ agreed ? '✓' : '' }}</view><text>我已阅读并同意《用户协议》和《隐私政策》</text></view>
      <button class="submit" :disabled="submitting" @tap="submit">{{ submitting ? '正在处理…' : mode === 'login' ? '安全登录' : '注册并登录' }}</button>
      <view class="switch-tip" @tap="switchMode(mode === 'login' ? 'register' : 'login')">{{ mode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录' }}</view>
    </view>

    <view class="security"><view>♢</view><text>账户角色由注册信息决定，所有登录与操作均留痕</text></view>
    <view v-if="appConfig.useMock" class="dev-note">当前为离线开发模式 · 账户仅保存在本机</view>
  </view>
</template>

<style scoped>
.auth-page{position:relative;min-height:100vh;overflow:hidden;padding:42rpx 36rpx 45rpx;box-sizing:border-box;color:#24351e;background:linear-gradient(180deg,#f4ffe4 0,#fbfcf9 40%,#f6faef 100%)}.orb{position:absolute;border-radius:50%;background:rgba(174,243,64,.2)}.orb-one{right:-150rpx;top:-140rpx;width:440rpx;height:440rpx}.orb-two{left:-180rpx;bottom:80rpx;width:350rpx;height:350rpx}.brand{position:relative;z-index:1;display:flex;align-items:center;gap:17rpx}.brand-icon{display:flex;align-items:center;justify-content:center;width:68rpx;height:68rpx;border-radius:20rpx;color:#fff;background:linear-gradient(145deg,#86db17,#47a900);box-shadow:0 10rpx 22rpx rgba(73,164,0,.24);font-size:27rpx;font-weight:800}.brand b,.brand text{display:block}.brand b{color:#4ca600;font-size:18rpx;letter-spacing:3rpx}.brand text{margin-top:4rpx;color:#62715b;font-size:21rpx}.welcome{position:relative;z-index:1;padding:48rpx 5rpx 28rpx}.welcome>text{color:#59b208;font-size:18rpx;font-weight:750;letter-spacing:5rpx}.welcome>view{margin-top:11rpx;color:#17320f;font-size:49rpx;font-weight:850}.welcome i{color:#5db507;font-style:normal}.welcome p{margin:11rpx 0 0;color:#7d8d76;font-size:23rpx;line-height:1.5}.auth-card{position:relative;z-index:2;padding:27rpx;border:1rpx solid #e0ead8;border-radius:29rpx;background:rgba(255,255,255,.97);box-shadow:0 18rpx 45rpx rgba(47,86,18,.1)}.tabs{display:grid;grid-template-columns:1fr 1fr;margin-bottom:27rpx;padding:6rpx;border-radius:16rpx;background:#f0f4ed}.tabs view{padding:15rpx;border-radius:12rpx;color:#7a8475;text-align:center;font-size:25rpx;font-weight:700}.tabs .active{color:#fff;background:linear-gradient(90deg,#80d912,#4dad00);box-shadow:0 7rpx 18rpx rgba(77,169,0,.2)}.field{margin-bottom:21rpx}.field label{display:block;margin:0 0 9rpx 4rpx;color:#596653;font-size:21rpx}.input-wrap{display:flex;align-items:center;height:76rpx;padding:0 19rpx;border:1rpx solid #dfe6da;border-radius:14rpx;background:#fafcf8}.input-wrap:focus-within{border-color:#73c92e;box-shadow:0 0 0 4rpx #eef9e5}.input-wrap>text{margin-right:13rpx;color:#54ac06;font-size:20rpx}.input-wrap input{flex:1;font-size:23rpx}.input-wrap b{color:#58ac0b;font-size:18rpx;font-weight:500}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:15rpx}.helper,.agreement,.security{display:flex;align-items:center}.helper{justify-content:space-between;margin-top:-8rpx;color:#8b9686;font-size:18rpx}.helper text:last-child{color:#51a806}.role-list{display:grid;grid-template-columns:repeat(3,1fr);gap:10rpx}.role-list>view{position:relative;min-height:105rpx;padding:16rpx 12rpx;box-sizing:border-box;border:1rpx solid #e0e6dc;border-radius:13rpx;background:#fafcf9}.role-list .selected{border-color:#68bf1c;background:#f1fae9}.role-list b,.role-list text{display:block}.role-list b{font-size:20rpx}.role-list text{margin-top:6rpx;color:#899483;font-size:15rpx;line-height:1.35}.role-list i{position:absolute;right:8rpx;top:7rpx;color:#55ad06;font-size:18rpx;font-style:normal}.agreement{gap:11rpx;margin-top:22rpx;color:#778271;font-size:18rpx}.agreement>view{display:flex;align-items:center;justify-content:center;width:30rpx;height:30rpx;border:2rpx solid #b8c2b3;border-radius:8rpx}.agreement .checked{color:#fff;border-color:#58b007;background:#58b007}.submit{height:82rpx;margin-top:23rpx;border:0;border-radius:16rpx;color:#fff;background:linear-gradient(90deg,#83dc15,#4aab00);box-shadow:0 12rpx 24rpx rgba(75,165,0,.23);font-size:27rpx;font-weight:750;line-height:82rpx}.switch-tip{padding:21rpx 0 2rpx;color:#52a906;text-align:center;font-size:20rpx}.security{position:relative;z-index:1;justify-content:center;gap:10rpx;margin-top:25rpx;color:#72816c;font-size:19rpx}.security view{color:#55ad07}.dev-note{position:relative;z-index:1;margin-top:15rpx;color:#a2aca0;text-align:center;font-size:17rpx}
</style>
