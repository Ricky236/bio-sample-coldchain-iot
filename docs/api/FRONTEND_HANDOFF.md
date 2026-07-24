# Web 与小程序正式联调说明

## 当前阶段

当前后端已经支持正式登录、多任务、设备、告警、交接和追溯 MVP。Web 管理端和微信小程序共享同一个 FastAPI 后端、同一份任务数据和同一组状态枚举。`TASK-001` 只保留给旧开发板演示；新账号必须通过“我的任务”接口获取有权访问的任务。

## 文档入口

- 接口契约：`docs/api/API_CONTRACT.md`
- Mock 数据：`docs/api/mock/`
- 本机 Swagger：<http://127.0.0.1:8000/docs>
- 本机契约接口：<http://127.0.0.1:8000/api/v1/meta/contracts>

`127.0.0.1` 只代表运行后端的那台电脑。其他电脑和手机无法通过自己的 `127.0.0.1` 访问赵耀电脑，前端代码禁止把该地址写死。远程联调地址由赵耀统一提供。

## 接口地址配置

Web 建议只在环境配置中定义：

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

微信小程序使用一个统一配置模块或环境变量保存 API 基础地址。任何成员不得在多个页面分别写死后端地址。

后端默认允许本机 `localhost` 和 `127.0.0.1` 不同端口跨域联调。正式部署域名由赵耀通过后端环境变量 `CORS_ORIGIN_REGEX` 统一配置，前端不得通过关闭浏览器安全策略解决跨域问题。

## 统一响应

所有 `/api/v1` 成功响应使用真实字段：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

前端同时判断 HTTP 状态码和 `code`。任务不存在为 HTTP 404、`code=40401`；状态冲突为 HTTP 409、`code=40901`。不要通过解析中文提示判断业务状态。

查询接口的 `message` 通常为 `success`，发出、签收和拒收成功时可以是动作说明。FastAPI 参数校验错误为 HTTP 422，并使用框架的 `detail` 列表。

## 状态枚举

任务状态：`pending_pack`、`pending_handoff`、`in_transit`、`arrived`、`signed`、`rejected`、`canceled`。

箱体状态：`BOX_OPEN`、`BOX_CLOSED`。

运动状态：`STABLE`、`MILD`、`SEVERE`、`IMPACT`、`FREE_FALL`。

温度状态：`TEMP_OK`、`TEMP_ALERT`。

前端角色：`admin`、`sender`、`carrier`、`receiver`。角色用于控制菜单和按钮提示，最终权限始终以后端响应为准。

公开注册只允许 `sender`、`carrier`、`receiver`，不能注册 `admin`。所有任务、设备、遥测、告警和报告查询都要携带 Bearer Token；401 清理会话并回登录页，403 显示无权限，404 不应解释为资源一定不存在，409 显示状态冲突。

## 设备联调规则

- 页面不得接收、保存、显示或打印设备密钥。
- 所有设备响应都不包含 `device_secret` 或 `device_secret_hash`。
- 创建和编辑任务时不要提交 `device_id`；设备关联只调用 `/api/v1/devices/{device_id}/bind`。
- 解绑成功后，设备应为 `available`、`current_task_id=null`，任务详情中的 `device_id` 也应为 `null`。
- 发货方通过 `GET /api/v1/users?role=carrier|receiver` 获取指派候选人，不得调用管理员用户列表。
- 设备配网后通过受控流程调用 `POST /api/v1/devices/{device_id}/rotate-secret`；返回的新密钥只交付硬件，前端不得持久化。

## 交接、证据和文件

- 任务交接记录统一读取 `GET /api/v1/tasks/{task_id}/handoffs?page&page_size`。
- 创建交接后，由发起方生成动态二维码，指定接收人扫码调用 `/qr-tokens/verify`。
- `/handoffs/{handoff_id}/confirm` 在二维码未验证时返回 `40932`，前端不得绕过。
- 正式签收前必须完成 `carrier_to_receiver` 交接；缺少接收证据返回 `40933`。
- 人脸相关页面必须标注“模拟核验/人工复核”，不得展示为真实生物识别。
- 新证据文件调用 `POST /api/v1/files/upload`，使用 multipart；仅允许 JPEG、PNG、PDF，最大 5 MB。
- 下载必须调用后端返回的 `/api/v1/files/{file_id}/download` 并携带 Token；不得读取或拼接本地磁盘路径。

## MVP 接口

### 元数据

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/meta/contracts` | 获取统一枚举和字段规则 | 无 | 以真实接口为准 |

### 任务

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/tasks/{task_id}` | 任务、设备、人员和状态摘要 | `task_id=TASK-001` | `mock/task-001.json` |

### 遥测

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/tasks/{task_id}/telemetry/latest` | 最新监测值 | `task_id` | `mock/telemetry-latest.json` |
| GET | `/api/v1/tasks/{task_id}/telemetry/history` | 最近监测记录 | `task_id`、`limit=1..100` | 参考 `mock/telemetry-latest.json` 的单条结构 |

### 告警

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/tasks/{task_id}/alarms` | 异常时间线 | `task_id`、`limit=1..100` | `mock/alarms.json` |

### 交接、签收和拒收

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/tasks/{task_id}/start` | 发出并进入运输中 | `task_id`，无请求体 | 响应任务结构参考 `mock/task-001.json` |
| POST | `/api/v1/tasks/{task_id}/sign` | 到达签收 | `task_id`，无请求体 | 响应任务结构参考 `mock/task-001.json` |
| POST | `/api/v1/tasks/{task_id}/reject` | 拒收 | `task_id`，JSON `reason` | 响应任务结构参考 `mock/task-001.json` |

### 追溯

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/tasks/{task_id}/trace-report` | 任务、监测摘要、告警和交接节点 | `task_id` | `mock/trace-report.json` |

### 新增正式联调接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/users` | 承运方/接收方候选目录 |
| GET | `/api/v1/tasks/{task_id}/handoffs` | 分页交接记录和证据状态 |
| POST | `/api/v1/files/upload` | 真实证据文件上传 |
| GET | `/api/v1/files/{file_id}/download` | 受控文件下载 |
| GET | `/api/v1/dashboard/summary` | 管理员全局大屏聚合 |
| POST | `/api/v1/devices/{device_id}/rotate-secret` | 受控设备重新配网 |

### 旧版兼容接口

| 方法 | 路径 | 用途 | 关键参数 | Mock |
| --- | --- | --- | --- | --- |
| POST | `/api/device/data` | UniKnect 开发板上传数据 | 见 `API_CONTRACT.md` | 无 |
| GET | `/api/device/latest` | 旧看板最新数据 | 无 | 无 |
| GET | `/api/device/history` | 旧看板历史数据 | 无 | 无 |
| GET | `/api/device/events` | 旧看板异常事件 | 无 | 无 |
| POST | `/api/task/start` | 旧看板发出按钮 | 无 | 无 |
| POST | `/api/task/sign` | 旧看板签收按钮 | 无 | 无 |
| GET | `/api/task/current` | 旧看板当前任务 | 无 | 无 |
| GET | `/api/task/report` | 旧看板追溯摘要 | 无 | 无 |

前端新页面统一使用 `/api/v1`；旧版接口主要用于开发板和现有演示看板兼容。

## 开发顺序

唐凯烜 Web：任务总览 → `TASK-001` 详情 → 实时监控 → 告警时间线 → 发出/签收 → 追溯展示。

刘佳雨小程序 A：公共 request → 简化身份入口 → 我的任务 → 任务详情 → 发出交接。

周瑞琪小程序 B：运输监控 → 异常查看 → 签收/拒收 → 追溯查看 → 回归测试。

## Mock 规范

- Mock 只用于独立开发，联调时必须切换真实 API。
- 页面不得写死最终比赛数据，`task_id` 在本阶段统一使用 `TASK-001`。
- Mock 和真实返回结构必须一致；后端契约调整后需同步更新 Mock。
- 禁止前端自行修改或另造状态枚举。

## 已知限制

- 当前数据库为 SQLite。
- `TASK-001` 和 `CLD-001` 仅用于旧开发板演示链路。
- 本机 `127.0.0.1` 无法被其他电脑或手机直接访问。
- 远程联调地址后续由赵耀统一提供。
- 当前数据库仍为 SQLite，适合竞赛 MVP，不作为生产级高并发方案。
- 登录、二维码和人脸占位验证已有限流；真实第三方人脸服务仍未接入。

## 问题反馈格式

联调问题请一次性提供：

```text
分支：
页面：
请求方法和 URL：
请求参数：
实际响应：
期望响应：
复现步骤：
截图或日志：
```
