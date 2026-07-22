# 微信小程序 MVP

刘佳雨负责的公共底座、简化身份、任务与发出交接模块。工程采用 uni-app、Vue 3、TypeScript 和 Pinia，构建目标为 `mp-weixin`。

## 启动与验证

```bash
npm install
npm run type-check
npm test
npm run build:mp-weixin
```

构建产物位于 `dist/build/mp-weixin/`，可用微信开发者工具导入。正式 AppID 由团队统一配置，不提交个人 AppID、Token 或远程联调地址。

## 环境配置

- `VITE_USE_MOCK`：默认关闭；仅显式设置为 `true` 时使用本机离线 Mock。
- `VITE_API_BASE_URL`：真实 API 根地址，默认仅供后端本机调试的 `http://127.0.0.1:8020`。
- `VITE_DEMO_TASK_IDS`：逗号分隔的演示任务 ID，默认 `TASK-001`。

示例：

```bash
VITE_USE_MOCK=false VITE_API_BASE_URL=http://127.0.0.1:8020 npm run dev:mp-weixin
```

Windows PowerShell 可在当前会话先设置对应环境变量。远程地址由项目负责人统一提供；不要把地址散落写在页面中。

当前后端没有任务列表接口，因此“我的任务”从 `VITE_DEMO_TASK_IDS` 读取允许展示的 ID，再逐项调用任务详情接口。这是集中配置的 MVP 适配，不是写死页面数据。后端提供列表接口后只需替换 `taskService.listDemoTasks`。

交接页备注仅用于提交前核对。当前 `POST /api/v1/tasks/{task_id}/start` 明确要求无请求体，因此备注不会上传；契约新增备注字段前不得擅自发送。

## 周瑞琪页面接入

所有后半段页面继续放在同一个 `miniprogram/` 工程：

1. 在 `src/pages.json` 注册页面，页面参数统一命名为 `task_id`。
2. 从 `@/services/request` 复用 `request`、`ApiError` 和 `errorMessage`，不要创建第二套请求封装。
3. 从 `@/stores/session` 复用会话和角色；角色只是 MVP 界面入口，不代表正式权限认证。
4. 从 `@/components/StatusTag.vue`、`ConfirmDialog.vue`、`StatePanel.vue` 复用状态、确认和页面状态组件。
5. 从 `@/types/api` 扩展契约中已经存在的响应类型，从 `@/utils/status` 复用冻结枚举的中文映射。
6. 后半段接口应集中加入 `src/services/`，Mock 和真实请求保持同一个方法签名。

修改 `pages.json`、`manifest.json`、公共 store 或 request 前先同步 `ljy`，避免盲目覆盖公共文件。
