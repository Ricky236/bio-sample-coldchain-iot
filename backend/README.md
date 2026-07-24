# 生物样本冷链转运交接与可信追溯系统后端

这是比赛项目的 FastAPI + SQLite MVP 后端。它负责接收 UniKnect 开发板数据，并为 Web 管理端和微信小程序提供统一接口。

## 第一次运行

在新 clone 的仓库根目录打开终端，逐行执行：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

看到 `Application startup complete` 表示启动成功。浏览器可打开：

- 比赛演示看板：<http://127.0.0.1:8000/>
- Swagger 接口调试页：<http://127.0.0.1:8000/docs>
- 接口契约检查：<http://127.0.0.1:8000/api/v1/meta/contracts>

端口不是项目固定配置。如果 8000 被占用，可以换成 8020 等空闲端口，浏览器地址也对应修改。当前联调建议统一使用 8000。

默认允许来自本机 `localhost` 和 `127.0.0.1` 任意端口的 Web 开发请求。部署到明确域名时，由负责人设置 `CORS_ORIGIN_REGEX`，例如：

```bash
CORS_ORIGIN_REGEX='^https://example\.com$' uvicorn main:app --host 0.0.0.0 --port 8000
```

公开注册不能创建管理员。首次初始化管理员请在后端目录运行：

```bash
python create_admin.py --phone admin_phone --name 管理员 --organization 组委会
```

脚本会在终端隐藏输入密码，不会把默认账号或密码写入仓库。

## 每次重新启动

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

按 `Control + C` 停止服务。

## 统一数据结构与状态

### 任务 Task

任务对象统一包含以下字段（snake_case）：

```json
{
  "task_id": "TASK-001",
  "device_id": "CLD-001",
  "sample_name": "生物样本转运箱 A",
  "sender": "高校实验室",
  "receiver": "医院检验科",
  "carrier": "演示人员",
  "status": "in_transit",
  "started_at": "2026-07-21T10:00:00+08:00",
  "signed_at": null,
  "rejected_at": null,
  "rejection_reason": null,
  "updated_at": "2026-07-21T10:00:00+08:00",
  "abnormal_count": 0,
  "latest_temperature": 4.2,
  "latest_humidity": 62.5,
  "latest_box_status": "BOX_CLOSED",
  "latest_move_status": "STABLE",
  "latest_temp_status": "TEMP_OK"
}
```

### 任务状态

后端内部统一使用英文键，旧版接口和首页看板自动映射为中文：

| 状态键 | 中文显示 | 含义 |
|--------|----------|------|
| `pending_pack` | 待发出 | 未点击发出 |
| `pending_handoff` | 待发出 | 未点击发出 |
| `in_transit` | 运输中 | 已发出，运输中 |
| `arrived` | 已到达 | 已到达（预留） |
| `signed` | 已签收 | 已签收 |
| `rejected` | 已拒收 | 已拒收 |
| `canceled` | 已取消 | 已取消（预留） |

### 异常事件

异常事件规则：

- `box_status = BOX_OPEN` → 开箱
- `move_status = MILD` → 轻微晃动
- `move_status = SEVERE` → 剧烈晃动
- `move_status = IMPACT` → 疑似碰撞
- `move_status = FREE_FALL` → 疑似跌落
- `temp_status = TEMP_ALERT` → 温度异常
- `battery < 20` → 低电量
- 设备超过 5 分钟没有心跳或遥测 → 设备离线

异常事件统一字段：

```json
{
  "event_id": 1,
  "id": 1,
  "data_id": 2,
  "task_id": "TASK-001",
  "device_id": "CLD-001",
  "event_type": "BOX_OPEN",
  "event_name": "开箱",
  "event_level": "medium",
  "description": "光敏检测到箱体疑似打开",
  "event_detail": "光敏检测到箱体疑似打开",
  "timestamp": "2026-07-21T10:05:00+08:00",
  "created_at": "2026-07-21T10:05:00+08:00"
}
```

`event_level` 映射：`SEVERE/IMPACT/FREE_FALL/DEVICE_OFFLINE` 为 `high`，`TEMP_ALERT/BOX_OPEN/LOW_BATTERY` 为 `medium`，`MILD` 为 `low`。

## 团队接口分工

开发板继续使用现有地址，不需要修改程序：

- `POST /api/device/data`：上传冷链监测数据。

Web 管理端和微信小程序统一使用 `/api/v1`。当前后端 MVP 已支持：

### 账号接口

- `POST /api/v1/auth/register`：注册用户，支持 `name、phone、organization、role、password`；公开注册只允许 `sender/carrier/receiver`，默认禁止注册 `admin`。
- `POST /api/v1/auth/login`：登录，返回 Bearer Token。
- `GET /api/v1/auth/me`：获取当前用户。
- `GET /api/v1/auth/permissions`：获取当前角色权限。
- `POST /api/v1/auth/refresh`：刷新 Token，旧 Token 会失效。
- `POST /api/v1/auth/logout`：退出登录。
- `GET /api/v1/users?role=carrier|receiver`：发货方或管理员查询可指派人员的最小信息目录。

### 运单接口

- `GET /api/v1/tasks`：按当前登录用户权限查询运单列表。
- `POST /api/v1/tasks`：创建正式运单，后端自动生成 `WD-YYYYMMDD-001` 格式运单号。
- `GET /api/v1/tasks/{task_id}`：任务详情。
- `PATCH /api/v1/tasks/{task_id}`：编辑未发出的运单。
- `POST /api/v1/tasks/{task_id}/assign`：指派承运方和接收方。
- `POST /api/v1/tasks/{task_id}/precheck`：保存装箱预检，通过后进入 `pending_handoff`。
- `POST /api/v1/tasks/{task_id}/cancel`：取消未发出的运单。
- `POST /api/v1/tasks/{task_id}/start`：发出交接，进入运输中。
- `POST /api/v1/tasks/{task_id}/arrive`：到达接收点，进入已到达。
- `POST /api/v1/tasks/{task_id}/sign`：签收。
- `POST /api/v1/tasks/{task_id}/reject`：拒收。

任务列表支持查询参数：

- `status`：按任务状态筛选，例如 `in_transit`。
- `keyword`：按运单号、样本名、批次模糊查询。
- `page`、`page_size`：分页。
- `updated_after`：只返回该时间之后更新过的任务，适合 Web/小程序轮询增量刷新。

创建、发出、到达、签收和拒收支持请求头 `Idempotency-Key`。同一个按钮请求如果因为网络慢被重复提交，后端会返回第一次成功的结果，不会重复创建或重复状态流转。

### 设备与监测接口

- `GET /api/v1/meta/contracts`：状态和字段约定。
- `GET /api/v1/tasks/TASK-001`：任务详情。
- `GET /api/v1/tasks/TASK-001/telemetry/latest`：最新监测数据。
- `GET /api/v1/tasks/TASK-001/telemetry/history?limit=100`：监测历史。
- `GET /api/v1/tasks/TASK-001/alarms?limit=100`：异常列表。
- `GET /api/v1/tasks/TASK-001/trace-report`：追溯报告。
- `POST /api/v1/device/telemetry`：正式设备遥测上传接口，支持电量和位置字段。
- `POST /api/v1/device/heartbeat`：设备心跳接口，用于表示设备在线。
- `POST /api/v1/devices`：登记设备。
- `GET /api/v1/devices`：设备列表，包含在线状态、电量、最后在线时间和当前绑定任务。
- `POST /api/v1/devices/{device_id}/bind`：绑定设备到运单。
- `POST /api/v1/devices/{device_id}/unbind`：解绑设备。
- `POST /api/v1/devices/{device_id}/rotate-secret`：轮换设备密钥，新密钥只返回一次。
- `GET /api/v1/devices/{device_id}/bindings`：设备绑定历史。

### 告警和交接接口

- `POST /api/v1/alarms/{alarm_id}/ack`：确认告警。
- `POST /api/v1/alarms/{alarm_id}/resolve`：填写处置结果并关闭告警。
- `POST /api/v1/tasks/{task_id}/handoffs`：发起交接会话。
- `GET /api/v1/tasks/{task_id}/handoffs`：分页查询任务交接记录和证据状态。
- `GET /api/v1/handoffs/{handoff_id}`：查询交接会话。
- `POST /api/v1/handoffs/{handoff_id}/confirm`：确认责任转移。
- `POST /api/v1/handoffs/{handoff_id}/reject`：拒绝交接。
- `POST /api/v1/tasks/{task_id}/qr-tokens`：生成交接二维码 Token。
- `POST /api/v1/qr-tokens/verify`：验证二维码 Token，成功后立即消费。
- `POST /api/v1/qr-tokens/{token_id}/revoke`：撤销未使用二维码 Token。
- `POST /api/v1/face/enroll`：录入本人脸模板引用。
- `GET /api/v1/face/profile`：查询本人人脸资料状态。
- `DELETE /api/v1/face/profile`：注销本人人脸资料。
- `POST /api/v1/face/verify`：交接时提交活体/相似度核验结果。
- `POST /api/v1/files`：登记证据文件元数据。
- `POST /api/v1/files/upload`：上传 JPEG、PNG 或 PDF 证据文件，最大 5 MB。
- `GET /api/v1/files/{file_id}`：获取证据文件下载信息。
- `GET /api/v1/files/{file_id}/download`：经过任务权限校验后下载文件。
- `GET /api/v1/tasks/{task_id}/trace-report.pdf`：导出简版 PDF 追溯报告。

### 管理与审计接口

- `GET /api/v1/admin/users`：管理员查看用户列表。
- `PATCH /api/v1/admin/users/{user_id}/status`：管理员启用或停用用户，支持 `active/disabled`。
- `GET /api/v1/admin/tasks`：管理员查看全部任务列表。
- `GET /api/v1/admin/audit-logs`：管理员查看关键操作审计日志。
- `GET /api/v1/admin/face-reviews`：管理员查看人脸人工复核列表。
- `POST /api/v1/admin/face-reviews/{review_id}/approve`：通过人工复核。
- `POST /api/v1/admin/face-reviews/{review_id}/reject`：拒绝人工复核。
- `GET /api/v1/notifications`：当前用户消息列表。
- `POST /api/v1/notifications/{notification_id}/read`：标记消息已读。
- `GET /api/v1/dashboard/summary`：管理员查看任务、设备和告警聚合指标。

旧版 `/api/device/*`、`/api/task/*` 和首页看板均继续保留。

当前 MVP 已实现二维码 Token 生成、验证、一次性消费和撤销，交接确认强制要求指定接收方已经验证二维码；也支持人脸核验占位流程、真实证据文件上传与受控下载、消息通知和简版 PDF 追溯报告。暂不接入真实人脸供应商和复杂 PDF 排版，人脸结果仅作为模拟/人工复核证据。

### 历史数据查询参数

`GET /api/v1/tasks/{task_id}/telemetry/history` 支持：

- `limit`：返回条数，1 到 100。
- `start_time`：开始时间，ISO 8601，例如 `2026-07-22T10:00:00+08:00`。
- `end_time`：结束时间。
- `cursor`：上一页返回的 `next_cursor`，用于继续翻页。
- `downsample`：降采样间隔，例如 `2` 表示每 2 条取 1 条，适合趋势图减少点数。

返回示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "limit": 100,
    "start_time": "2026-07-22T10:00:00+08:00",
    "end_time": null,
    "cursor": null,
    "next_cursor": 12,
    "downsample": 1,
    "items": []
  }
}
```

## MVP 接口示例

注册发货方：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "发货方演示",
    "phone": "13800000001",
    "organization": "高校实验室",
    "role": "sender",
    "password": "password123"
  }'
```

登录并获取 Token：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"phone":"13800000001","password":"password123"}'
```

创建运单时需要把登录返回的 Token 放到 `Authorization`：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/tasks" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 这里替换成登录返回的token" \
  -H "Idempotency-Key: demo-create-task-001" \
  -d '{
    "sample_name": "血液样本批次 A",
    "batch": "B-20260722-01",
    "receiver": "市医院检验科",
    "carrier": "迅达冷链",
    "expected_arrival": "2026-07-23T10:00:00+08:00",
    "device_id": "CLD-001",
    "box_id": "BOX-A12",
    "seal_id": "SEAL-8891",
    "temperature_min": 2.0,
    "temperature_max": 8.0
  }'
```

正式设备遥测上传示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/device/telemetry" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "CLD-001",
    "task_id": "TASK-001",
    "sequence": 1001,
    "captured_at": "2026-07-22T18:50:00+08:00",
    "temperature": 6.2,
    "humidity": 62.5,
    "battery": 86,
    "box_status": "BOX_CLOSED",
    "move_status": "STABLE",
    "light_raw": 120,
    "location": {"lat": 30.123, "lng": 120.456, "accuracy": 20}
  }'
```

如果正式遥测上传包含 `sequence`，后端会按 `device_id + sequence` 去重。重复上传不会重复入库，会返回：

```json
{
  "code": 0,
  "message": "telemetry duplicated",
  "data": {
    "saved": 0,
    "duplicate": true,
    "items": []
  }
}
```

设备遥测和心跳都会更新设备在线状态：

- `status = online`
- `battery`
- `last_seen_at`
- `current_task_id`

设备心跳示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/device/heartbeat" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "CLD-001",
    "task_id": "TASK-001",
    "battery": 83,
    "rssi": -71,
    "network": "4G"
  }'
```

登记并绑定设备：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/devices" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 这里替换成登录返回的token" \
  -d '{
    "device_id": "CLD-001",
    "device_name": "UniKnect 一号箱",
    "model": "UniKnect Kit GEN-1 Pro"
  }'

curl -X POST "http://127.0.0.1:8000/api/v1/devices/CLD-001/bind" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 这里替换成登录返回的token" \
  -d '{"task_id":"TASK-001"}'
```

确认并处置告警：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/alarms/1/ack"

curl -X POST "http://127.0.0.1:8000/api/v1/alarms/1/resolve" \
  -H "Content-Type: application/json" \
  -d '{"resolution":"已确认温度恢复正常，继续运输"}'
```

发起并确认交接：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/tasks/TASK-001/handoffs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 发货方token" \
  -d '{
    "handoff_type": "sender_to_carrier",
    "to_user_id": 2
  }'

curl -X POST "http://127.0.0.1:8000/api/v1/handoffs/HO_xxx/confirm" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 承运方token" \
  -d '{"location":{"lat":30.12,"lng":120.45,"accuracy":20}}'
```

生成和验证二维码 Token：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/tasks/TASK-001/qr-tokens" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 发起方token" \
  -d '{
    "action": "handoff_send",
    "handoff_id": "HO_xxx",
    "ttl_seconds": 60
  }'

curl -X POST "http://127.0.0.1:8000/api/v1/qr-tokens/verify" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 扫码方token" \
  -d '{"token":"qr_xxx"}'
```

注意：二维码 Token 验证成功后会立即消费，第二次验证会返回 HTTP 410。

管理员查看审计日志：

```bash
curl "http://127.0.0.1:8000/api/v1/admin/audit-logs" \
  -H "Authorization: Bearer 管理员token"
```

管理员查看用户和任务：

```bash
curl "http://127.0.0.1:8000/api/v1/admin/users" \
  -H "Authorization: Bearer 管理员token"

curl "http://127.0.0.1:8000/api/v1/admin/tasks" \
  -H "Authorization: Bearer 管理员token"
```

管理员停用用户：

```bash
curl -X PATCH "http://127.0.0.1:8000/api/v1/admin/users/2/status" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 管理员token" \
  -d '{"status":"disabled"}'
```

用户被停用后无法重新登录，已有 Token 也会被撤销。

登记证据文件：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/files" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 登录token" \
  -d '{
    "task_id": "TASK-001",
    "file_name": "precheck-photo.jpg",
    "file_type": "image/jpeg",
    "file_size": 12345,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "usage": "precheck",
    "related_type": "task",
    "related_id": "TASK-001",
    "storage_url": "local://evidence/precheck-photo.jpg"
  }'
```

查询证据文件：

```bash
curl "http://127.0.0.1:8000/api/v1/files/FILE_xxx" \
  -H "Authorization: Bearer 登录token"
```

说明：当前只是登记文件元数据和哈希，暂不保存真实图片二进制。追溯报告 `/api/v1/tasks/{task_id}/trace-report` 会返回：

- `report_version`
- `trace_hash`
- `summary.evidence_count`
- `evidence_files`

导出简版 PDF 追溯报告：

```bash
curl "http://127.0.0.1:8000/api/v1/tasks/TASK-001/trace-report.pdf" \
  -o TASK-001-trace-report.pdf
```

消息通知：

```bash
curl "http://127.0.0.1:8000/api/v1/notifications" \
  -H "Authorization: Bearer 登录token"

curl -X POST "http://127.0.0.1:8000/api/v1/notifications/1/read" \
  -H "Authorization: Bearer 登录token"
```

说明：当设备数据触发开箱、温度异常、碰撞、跌落等异常时，后端会给该运单相关用户生成告警通知。

## 人脸核验占位 MVP

当前人脸功能用于 Web/小程序提前联调流程，不保存人脸特征向量，也不保存原始照片。

录入本人脸模板引用：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/face/enroll" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 登录token" \
  -d '{
    "template_id": "tpl_user_face",
    "consent": true,
    "quality_score": 0.92
  }'
```

提交交接人脸核验结果：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/face/verify" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer 登录token" \
  -d '{
    "handoff_id": "HO_xxx",
    "qr_token": "qr_xxx",
    "capture_file_id": "FILE_xxx",
    "liveness_token": "live_xxx",
    "liveness_passed": true,
    "similarity_score": 0.86,
    "location": {"lat":30.12,"lng":120.45,"accuracy":20}
  }'
```

MVP 规则：

- `liveness_passed = true` 且 `similarity_score >= 0.8`：核验通过。
- 否则进入 `pending_review`，管理员可人工通过或拒绝。

## 测试上传

后端启动后，另开一个终端执行：

```bash
curl -X POST "http://127.0.0.1:8000/api/device/data" \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

成功时会返回 `"ok": true`。

`TASK-001` 固定绑定 `CLD-001`。如果其他设备编号上传到该任务，后端返回 HTTP 409，不保存该条数据；未知旧任务仍按原接口兼容处理。

拒收接口需要提供原因：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/tasks/TASK-001/reject" \
  -H "Content-Type: application/json" \
  -d '{"reason":"温度异常"}'
```

## 运行自动测试

```bash
cd backend
source .venv/bin/activate
python -m pytest test_api.py -v
```

测试使用临时 SQLite 文件，不会清空或覆盖比赛演示数据库。

## 数据与文档

- 数据库：`backend/device_data.db`，首次启动自动创建。
- 详细接口契约：`docs/api/API_CONTRACT.md`。
- 前端 Mock 数据：`docs/api/mock/`。
- MVP 设计：`docs/backend/MVP_BACKEND_DESIGN.md`。

统一接口成功响应格式：

```json
{"code": 0, "message": "success", "data": {}}
```

任务不存在返回 HTTP 404；状态不允许操作返回 HTTP 409。前端应判断 HTTP 状态码和 `code`，不要通过中文提示文字判断业务状态。

## 安全与追溯增强

当前后端已完成以下安全加固：

- 新注册用户密码使用 PBKDF2-SHA256 加盐哈希。
- 旧版 SHA-256 密码哈希账号仍可登录，登录成功后会自动升级为 PBKDF2。
- 登录 Token 只保存 SHA-256 哈希，不在数据库中保存明文 Token。
- 用户被管理员停用后，已有 Token 会被撤销。
- 普通请求不能公开注册管理员；仅本地受控初始化可临时设置 `ALLOW_ADMIN_SELF_REGISTER=true`。
- 正式遥测和心跳只接受已登记、已配置密钥并已绑定任务的设备。
- 所有设备响应使用字段白名单，不返回 `device_secret`、`device_secret_hash` 或其他签名材料。
- 设备只能由其登记人或管理员管理，其他发货方不能覆盖、查询绑定历史或解绑。
- 已签名设备会记录 `X-Nonce`，重复 nonce 会被拒绝，防止重放。

当前后端已增加状态历史：

- 新增 `task_status_history`。
- 创建、预检、发出、到达、签收、拒收、取消和交接确认会写入状态历史。
- `/api/v1/tasks/{task_id}/trace-report` 会返回 `status_history`，用于追溯任务状态变化。

## 设备签名说明

当前为了兼容正在运行的开发板，旧接口 `POST /api/device/data` 不强制设备签名。正式接口中：

- 未登记设备：HTTP 401，业务码 `40125`。
- 已登记但未配置密钥的设备：HTTP 401，业务码 `40126`。
- 缺少或错误签名：HTTP 401。
- 重复 nonce：HTTP 409，业务码 `40940`。
- 设备没有绑定到请求中的任务：HTTP 409，业务码 `40920`。

设备必须通过 `POST /api/v1/devices/{device_id}/bind` 绑定任务。创建或编辑任务时提交裸 `device_id` 会返回 HTTP 422、业务码 `42206`，避免设备表、绑定历史和任务三处关系不一致。解绑会同时把设备恢复为 `available` 并清空任务的 `device_id`。

请求头：

```text
X-Device-Id: CLD-001
X-Timestamp: 2026-07-22T22:01:01+08:00
X-Nonce: nonce-001
X-Signature: hmac_sha256_hex
```

签名规则：

```text
body_hash = sha256(canonical_json_body)
hmac_key = sha256(device_secret)
message = device_id + "." + timestamp + "." + nonce + "." + body_hash
signature = hmac_sha256(hmac_key, message)
```

其中 `canonical_json_body` 使用 JSON 字段按字母排序、无多余空格的形式。`X-Timestamp` 允许 5 分钟时间偏差，`X-Nonce` 只能使用一次。
