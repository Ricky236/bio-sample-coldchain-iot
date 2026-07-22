<script setup lang="ts">
withDefaults(defineProps<{ visible: boolean; title: string; content: string; loading?: boolean; confirmText?: string }>(), {
  confirmText: '确认操作',
})
defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <view v-if="visible" class="mask" @tap.self="$emit('cancel')">
    <view class="dialog">
      <view class="dialog-mark">✓</view>
      <view class="dialog-title">{{ title }}</view>
      <view class="dialog-content">{{ content }}</view>
      <view class="actions">
        <button class="secondary" :disabled="loading" @tap="$emit('cancel')">暂不操作</button>
        <button class="primary" :loading="loading" :disabled="loading" @tap="$emit('confirm')">{{ confirmText }}</button>
      </view>
    </view>
  </view>
</template>

<style scoped>
.mask { position: fixed; inset: 0; z-index: 30; display: flex; align-items: center; justify-content: center; padding: 36rpx; background: rgba(17, 29, 50, .52); backdrop-filter: blur(6rpx); }
.dialog { width: 100%; max-width: 620rpx; padding: 40rpx 34rpx 32rpx; box-sizing: border-box; border-radius: 30rpx; background: #fff; text-align: center; box-shadow: 0 30rpx 70rpx rgba(17, 29, 50, .2); }
.dialog-mark { display: flex; align-items: center; justify-content: center; width: 80rpx; height: 80rpx; margin: 0 auto 24rpx; border-radius: 26rpx; color: #fff; background: linear-gradient(135deg, #786aff, #5146f6); font-size: 40rpx; font-weight: 700; }
.dialog-title { color: #132b43; font-size: 35rpx; font-weight: 750; }
.dialog-content { margin: 18rpx 8rpx 34rpx; color: #7e8fa4; line-height: 1.7; }
.actions { display: flex; gap: 18rpx; }
.actions button { flex: 1; margin: 0; font-size: 27rpx; }
</style>
