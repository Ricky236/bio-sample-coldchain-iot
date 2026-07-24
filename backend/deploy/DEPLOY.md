# 服务器部署说明

## 1. 上传与依赖

```bash
# 示例路径
sudo mkdir -p /opt/coldchain
# 将 backend 目录同步到 /opt/coldchain/backend
cd /opt/coldchain/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 若尚未有 .env
mkdir -p data/uploads
```

## 2. 配置 `.env`

至少确认：

- `SEED_DEMO_TASK=false`
- `ENABLE_LEGACY_DEMO_API=false`
- `ENABLE_DEMO_DASHBOARD=false`
- `ENABLE_API_DOCS=false`
- `ALLOW_LOCAL_SIMULATION=false`
- `CORS_ORIGIN_REGEX` 改为正式域名
- **硬件直传本机（如 8080）**：`ENABLE_LIVE_HARDWARE_PROXY=false`（读本地 SQLite，禁止 HTTP 自拉）
- **本机只做开发、数据在公网机**：`ENABLE_LIVE_HARDWARE_PROXY=true`，且 `HARDWARE_PUBLIC_BASE` 指向硬件落库机

## 3. 创建管理员

```bash
source .venv/bin/activate
python create_admin.py --phone 你的手机号 --name 管理员 --organization 组委会
```

## 4. 启动

本机/Windows 验证：

```powershell
cd backend
.\scripts\start-prod.ps1
```

Linux 生产：

```bash
chmod +x scripts/start-prod.sh
./scripts/start-prod.sh
```

或使用 systemd：

```bash
sudo cp deploy/coldchain-backend.service /etc/systemd/system/
# 按实际路径修改 service 内 WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now coldchain-backend
```

## 5. HTTPS（Nginx）

参考 `deploy/nginx.conf.example`，配置域名与证书后反代到 `127.0.0.1:8000`。

## 6. 健康检查

```bash
curl -s http://127.0.0.1:8000/api/v1/meta/contracts
# 生产环境 /docs 默认关闭，返回 404 属正常
```

## 7. 备份

定期备份：

- `data/device_data.db`
- `data/uploads/`
