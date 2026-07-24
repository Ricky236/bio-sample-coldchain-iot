# 生物样本冷链 IoT — 后端接口文档

> 版本：与仓库 `backend/main.py` 同步（2026-07-24）  
> 机器可读契约：同目录 [`openapi.json`](./openapi.json)  
> 精简联调说明：[`API_CONTRACT.md`](./API_CONTRACT.md)  
> 前端对接备忘：[`FRONTEND_HANDOFF.md`](./FRONTEND_HANDOFF.md)

---

## 1. 概述

### 1.1 基础地址

| 环境 | 示例 |
|------|------|
| 本地开发 | `http://127.0.0.1:8000` |
| 公网硬件落库机 | `http://47.103.152.175:8080` |

正式联调只替换域名/端口，**不改变** `/api/v1/...` 路径。

### 1.2 双轨接口

| 体系 | 前缀 | 用途 | 响应格式 |
|------|------|------|----------|
| **正式 API** | `/api/v1/*` | 小程序 / Web / 管理端 | `{ "code", "message", "data" }` |
| **Legacy** | `/api/device/*`、`/api/task/*` | 开发板直传、演示看板 | `{ "ok": true, ... }` |
| **页面** | `/`、`/home` | HTML 演示看板 | HTML（需开关） |

### 1.3 通用约定

- 字段名：`snake_case`
- 时间：ISO 8601，如 `2026-07-24T22:53:14+08:00`
- 鉴权（正式接口，除注册/登录/元数据/公开快照外）：`Authorization: Bearer <token>`
- 设备正式上传另需签名头：`X-Device-Id`、`X-Timestamp`、`X-Nonce`、`X-Signature`
- 幂等写操作建议带：`Idempotency-Key`（创建运单、发出、到达、签收、拒收等）
- 无权资源多返回 **HTTP 404**（防枚举）；明确角色不足返回 **403**
- FastAPI 校验失败：HTTP **422**，响应为框架 `detail` 列表（非业务信封）

### 1.4 成功 / 错误信封（`/api/v1`）

成功：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

业务错误：

```json
{
  "code": 40401,
  "message": "task not found",
  "data": null
}
```

---

## 2. 认证与角色

### 2.1 角色

| 角色 | 说明 |
|------|------|
| `admin` | 管理员（建议用 `create_admin.py` 初始化，默认禁止公开注册） |
| `sender` | 发货方 |
| `carrier` | 承运方 |
| `receiver` | 接收方 |

### 2.2 权限名（`GET /api/v1/auth/permissions`）

| 角色 | permissions |
|------|-------------|
| admin | `view_task`, `start_task`, `sign_task`, `reject_task`, `view_report`, `view_alarm`, `upload_location`, `manage_user`, `manage_device`, `manage_alarm` |
| sender | `view_task`, `start_task`, `view_report`, `manage_device` |
| carrier | `view_task`, `view_alarm`, `upload_location` |
| receiver | `view_task`, `sign_task`, `reject_task`, `view_report` |

实际接口以服务端 `can_view_task` / `can_modify_task` / `can_handle_alarm` 等为准。

### 2.3 运单操作权限速查

| 操作 | 允许 |
|------|------|
| 创建 / 编辑 / 预检 / 取消 / 指派 / 发出 / 删除 | 运单 owner（发货方）或 admin |
| 设备登记 / 绑定 / 解绑 | 设备所有者且为发货方，或 admin |
| 到达 | 已指派承运方或 admin |
| 签收 / 拒收 | 已指派接收方或 admin |
| 查询遥测 / 告警 / 报告 / PDF | 运单相关方或 admin |
| 告警 ack / resolve | **当前责任人**（按 custody：发出前 sender，在途 carrier，签收后 receiver）或 admin |
| 管理用户 / 审计 / 大屏 | admin |

### 2.4 责任链（custody）

| 运单状态 | 当前责任角色 |
|----------|--------------|
| `signed` | receiver |
| `in_transit` / `arrived` | carrier |
| 其它（装箱、待交接、拒收、取消等） | sender（owner） |

---

## 3. 状态与枚举

完整枚举以 **`GET /api/v1/meta/contracts`** 为准。

| 类别 | 取值 |
|------|------|
| 运单 | `pending_pack`, `pending_handoff`, `in_transit`, `arrived`, `signed`, `rejected`, `canceled` |
| 箱体 | `BOX_OPEN`, `BOX_CLOSED` |
| 运动 | `STABLE`, `MILD`, `SEVERE`, `IMPACT`, `FREE_FALL` |
| 温度状态 | `TEMP_OK`, `TEMP_ALERT` |
| 设备 | `available`, `bound`, `online`, `offline` |
| 告警 | `new`, `acknowledged`, `resolved` |
| 交接 | `pending`, `confirmed`, `rejected` |
| 交接类型 | `sender_to_carrier`, `carrier_to_carrier`, `carrier_to_receiver` |

运单号格式：`WD-YYYYMMDD-NNN`（演示种子单可为 `TASK-001`）。

---

## 4. 接口总览

共 **71** 条路径（含 HTML）。下表为正式与 Legacy 主要入口。

### 4.1 元数据 / 认证 / 用户

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/v1/meta/contracts` | 公开 | 枚举与契约 |
| POST | `/api/v1/auth/register` | 公开 | 注册（默认禁 admin） |
| POST | `/api/v1/auth/login` | 公开 | 登录（限流） |
| GET | `/api/v1/auth/me` | Bearer | 当前用户 |
| GET | `/api/v1/auth/permissions` | Bearer | 权限列表 |
| POST | `/api/v1/auth/refresh` | Bearer | 刷新 Token |
| POST | `/api/v1/auth/logout` | Bearer | 退出 |
| GET | `/api/v1/users` | Bearer（sender/admin） | 可指派人员目录 |

### 4.2 运单

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/v1/tasks` | Bearer | 列表（分页/筛选） |
| POST | `/api/v1/tasks` | Bearer | 创建 |
| GET | `/api/v1/tasks/{task_id}` | Bearer | 详情 |
| PATCH | `/api/v1/tasks/{task_id}` | Bearer | 编辑未发出单 |
| DELETE | `/api/v1/tasks/{task_id}` | Bearer | 硬删除待发出/已取消 |
| POST | `/api/v1/tasks/{task_id}/assign` | Bearer | 指派承运/接收 |
| POST | `/api/v1/tasks/{task_id}/cancel` | Bearer | 取消 |
| POST | `/api/v1/tasks/{task_id}/precheck` | Bearer | 装箱预检 |
| POST | `/api/v1/tasks/{task_id}/start` | Bearer | 发出 → `in_transit` |
| POST | `/api/v1/tasks/{task_id}/arrive` | Bearer | 到达 → `arrived` |
| POST | `/api/v1/tasks/{task_id}/sign` | Bearer | 签收 → `signed` |
| POST | `/api/v1/tasks/{task_id}/reject` | Bearer | 拒收 → `rejected` |
| GET | `/api/v1/tasks/{task_id}/trace-report` | Bearer | JSON 追溯报告 |
| GET | `/api/v1/tasks/{task_id}/trace-report.pdf` | Bearer | PDF 下载 |
| GET | `/api/v1/tasks/{task_id}/hardware/snapshot` | Bearer | 硬件快照 |

### 4.3 设备 / 遥测

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/v1/devices` | Bearer | 登记设备 |
| GET | `/api/v1/devices` | Bearer | 设备列表 |
| POST | `/api/v1/devices/{device_id}/bind` | Bearer | 绑定运单 |
| POST | `/api/v1/devices/{device_id}/unbind` | Bearer | 解绑 |
| GET | `/api/v1/devices/{device_id}/bindings` | Bearer | 绑定历史 |
| POST | `/api/v1/devices/{device_id}/rotate-secret` | Bearer | 轮换密钥 |
| POST | `/api/v1/devices/{device_id}/bind-check` | Bearer | 绑定前占用检查 |
| GET | `/api/v1/devices/{device_id}/precheck` | Bearer | 传感器预检 |
| POST | `/api/v1/devices/{device_id}/simulate-reading` | Bearer + 模拟开关 | 本地模拟遥测 |
| POST | `/api/v1/device/telemetry` | 设备签名 | 正式遥测上传 |
| POST | `/api/v1/device/heartbeat` | 设备签名 | 心跳 |
| GET | `/api/v1/tasks/{task_id}/telemetry/latest` | Bearer | 最新监测 |
| GET | `/api/v1/tasks/{task_id}/telemetry/history` | Bearer | 监测历史 |

### 4.4 告警 / 交接 / 二维码 / 人脸 / 文件 / 通知

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/v1/tasks/{task_id}/alarms` | Bearer | 告警列表 |
| POST | `/api/v1/alarms/{alarm_id}/ack` | Bearer | 确认 |
| POST | `/api/v1/alarms/{alarm_id}/resolve` | Bearer | 处置关闭 |
| POST | `/api/v1/tasks/{task_id}/handoffs` | Bearer | 发起交接 |
| GET | `/api/v1/tasks/{task_id}/handoffs` | Bearer | 交接列表 |
| GET | `/api/v1/handoffs/{handoff_id}` | Bearer | 交接详情 |
| POST | `/api/v1/handoffs/{handoff_id}/confirm` | Bearer | 确认交接（须已验 QR） |
| POST | `/api/v1/handoffs/{handoff_id}/reject` | Bearer | 拒绝交接 |
| POST | `/api/v1/tasks/{task_id}/qr-tokens` | Bearer | 生成 QR |
| POST | `/api/v1/qr-tokens/verify` | Bearer | 验 QR（一次性） |
| POST | `/api/v1/qr-tokens/{token_id}/revoke` | Bearer | 撤销 QR |
| POST | `/api/v1/face/enroll` | Bearer | 录入人脸占位 |
| GET / DELETE | `/api/v1/face/profile` | Bearer | 查询 / 注销 |
| POST | `/api/v1/face/verify` | Bearer | 交接人脸核验 |
| POST | `/api/v1/face/simulate-verify` | Bearer + 模拟开关 | 模拟核验通过 |
| POST | `/api/v1/files/upload` | Bearer | multipart 上传 |
| POST | `/api/v1/files` | Bearer | 仅登记元数据 |
| GET | `/api/v1/files/{file_id}` | Bearer | 元数据 |
| GET | `/api/v1/files/{file_id}/download` | Bearer | 下载 |
| GET | `/api/v1/notifications` | Bearer | 通知列表 |
| POST | `/api/v1/notifications/{id}/read` | Bearer | 标记已读 |

### 4.5 管理 / 大屏

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/v1/admin/users` | admin | 用户列表 |
| PATCH | `/api/v1/admin/users/{user_id}/status` | admin | 启用/停用 |
| GET | `/api/v1/admin/tasks` | admin | 全部运单 |
| GET | `/api/v1/admin/audit-logs` | admin | 审计日志 |
| GET | `/api/v1/admin/face-reviews` | admin | 人脸复核 |
| POST | `/api/v1/admin/face-reviews/{id}/approve\|reject` | admin | 复核通过/拒绝 |
| GET | `/api/v1/admin/live-snapshot` | **公开** | 本机 SQLite 硬件快照 |
| GET | `/api/v1/dashboard/summary` | admin | 聚合指标 |

### 4.6 Legacy（需 `ENABLE_LEGACY_DEMO_API=true`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/device/data` | 开发板上传（无签名） |
| GET | `/api/device/latest` | 最新一条 |
| GET | `/api/device/history` | 最近 100 条 |
| GET | `/api/device/events` | 最近 100 条异常 |
| POST | `/api/task/start` | 演示单发出 |
| POST | `/api/task/sign` | 演示单签收 |
| GET | `/api/task/current` | 当前演示任务 |
| GET | `/api/task/report` | 演示报告 |

### 4.7 演示看板（需 `ENABLE_DEMO_DASHBOARD=true`）

| 方法 | 路径 |
|------|------|
| GET | `/` |
| GET | `/home` |

---

## 5. 详细说明（按域）

### 5.1 认证

#### `POST /api/v1/auth/register`

```json
{
  "phone": "13800000000",
  "password": "secret12",
  "name": "张三",
  "organization": "高校实验室",
  "role": "sender"
}
```

- `username` 与 `phone` 二选一  
- `role=admin` 且未开 `ALLOW_ADMIN_SELF_REGISTER` → `40303`

#### `POST /api/v1/auth/login`

```json
{ "phone": "13800000000", "password": "secret12" }
```

返回 `token` / `access_token` 与用户信息。登录限流：超限 `42901`。

---

### 5.2 运单生命周期

推荐小程序流程：

```text
创建任务 → bind 设备 → 设备预检 → 任务预检 → assign
  → 发出交接(handoff + QR + 确认) → start
  → 在途监测/告警 → arrive
  → 接收交接(handoff + QR + 确认) → sign / reject
  → trace-report / PDF
```

#### `POST /api/v1/tasks`

创建时 **禁止** 传 `device_id`（`42206`），必须走绑定接口。

主要字段：`sample_name`（必填）、`batch`、`receiver`、`carrier`、`expected_arrival`、`box_id`、`seal_id`、`temperature_min`、`temperature_max`。

#### `POST /api/v1/tasks/{task_id}/start`

- 正式单须预检通过 + 已指派承运/接收，否则 `40902`
- 演示种子单 `TASK-001` 在 `SEED_DEMO_TASK=true` 时可放宽

#### `POST /api/v1/tasks/{task_id}/sign` / `reject`

- 正式签收须完成 `carrier_to_receiver` 交接证据，否则 `40933`
- 拒收体：`{ "reason": "温度异常" }`（必填）

#### `GET /api/v1/tasks/{task_id}/trace-report`

`data` 含：

| 字段 | 说明 |
|------|------|
| `task` | 运单详情 |
| `latest` | 最新遥测 |
| `summary` | `total_records`、`avg_temperature`、`event_count` 等 |
| `events` | 最近异常 |
| `handoff_nodes` | 责任节点 |
| `evidence_files` | 证据文件 |
| `trace_hash` | 追溯摘要哈希 |

`summary` 的温湿度统计按 **`device_data.task_id = 当前运单`** 聚合。硬件若写死 `TASK-001`，服务端会在落库时改写为当前绑定运单（见 §7）。

---

### 5.3 设备与预检

#### `GET /api/v1/devices/{device_id}/precheck`

Query：

| 参数 | 必填 | 说明 |
|------|------|------|
| `min_temp` | 是 | 合格下限 |
| `max_temp` | 是 | 合格上限 |
| `allow_local` | 否 | 是否允许回退本地测试数据 |

返回要点：`online`、`passed`、`temperature`、`source`（`hardware`/`local`/`none`）、`reason`、`age_seconds`。

#### `POST /api/v1/devices/{device_id}/bind`

体：`{ "task_id": "WD-20260724-001" }`  
成功后 `devices.current_task_id` 与 `task_handoff.device_id` 同步。

---

### 5.4 正式设备上传（签名）

#### `POST /api/v1/device/telemetry`

请求体要点：`device_id`、`task_id`、`temperature`、`humidity`、`light_raw`、`box_status`、`move_status`，可选 `sequence`、`battery`、`location` 等。

签名要求：

1. 设备已登记并配置 `device_secret`
2. 设备已绑定目标运单（校验时使用 **改写后** 的运单号）
3. 头：`X-Device-Id`、`X-Timestamp`、`X-Nonce`、`X-Signature`
4. 时间偏差 ≤ 5 分钟；nonce 不可重放

签名算法概要：对规范化 JSON 体做 SHA-256，再用 `sha256(device_secret)` 作密钥 HMAC-SHA256。

常见错误：`40120`～`40126`、`40920`、`40940`。

#### `POST /api/v1/device/heartbeat`

字段：`device_id`、可选 `task_id`/`battery`/`rssi`/`network`。

---

### 5.5 告警

#### `GET /api/v1/tasks/{task_id}/alarms?limit=100`

合并本地 `event_log` 与同设备 live 事件；live 事件会同步到本地供 ack/resolve。

#### `POST /api/v1/alarms/{alarm_id}/ack`

无体或空体。

#### `POST /api/v1/alarms/{alarm_id}/resolve`

```json
{ "resolution": "已现场复核，箱体已关闭" }
```

---

### 5.6 交接与二维码

1. `POST .../handoffs`：`{ "handoff_type", "to_user_id" }`
2. `POST .../qr-tokens`：`{ "action", "handoff_id", "ttl_seconds" }`（TTL 10～300）
3. 对方 `POST /qr-tokens/verify`：`{ "token" }`（一次性消费）
4. `POST .../handoffs/{id}/confirm`（须已验 QR，否则 `40932`）

人脸为 MVP 占位：`liveness_passed=true` 且 `similarity_score >= 0.8` 视为通过，否则进人工复核。

---

### 5.7 证据文件

#### `POST /api/v1/files/upload`（`multipart/form-data`）

| 字段 | 说明 |
|------|------|
| `task_id` | 必填 |
| `usage` | 必填（如 `precheck`、交接证据等，非法 → `42210`） |
| `file` | JPEG/PNG/PDF，≤ 5MB |
| `related_type` / `related_id` / `expected_sha256` | 可选 |

下载必须带有效 Bearer，不暴露磁盘路径。

---

### 5.8 Legacy 开发板

```http
POST /api/device/data
Content-Type: application/json
```

```json
{
  "device_id": "CLD-001",
  "task_id": "TASK-001",
  "temperature": 4.2,
  "humidity": 62.5,
  "light_raw": 120,
  "box_status": "BOX_CLOSED",
  "move_status": "STABLE",
  "temp_status": "TEMP_OK",
  "acc_total": 9.81,
  "motion_score": 0.2
}
```

未开 Legacy 开关时返回 `40400`。  
成功响应示例：`{ "ok": true, "data": { ... } }`（`data.task_id` 可能已被改写为当前运单）。

---

## 6. Query 参数速查

| 接口 | 参数 |
|------|------|
| `GET /users` | `role*`（carrier/receiver）、`keyword`、`organization`、`page`、`page_size` |
| `GET /tasks` | `status`、`keyword`、`updated_after`、`page`、`page_size` |
| `GET /devices` | `page`、`page_size` |
| `GET .../telemetry/history` | `limit`(1–100)、`start_time`、`end_time`、`cursor`、`downsample`(1–60) |
| `GET .../alarms` | `limit` |
| `GET .../handoffs` | `page`、`page_size` |
| `GET .../precheck` | `min_temp*`、`max_temp*`、`allow_local` |
| `GET /admin/live-snapshot` | `telemetry_limit`、`alarm_limit`、`device_history_limit` |
| `GET /notifications` | `limit` |
| 多数 admin 列表 | `limit` 或分页 |

---

## 7. 硬件落库与 task_id 改写

### 7.1 推荐部署形态

| 机器角色 | 配置 |
|----------|------|
| **传感器直传服务器**（如 8080） | `ENABLE_LIVE_HARDWARE_PROXY=false`，业务读**本地 SQLite**，禁止 HTTP 自拉 |
| **本机开发旁路** | `ENABLE_LIVE_HARDWARE_PROXY=true`，`HARDWARE_PUBLIC_BASE` 指向落库机 |

`GET /api/v1/admin/live-snapshot` **始终**读本机库。

### 7.2 `resolve_inbound_task_id`

开发板常写死 `task_id=TASK-001`。在 `save_device_data` / 正式 telemetry 落库前：

1. 设备已 bind 且运单非终态 → **一律归属** `devices.current_task_id`
2. 上报为 `TASK-001`，且该设备另有非演示活跃运单 → 归属该运单（优先 `in_transit`/`arrived`）
3. 其它显式 `task_id` → **不改写**

因此追溯报告的「监测记录 / 平均温度」会统计到真实运单 `WD-...` 上。

---

## 8. 业务错误码

| HTTP | code | 含义 |
|------|------|------|
| 400 | 40001 | 拒收原因为空 |
| 400 | 40020 | 人脸未 consent |
| 400 | 40021 | 未录入人脸 |
| 401 | 40101 | 账号或密码错误 |
| 401 | 40102 | 未授权 / Token 无效 |
| 401 | 40120–40126 | 设备签名 / 时间 / 未登记 / 未配网 |
| 403 | 40301 | 禁止操作 |
| 403 | 40302 | 用户已停用 |
| 403 | 40303 | 禁止注册 admin / 演示单不可删等 |
| 404 | 40400 | Legacy 已关闭 |
| 404 | 40401–40410 | 任务/设备/告警/用户/交接/QR/文件/通知/复核/文件内容不存在 |
| 409 | 40901 | 运单状态冲突 |
| 409 | 40902 | 用户名冲突或发出前置条件不足 |
| 409 | 40920/40921 | 设备与运单不匹配 / 已绑定 / 设备号占用 |
| 409 | 40930–40933 | 交接冲突 / 缺 QR / 缺接收证据 |
| 409 | 40940 | nonce 重放 |
| 409 | 40950 | 文件 SHA-256 不匹配 |
| 410 | 41010 | QR 无效/过期/已用 |
| 413 | 41301 | 文件过大 |
| 415 | 41501/41502 | 文件类型或内容非法 |
| 422 | 42202–42210 | 角色/参数/交接类型/指派对象/usage 等非法 |
| 429 | 42901 | 限流 |
| 502 | 50201 | 硬件快照代理失败 |

---

## 9. 环境变量（与接口相关）

| 变量 | 默认 | 作用 |
|------|------|------|
| `ENABLE_LEGACY_DEMO_API` | false | Legacy `/api/device|task/*` |
| `ENABLE_DEMO_DASHBOARD` | false | `/`、`/home` |
| `ENABLE_API_DOCS` | false | `/docs`、`/redoc`、`/openapi.json` |
| `ALLOW_LOCAL_SIMULATION` | false | simulate-reading / simulate-verify |
| `ALLOW_ADMIN_SELF_REGISTER` | false | 公开注册 admin |
| `ALLOW_DEVICE_AUTO_REGISTER` | true | 绑定时可按快照自动登记 |
| `ENABLE_LIVE_HARDWARE_PROXY` | true | true=拉公网；false=只读本地库 |
| `SEED_DEMO_TASK` | false | 种子 `TASK-001` |
| `HARDWARE_PUBLIC_BASE` | 公网 8080 | 代理基址 |
| `PRECHECK_MAX_DATA_AGE_SECONDS` | 120 | 预检数据时效 |
| `DATABASE_PATH` | `data/device_data.db` | SQLite |
| `FILE_STORAGE_DIR` | `data/uploads` | 证据目录 |
| `CORS_ORIGIN_REGEX` | localhost | 跨域 |

生产建议：Legacy、看板、Docs、本地模拟、admin 自注册均关闭；硬件落库机设 `ENABLE_LIVE_HARDWARE_PROXY=false`。

示例见 `backend/.env.example`、`backend/deploy/server.env.8080`。

---

## 10. 请求体模型摘要

完整字段类型见 [`openapi.json`](./openapi.json) 的 `components.schemas`。常用模型：

| 模型 | 用途 |
|------|------|
| `RegisterIn` / `LoginIn` | 注册登录 |
| `CreateTaskIn` / `UpdateTaskIn` / `AssignTaskIn` / `PrecheckTaskIn` / `RejectTaskIn` | 运单 |
| `RegisterDeviceIn` / `BindDeviceIn` / `DeviceBindCheckIn` | 设备 |
| `DeviceTelemetryIn` / `DeviceHeartbeatIn` / `LocationIn` | 正式设备上传 |
| `DeviceDataIn` | Legacy 上传 |
| `CreateHandoffIn` / `HandoffConfirmIn` / `HandoffRejectIn` | 交接 |
| `CreateQrTokenIn` / `VerifyQrTokenIn` | 二维码 |
| `FaceEnrollIn` / `FaceVerifyIn` / `FaceSimulateIn` | 人脸占位 |
| `CreateFileIn` | 文件元数据 |
| `ResolveAlarmIn` | 告警处置 |
| `LocalDeviceReadingIn` | 本地模拟遥测 |
| `UpdateUserStatusIn` | 用户启停 |

---

## 11. curl 示例

```bash
# 登录
curl -s -X POST "$BASE/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"phone":"13800000000","password":"secret12"}'

# 运单列表
curl -s "$BASE/api/v1/tasks?page=1&page_size=20" \
  -H "Authorization: Bearer $TOKEN"

# 设备预检
curl -s "$BASE/api/v1/devices/CLD-001/precheck?min_temp=-80&max_temp=8" \
  -H "Authorization: Bearer $TOKEN"

# 追溯报告
curl -s "$BASE/api/v1/tasks/WD-20260724-001/trace-report" \
  -H "Authorization: Bearer $TOKEN"

# Legacy 最新点（需开 Legacy）
curl -s "$BASE/api/device/latest"

# 本机硬件快照
curl -s "$BASE/api/v1/admin/live-snapshot?telemetry_limit=5"
```

---

## 12. 在线文档

若 `.env` 中 `ENABLE_API_DOCS=true`，可访问：

- Swagger UI：`/docs`
- ReDoc：`/redoc`
- OpenAPI JSON：`/openapi.json`

仓库内已导出静态副本：`docs/api/openapi.json`（由当前 `main:app` 生成）。

---

## 13. 相关文档

| 文件 | 内容 |
|------|------|
| [`API_CONTRACT.md`](./API_CONTRACT.md) | 正式契约精简版 |
| [`FRONTEND_HANDOFF.md`](./FRONTEND_HANDOFF.md) | 前端对接 |
| [`../backend/MVP_BACKEND_DESIGN.md`](../backend/MVP_BACKEND_DESIGN.md) | 设计说明 |
| [`../../backend/deploy/DEPLOY.md`](../../backend/deploy/DEPLOY.md) | 部署 |
| [`mock/*.json`](./mock/) | 前端开发 Mock |

---

*本文档随后端演进更新；若与运行中服务不一致，以 `ENABLE_API_DOCS=true` 时的 `/openapi.json` 或重新导出的 `docs/api/openapi.json` 为准。*
