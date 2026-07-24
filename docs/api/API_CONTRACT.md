# 正式 `/api/v1` 接口契约

## 约定

- 本地基础地址：`http://127.0.0.1:8000`
- 正式联调时只替换域名，不改变接口路径。
- 字段使用 `snake_case`。
- 时间使用 ISO 8601 字符串，例如 `2026-07-13T10:30:00+08:00`。
- `/api/v1` 成功响应统一包含 `code`、`message`、`data`。查询接口的 `message` 为 `success`，操作接口可以返回对应动作说明。
- 任务不存在：HTTP 404，业务码 `40401`。
- 任务状态不允许操作：HTTP 409，业务码 `40901`。
- FastAPI 参数校验失败：HTTP 422，响应使用框架的 `detail` 列表；拒收原因只有空白时返回 HTTP 400 和统一业务错误结构。
- 除注册、登录、元数据和设备签名上传外，正式接口都需要 `Authorization: Bearer <token>`。
- 无 Token 或 Token 失效返回 HTTP 401；无权资源统一返回 HTTP 404，避免泄漏资源是否存在；允许访问但角色无操作权限时返回 HTTP 403。

## 认证与人员目录

- `POST /api/v1/auth/register`：公开注册 `sender`、`carrier`、`receiver`。默认禁止注册 `admin`，返回 HTTP 403、业务码 `40303`。
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/permissions`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/users?role=carrier|receiver&keyword=&organization=&page=&page_size=`：仅发货方和管理员可查询。响应只包含 `user_id`、姓名、显示名、组织、角色和状态。

管理员通过 `python create_admin.py --phone ... --name ...` 在服务器终端受控初始化，不需要开放公共管理员注册。`ALLOW_ADMIN_SELF_REGISTER=true` 只保留给本地自动测试，远程联调和正式演示不得开启。

登录、二维码验证和人脸占位验证都有每分钟限流，超过限制返回 HTTP 429、业务码 `42901`，并携带 `Retry-After`。

## 任务权限矩阵

| 动作 | 后端允许角色 |
| --- | --- |
| 创建、编辑、预检、取消、指派、发出 | 任务发货方本人或管理员 |
| 到达 | 已分配承运方或管理员 |
| 签收、拒收 | 已分配接收方或管理员 |
| 查询任务、遥测、告警、报告、PDF | 任务发货方、承运方、接收方或管理员 |
| 告警确认、解决 | 管理员 |
| 设备登记、绑定、解绑 | 设备所有者且为任务发货方，或管理员 |

## 状态枚举

任务：`pending_pack`、`pending_handoff`、`in_transit`、`arrived`、`signed`、`rejected`、`canceled`。

箱体：`BOX_OPEN`、`BOX_CLOSED`。

运动：`STABLE`、`MILD`、`SEVERE`、`IMPACT`、`FREE_FALL`。

温度：`TEMP_OK`、`TEMP_ALERT`。

设备：`available`、`bound`、`online`、`offline`。

告警：`new`、`acknowledged`、`resolved`。

交接：`pending`、`confirmed`、`rejected`。

前端应保存英文值，页面显示时再映射为中文。完整枚举也可从 `GET /api/v1/meta/contracts` 实时读取。

`GET /api/v1/meta/contracts` 是 Web 和小程序的唯一枚举来源；页面只负责中文映射，不新增私有状态值。

## 查询接口

### 任务详情

`GET /api/v1/tasks/{task_id}`，需要 Bearer Token。

MVP 使用 `TASK-001`，绑定设备为 `CLD-001`。响应 `data` 包含任务名称、设备编号、发出单位、接收单位、承运人员、状态和交接时间。

### 最新监测

`GET /api/v1/tasks/{task_id}/telemetry/latest`

还没有监测数据时 `data` 为 `null`。

### 监测历史

`GET /api/v1/tasks/{task_id}/telemetry/history?limit=100`

`limit` 范围为 1 到 100，结果按最新到最早排列。

### 异常列表

`GET /api/v1/tasks/{task_id}/alarms?limit=100`

返回开箱、温度异常、晃动、碰撞或跌落等事件，按最新到最早排列。

### 追溯报告

`GET /api/v1/tasks/{task_id}/trace-report`

返回 `task`、`latest`、`summary`、`events` 和 `handoff_nodes`，Web 与小程序应直接使用这一接口生成追溯展示。`handoff_nodes` 根据实际状态包含 `started`、`signed` 或 `rejected` 节点；尚未交接时可以为空数组。

## 操作接口

### 发出交接

`POST /api/v1/tasks/{task_id}/start`

无请求体。正式任务必须已经预检通过并指派承运方、接收方，否则返回 HTTP 409、业务码 `40902`。任务从待发出进入 `in_transit`，重复发出返回 HTTP 409。

请求必须由任务发货方本人或管理员发起。创建、发出、到达、签收和拒收的网络重试应携带稳定的 `Idempotency-Key`。

### 承运到达

`POST /api/v1/tasks/{task_id}/arrive`

无请求体，仅已分配承运方或管理员可操作。

### 到达签收

`POST /api/v1/tasks/{task_id}/sign`

无请求体。只有运输中或已到达任务允许签收。

仅已分配接收方或管理员可操作。

正式任务签收前必须完成 `carrier_to_receiver` 交接，并由接收方验证动态二维码、确认交接；缺少证据返回 HTTP 409、业务码 `40933`。拒收至少要求接收方已验证相同交接会话的二维码。

### 拒收

`POST /api/v1/tasks/{task_id}/reject`

请求体：

```json
{"reason": "温度异常"}
```

只有运输中或已到达任务允许拒收，`reason` 不能为空。

## 设备登记、绑定与正式上传

- `POST /api/v1/devices`：发货方或管理员登记设备。设备编号已属于其他用户时返回 HTTP 409。
- `GET /api/v1/devices`：仅返回当前用户有权查看的设备。
- `POST /api/v1/devices/{device_id}/bind`
- `POST /api/v1/devices/{device_id}/unbind`
- `GET /api/v1/devices/{device_id}/bindings`
- `POST /api/v1/devices/{device_id}/rotate-secret`：设备所有者或管理员轮换密钥；新密钥只在本次响应中返回一次，旧密钥立即失效。
- `POST /api/v1/device/telemetry`
- `POST /api/v1/device/heartbeat`

设备响应采用显式白名单，绝不返回 `device_secret`、`device_secret_hash` 或签名材料。任务与设备的关联只能通过绑定接口修改；解绑会同时清空任务的 `device_id`。

正式遥测和心跳必须满足：

1. 设备已登记并配置有效密钥；
2. 设备已绑定请求中的任务；
3. 请求带 `X-Device-Id`、`X-Timestamp`、`X-Nonce`、`X-Signature`；
4. 时间偏差不超过 5 分钟，nonce 未使用过。

未知设备返回 `40125`，未配网设备返回 `40126`，签名错误返回 `40124`，重复 nonce 返回 `40940`，绑定不一致返回 `40920`。

## 正式接口总览

| 模块 | 方法与路径 | 鉴权/分页要点 |
| --- | --- | --- |
| 元数据 | `GET /api/v1/meta/contracts` | 公开 |
| 认证 | `POST /auth/register`、`POST /auth/login`、`GET /auth/me`、`GET /auth/permissions`、`POST /auth/refresh`、`POST /auth/logout` | 登录限流；除注册、登录外需要 Token |
| 人员目录 | `GET /api/v1/users` | 发货方/管理员；`role` 必填；`page/page_size` |
| 任务 | `GET/POST /api/v1/tasks`、`GET/PATCH /tasks/{id}` | 列表支持状态、关键词、分页和增量时间 |
| 任务流转 | `POST /tasks/{id}/assign|precheck|cancel|start|arrive|sign|reject` | 状态及资源权限由后端校验；关键写操作使用 `Idempotency-Key` |
| 遥测 | `POST /device/telemetry`、`POST /device/heartbeat`、`GET /tasks/{id}/telemetry/latest|history` | 上传使用设备签名；历史支持时间、游标、降采样 |
| 设备 | `GET/POST /devices`、`POST /devices/{id}/bind|unbind|rotate-secret`、`GET /devices/{id}/bindings` | 列表分页；资源所有权校验 |
| 告警 | `GET /tasks/{id}/alarms`、`POST /alarms/{id}/ack|resolve` | 查询任务相关方；处置管理员 |
| 交接 | `POST/GET /tasks/{id}/handoffs`、`GET /handoffs/{id}`、`POST /handoffs/{id}/confirm|reject` | 任务列表接口支持分页；确认必须有已消费 QR |
| 二维码 | `POST /tasks/{id}/qr-tokens`、`POST /qr-tokens/verify`、`POST /qr-tokens/{id}/revoke` | 一次性消费、过期、撤销和限流 |
| 人脸占位 | `POST /face/enroll|verify`、`GET/DELETE /face/profile`、管理员复核接口 | 仅为模拟/人工复核流程，不是真实生物识别 |
| 文件 | `POST /files/upload`、`POST /files`、`GET /files/{id}`、`GET /files/{id}/download` | 真实上传使用 multipart；下载重新校验任务权限 |
| 报告 | `GET /tasks/{id}/trace-report`、`GET /tasks/{id}/trace-report.pdf` | 任务相关方 |
| 管理 | `GET /admin/users|tasks|audit-logs`、用户状态和人脸复核接口 | 仅管理员；任务列表分页 |
| 通知 | `GET /notifications`、`POST /notifications/{id}/read` | 仅本人 |
| 大屏 | `GET /dashboard/summary` | 仅管理员，返回真实聚合指标 |

完整请求模型、字段类型和 Swagger 在线调试以运行中的 `/docs` 与 `/openapi.json` 为准。

## 真实证据文件

`POST /api/v1/files/upload` 使用 `multipart/form-data`：

- `task_id`、`usage` 必填；
- `related_type`、`related_id`、`expected_sha256` 可选；
- `file` 只允许 JPEG、PNG、PDF，最大 5 MB；
- 后端同时校验扩展名、MIME、文件头并计算 SHA-256；
- 相同任务、用途和 SHA-256 的文件重复上传时返回原文件；
- 下载地址不暴露磁盘路径，必须携带仍然有效的 Bearer Token；
- 文件随任务证据保留，当前 MVP 不提供用户侧删除接口。

元数据兼容接口 `POST /api/v1/files` 继续保留，但新页面应使用真实上传接口。

## 主错误码

| HTTP | 业务码 | 含义 |
| --- | --- | --- |
| 401 | `40102` | Token 缺失或失效 |
| 401 | `40120`～`40126` | 设备签名、时间、设备登记或配网失败 |
| 403 | `40301` | 角色无权执行动作 |
| 403 | `40303` | 禁止公开注册管理员 |
| 404 | `40401/40402/40405/40407` | 任务、设备、交接或文件不存在/不可见 |
| 409 | `40901` | 任务状态冲突 |
| 409 | `40902` | 发出前置条件不完整 |
| 409 | `40920/40921` | 设备绑定或所有权冲突 |
| 409 | `40932/40933` | 交接二维码或接收证据不足 |
| 409 | `40940` | 设备 nonce 重放 |
| 409 | `40950` | 文件 SHA-256 不一致 |
| 410 | `41010` | 二维码无效、过期、撤销或已使用 |
| 413 | `41301` | 文件超过 5 MB |
| 415 | `41501/41502` | 文件类型或内容无效 |
| 429 | `42901` | 验证请求过多 |

## 开发板兼容接口

开发板仍向 `POST /api/device/data` 上传，不改 MicroPython 代码。请求字段为：

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

`timestamp` 可选，不传时使用服务器时间。

演示任务 `TASK-001` 绑定设备 `CLD-001`。设备编号不匹配时返回 HTTP 409：

```json
{"ok": false, "error": "device does not match task"}
```

## Mock 文件

- `mock/task-001.json`
- `mock/telemetry-latest.json`
- `mock/alarms.json`
- `mock/trace-report.json`
- `mock/auth-unauthorized.json`
- `mock/task-list-empty.json`
- `mock/task-list-page.json`
- `mock/forbidden.json`
- `mock/state-conflict.json`
- `mock/alarm-resolved.json`
- `mock/handoff-list.json`
- `mock/devices.json`
- `mock/telemetry-history-gnss.json`
- `mock/telemetry-history-no-gnss.json`

Mock 只供前端页面开发；联调后应切换到真实接口。
