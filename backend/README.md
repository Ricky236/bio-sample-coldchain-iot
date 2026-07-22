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

端口不是项目固定配置。如果 8000 被占用，可以换成 8020 等空闲端口，浏览器地址也对应修改。当前负责人电脑的演示服务使用 8020：<http://127.0.0.1:8020/docs>。

默认允许来自本机 `localhost` 和 `127.0.0.1` 任意端口的 Web 开发请求。部署到明确域名时，由负责人设置 `CORS_ORIGIN_REGEX`，例如：

```bash
CORS_ORIGIN_REGEX='^https://example\.com$' uvicorn main:app --host 0.0.0.0 --port 8000
```

## 每次重新启动

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

按 `Control + C` 停止服务。

## 团队接口分工

开发板继续使用现有地址，不需要修改程序：

- `POST /api/device/data`：上传冷链监测数据。

Web 管理端和微信小程序统一使用 `/api/v1`：

- `POST /api/v1/auth/register`：注册账户并返回登录会话。
- `POST /api/v1/auth/login`：手机号与密码登录。
- `GET /api/v1/auth/me`：通过 Bearer Token 获取当前用户。
- `POST /api/v1/auth/logout`：注销当前登录会话。
- `GET /api/v1/meta/contracts`：状态和字段约定。
- `GET /api/v1/tasks/TASK-001`：任务详情。
- `GET /api/v1/tasks/TASK-001/telemetry/latest`：最新监测数据。
- `GET /api/v1/tasks/TASK-001/telemetry/history?limit=100`：监测历史。
- `GET /api/v1/tasks/TASK-001/alarms?limit=100`：异常列表。
- `POST /api/v1/tasks/TASK-001/start`：发出交接。
- `POST /api/v1/tasks/TASK-001/sign`：签收。
- `POST /api/v1/tasks/TASK-001/reject`：拒收。
- `GET /api/v1/tasks/TASK-001/trace-report`：追溯报告。

旧版 `/api/device/*`、`/api/task/*` 和首页看板均继续保留。

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
