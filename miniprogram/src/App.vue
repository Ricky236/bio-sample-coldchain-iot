<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'
import { useSessionStore } from '@/stores/session'

onLaunch(() => useSessionStore().restore())
</script>

<style lang="scss">
page {
  background: #fbfcf9;
  color: #24321f;
  font-size: 28rpx;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}

.page {
  min-height: 100vh;
  padding: 28rpx;
  padding-bottom: calc(44rpx + env(safe-area-inset-bottom));
  box-sizing: border-box;
}

.card {
  position: relative;
  overflow: hidden;
  margin-bottom: 22rpx;
  padding: 30rpx;
  background: #fff;
  border: 1rpx solid #e6ebf2;
  border-radius: 24rpx;
  box-shadow: 0 8rpx 24rpx rgba(38, 54, 88, .07);
}

.title {
  margin-bottom: 10rpx;
  color: #102a43;
  font-size: 42rpx;
  font-weight: 750;
  line-height: 1.25;
}

.muted { color: #91a1b5; }
.danger { color: #d84a55; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 20rpx; }

.primary,
.secondary {
  height: 88rpx;
  border: 0;
  border-radius: 18rpx;
  font-size: 30rpx;
  font-weight: 650;
  line-height: 88rpx;
}

.primary {
  color: #fff;
  background: linear-gradient(135deg, #86dc16 0%, #49ad00 100%);
  box-shadow: 0 12rpx 24rpx rgba(76, 170, 0, .22);
}

.secondary { color: #50aa03; background: #eff9e7; }
button::after { border: 0; }
button[disabled] { opacity: .48; }

.field {
  min-height: 54rpx;
  padding: 20rpx 0;
  border-bottom: 1rpx solid #edf1f6;
}

.field:last-child { border-bottom: 0; }
.label { color: #8b9bb0; }

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24rpx;
}

.section-title { color: #132b43; font-size: 32rpx; font-weight: 720; }
.section-hint { color: #a0aec0; font-size: 24rpx; }
</style>
