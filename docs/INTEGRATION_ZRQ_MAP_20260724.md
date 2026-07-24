# zrq → ljy 地图与监控能力整合记录

日期：2026-07-24  
目标目录：`bio-sample-coldchain-iot`（`ljy`）  
来源目录：`bio-sample-coldchain-iot-zrq`（本地 A 类改动）

## 整合原则

1. 保留 `ljy` 装箱 / 交接 / 到达验收 / 证据上传主流程。
2. 只合入 zrq 文档中的 **A 类正式能力** 与小程序地图相关改动。
3. **不合入** live-snapshot 默认同步、运单镜像、免登录快照等 B 类联调代码。

## 已合入内容

### 后端

- `DeviceDataIn` 支持顶层 `lat` / `lng` / `accuracy`
- `save_device_data` 优先 `location`，否则回退扁平字段
- 遥测「最新」与历史相关查询改为 `ORDER BY timestamp DESC, id DESC`
- 新增 `GET /home` 看板别名

### 小程序

- 新增 `RouteTrackCard.vue`
- `pages.json` / `manifest.json` 增加定位权限（保留 ljy appid）
- `Telemetry` / `AlarmEvent` 类型扩展
- `monitor`：真实地图轨迹 + 温度 canvas 趋势（保留硬件 snapshot 优先）
- `handoff` / `task-detail` / `acceptance`：挂载轨迹卡
- `alarm-detail`：告警触发时状态快照；接入 `ackAlarm` / `resolveAlarm`
- `tasks.ts`：静默请求参数、告警确认/关闭接口

## 未合入（有意排除）

- live-snapshot 同步客户端 / 轮询 / 镜像
- `files.ts` / `sha256.ts`（验收证据继续用 ljy 的 `uploadEvidence`）
- zrq 的 `hardware/`、`web-admin/`、deploy 探测脚本（可按需另拷）
