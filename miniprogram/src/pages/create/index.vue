<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'

const waybillNo = ref('WD-20260722-001')
const sampleName = ref('生物样本批次A')
const batch = ref('B20260722')
const range = ref('2 ~ 8℃')
const sender = ref('高校实验室')
const receiver = ref('医院检验科')
const carrier = ref('李强')
const arrival = ref('2026-07-23 10:00')
const device = ref('CLD-001')
const box = ref('BOX-A12')
const seal = ref('SEAL-8891')
const saved = ref(false)
const precheckOK = computed(() => Boolean(device.value && box.value && seal.value))

function scanDevice() {
  uni.scanCode({ success: ({ result }) => { device.value = result || device.value }, fail: () => uni.showToast({ title: '演示模式：已保留 CLD-001', icon: 'none' }) })
}
function saveDraft() { saved.value = true; uni.showToast({ title: '草稿已保存', icon: 'success' }) }
function complete() {
  if (!sampleName.value.trim() || !precheckOK.value) return uni.showToast({ title: '请补全运单和设备信息', icon: 'none' })
  uni.showModal({ title: '预检通过', content: `运单 ${waybillNo.value} 已完成建档、设备绑定和首条预检。`, showCancel: false, success: () => uni.navigateBack() })
}
onLoad(() => { waybillNo.value = `WD-${new Date().toISOString().slice(0,10).replace(/-/g,'')}-001` })
</script>

<template>
  <view class="create-page">
    <view class="steps"><view class="active"><b>1</b>创建运单</view><i /><view><b>2</b>绑定设备</view><i /><view><b>3</b>装箱预检</view></view>
    <view class="panel form-panel">
      <view class="field"><label>运单号 ⓘ</label><view class="fixed"><input v-model="waybillNo" disabled /><text>后端生成，不可修改</text></view></view>
      <view class="field"><label>样本名称</label><input v-model="sampleName" /></view>
      <view class="field"><label>批次</label><input v-model="batch" /></view>
      <view class="field"><label>温控范围</label><input v-model="range" /></view>
      <view class="field"><label>发货单位</label><input v-model="sender" /></view>
      <view class="field"><label>收货单位</label><input v-model="receiver" /></view>
      <view class="field"><label>承运人</label><input v-model="carrier" /></view>
      <view class="field"><label>预计送达</label><input v-model="arrival" /></view>
    </view>
    <view class="panel device-panel">
      <view class="panel-head"><b>设备绑定</b><button @tap="scanDevice">⌗ 扫码绑定设备</button></view>
      <view class="device-grid"><view><label>设备</label><input v-model="device" /></view><view><label>箱体</label><input v-model="box" /></view><view><label>封签</label><input v-model="seal" /></view></view>
      <view class="device-state"><text>● 在线</text><text>▰ 电量 86%</text><text>⌁ 传感器正常</text></view>
    </view>
    <view class="panel precheck">
      <view class="panel-head"><b>首条监测预检 <em>OK</em></b></view>
      <view class="metrics"><view><label>温度</label><b>4.1℃</b></view><view><label>湿度</label><b>60%</b></view><view><label>时间</label><b>10:02</b></view><view><label>预检结果</label><b>✓ 合格</b></view></view>
      <view class="ok-note">✓ 设备运行正常，温湿度在可控范围内，可进行下一步交接。</view>
      <view class="warn-note">▲ 若温湿度超出范围，请检查设备与箱体密封后重新预检。</view>
    </view>
    <view class="actions"><button class="draft" @tap="saveDraft">▤ {{ saved ? '已存草稿' : '存草稿' }}</button><button class="complete" @tap="complete">✓ 完成预检并待交接</button></view>
  </view>
</template>

<style scoped>
.create-page{min-height:100vh;padding:24rpx 30rpx 170rpx;box-sizing:border-box;color:#263222;background:#fbfcf9}.steps{display:flex;align-items:center;justify-content:center;gap:10rpx;margin:10rpx 0 22rpx}.steps>view{display:flex;align-items:center;gap:9rpx;padding:12rpx 20rpx;border:1rpx solid #dfe5d9;border-radius:999rpx;color:#72796e;font-size:23rpx}.steps b{display:flex;align-items:center;justify-content:center;width:35rpx;height:35rpx;border-radius:50%;background:#edf0eb}.steps .active{color:#fff;border-color:#67c700;background:linear-gradient(90deg,#8de220,#4daf00)}.steps .active b{color:#4da900;background:#fff}.steps i{width:46rpx;border-top:2rpx dashed #aaca83}.panel{margin-bottom:18rpx;padding:24rpx;border:1rpx solid #dcebc8;border-radius:24rpx;background:#fff;box-shadow:0 8rpx 24rpx rgba(62,105,26,.08)}.field{display:grid;grid-template-columns:145rpx 1fr;align-items:center;min-height:67rpx;border:0}.field label{font-size:24rpx}.field input{height:54rpx;padding:0 16rpx;border-radius:9rpx;background:#f4f5f3;font-size:24rpx}.fixed{display:flex;align-items:center;gap:10rpx}.fixed input{flex:1;color:#56ad08;background:#f3f9eb}.fixed text{padding:8rpx 10rpx;border:1rpx solid #d8e8c6;border-radius:6rpx;color:#5cac14;font-size:17rpx}.panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18rpx}.panel-head b{font-size:29rpx}.panel-head button{height:52rpx;padding:0 18rpx;border:2rpx solid #59b807;border-radius:16rpx;color:#54ae06;background:#fff;font-size:20rpx;line-height:48rpx}.device-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16rpx}.device-grid label{display:block;margin-bottom:7rpx;color:#697164;font-size:20rpx}.device-grid input{height:56rpx;padding:0 12rpx;border-radius:9rpx;background:#f2f3f1;font-size:23rpx}.device-state,.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:15rpx;margin-top:15rpx}.device-state text{padding:12rpx 4rpx;border:1rpx solid #d8e8c7;border-radius:10rpx;color:#52ae06;text-align:center;font-size:19rpx}.panel-head em{margin-left:10rpx;padding:5rpx 13rpx;border-radius:999rpx;color:#fff;background:#5fba08;font-size:19rpx;font-style:normal}.metrics{grid-template-columns:repeat(4,1fr);text-align:center}.metrics>view{border-right:1rpx solid #e2e8dc}.metrics>view:last-child{border:0}.metrics label{display:block;color:#777f72;font-size:19rpx}.metrics b{display:block;margin-top:10rpx;color:#53b006;font-size:28rpx}.ok-note,.warn-note{margin-top:17rpx;padding:10rpx 14rpx;border-radius:9rpx;font-size:18rpx}.ok-note{color:#4e9f0d;background:#f1f9e9}.warn-note{color:#d78800;background:#fff8e7}.actions{position:fixed;z-index:10;left:0;right:0;bottom:0;display:grid;grid-template-columns:1fr 1.5fr;gap:28rpx;padding:22rpx 36rpx calc(22rpx + env(safe-area-inset-bottom));background:rgba(255,255,255,.96);box-shadow:0 -8rpx 25rpx rgba(44,73,22,.08)}.actions button{height:76rpx;border-radius:999rpx;font-size:25rpx;line-height:76rpx}.draft{border:1rpx solid #8d9588;color:#656d61;background:#fff}.complete{color:#fff;background:linear-gradient(90deg,#7bd50d,#48ad00);box-shadow:0 10rpx 23rpx rgba(78,178,0,.24)}
</style>
