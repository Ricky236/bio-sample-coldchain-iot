"""通过宝塔面板将可部署后端上传到 47.103.152.175（8080 机）。

凭据仅从环境变量读取：BT_USER / BT_PASS / BT_HOST
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

from playwright.sync_api import sync_playwright

HOST = os.environ.get("BT_HOST", "http://47.103.152.175:8888").rstrip("/")
USER = os.environ.get("BT_USER", "")
PASS = os.environ.get("BT_PASS", "")
REMOTE_DIR = "/opt/coldchain-backend/backend"
OUT = Path(__file__).resolve().parent / "bt-deploy-out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = Path(__file__).resolve().parents[1]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FILES = {
    f"{REMOTE_DIR}/main.py": BACKEND / "main.py",
    f"{REMOTE_DIR}/requirements.txt": BACKEND / "requirements.txt",
    f"{REMOTE_DIR}/create_admin.py": BACKEND / "create_admin.py",
    f"{REMOTE_DIR}/.env": Path(__file__).resolve().parent / "server.env.8080",
}

RESTART_SH = r"""#!/bin/bash
exec > /tmp/coldchain-deploy-restart.log 2>&1
set -euxo pipefail
date
APP=/opt/coldchain-backend/backend
cd "$APP"
mkdir -p uploads data/uploads
if [[ -f device_data.db ]]; then
  cp -a device_data.db "device_data.db.bak-$(date +%Y%m%d%H%M%S)"
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || /www/server/pyporject_evn/agriculture311/bin/python -m venv .venv
fi
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cat >/etc/systemd/system/coldchain-backend.service <<'EOF'
[Unit]
Description=Coldchain FastAPI backend
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/coldchain-backend/backend
EnvironmentFile=-/opt/coldchain-backend/backend/.env
ExecStart=/opt/coldchain-backend/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable coldchain-backend
systemctl restart coldchain-backend
sleep 3
systemctl is-active coldchain-backend
curl -s -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:8000/home || true
curl -s -o /dev/null -w "latest=%{http_code}\n" http://127.0.0.1:8000/api/device/latest || true
curl -s -o /dev/null -w "snap=%{http_code}\n" http://127.0.0.1:8000/api/v1/admin/live-snapshot || true
curl -s -o /dev/null -w "contracts=%{http_code}\n" http://127.0.0.1:8000/api/v1/meta/contracts || true
echo DEPLOY_DONE
date
"""


def login():
    if not USER or not PASS:
        raise SystemExit("set BT_USER and BT_PASS")
    print("==> login baota", HOST)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA, locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)
        page.goto(HOST + "/login", wait_until="domcontentloaded")
        page.wait_for_selector('input[type="password"]')
        page.locator('input[type="text"], input:not([type])').first.fill(USER)
        page.fill('input[type="password"]', PASS)
        page.locator('button:has-text("登录"), button[type="submit"]').first.click()
        page.wait_for_timeout(7000)
        for _ in range(4):
            box = page.locator(
                '.el-message-box button:has-text("确定"), button:has-text("点击忽略")'
            )
            if box.count() == 0:
                break
            try:
                box.first.click(timeout=800)
            except Exception:
                break
            page.wait_for_timeout(400)
        try:
            page.get_by_text("计划任务", exact=True).first.click(timeout=3000)
            page.wait_for_timeout(2500)
        except Exception:
            pass
        cookies = context.cookies()
        token = page.evaluate(
            """() => {
              try {
                return window.vite_public_request_token
                  || window.request_token
                  || localStorage.getItem('request_token')
                  || null
              } catch (e) { return null }
            }"""
        )
        if not token:
            html = page.content()
            m = re.search(r"request_token['\"]?\s*[:=]\s*['\"]([^'\"]+)", html)
            if m:
                token = m.group(1)
        browser.close()
    return cookies, token


def make_opener(cookies):
    cj = CookieJar()
    for ck in cookies:
        cj.set_cookie(
            Cookie(
                0,
                ck["name"],
                ck["value"],
                None,
                False,
                ck.get("domain") or "47.103.152.175",
                True,
                False,
                ck.get("path") or "/",
                True,
                bool(ck.get("secure")),
                None,
                True,
                None,
                None,
                {},
                False,
            )
        )
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def post(opener, token, path, data, timeout=180):
    payload = dict(data)
    if token:
        payload["request_token"] = token
    body = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        HOST + path,
        data=body,
        headers={
            "User-Agent": UA,
            "Referer": HOST + "/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-http-token": token or "",
        },
        method="POST",
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def save_file(opener, token, remote_path: str, text: str):
    post(opener, token, "/files?action=CreateFile", {"path": remote_path})
    code, raw = post(
        opener,
        token,
        "/files?action=SaveFileBody",
        {"path": remote_path, "data": text, "encoding": "utf-8"},
        timeout=300,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    ok = code == 200 and payload.get("status") is not False
    print("save", remote_path, "ok" if ok else "FAIL", raw[:160].replace("\n", " "))
    if not ok:
        raise SystemExit(f"SaveFileBody failed: {remote_path}")


def main():
    cookies, token = login()
    print("cookies", len(cookies), "token", bool(token))
    if not token:
        raise SystemExit("no baota request_token")
    opener = make_opener(cookies)

    for remote, local in FILES.items():
        if not local.exists():
            raise SystemExit(f"missing {local}")
        print("==> upload", local.name, "->", remote, "bytes", local.stat().st_size)
        save_file(opener, token, remote, local.read_text(encoding="utf-8"))

    save_file(opener, token, "/tmp/coldchain-deploy-restart.sh", RESTART_SH)

    code, cron_raw = post(opener, token, "/crontab?action=GetCrontab", {})
    try:
        cron_list = json.loads(cron_raw)
    except json.JSONDecodeError:
        cron_list = []
    if isinstance(cron_list, list):
        for it in cron_list:
            name = str(it.get("name") or "")
            if "coldchain" in name.lower():
                post(opener, token, "/crontab?action=DelCrontab", {"id": str(it["id"])})

    _, add_raw = post(
        opener,
        token,
        "/crontab?action=AddCrontab",
        {
            "name": "coldchain-deploy-once",
            "type": "day",
            "where_hour": "5",
            "where_minute": "20",
            "hour": "5",
            "minute": "20",
            "sType": "toShell",
            "sBody": "bash /tmp/coldchain-deploy-restart.sh",
            "sName": "",
            "backupTo": "",
            "save": "",
            "urladdress": "",
            "save_local": "1",
            "flock": "1",
        },
    )
    add_json = json.loads(add_raw)
    cron_id = str(add_json.get("id") or "")
    print("cron", add_raw[:200])
    if not cron_id:
        raise SystemExit("AddCrontab failed")

    post(opener, token, "/crontab?action=StartTask", {"id": cron_id})

    done = False
    log_text = ""
    for i in range(36):
        time.sleep(5)
        _, log_raw = post(
            opener,
            token,
            "/files?action=GetFileBody",
            {"path": "/tmp/coldchain-deploy-restart.log"},
        )
        try:
            payload = json.loads(log_raw)
            log_text = str(payload.get("data") or log_raw)
        except json.JSONDecodeError:
            log_text = log_raw
        print(f"poll {i}", log_text[-220:].replace("\n", " | "))
        (OUT / "restart-log.txt").write_text(log_text, encoding="utf-8")
        if "DEPLOY_DONE" in log_text:
            done = True
            break

    post(opener, token, "/crontab?action=DelCrontab", {"id": cron_id})
    if not done:
        raise SystemExit("deploy restart did not finish; see bt-deploy-out/restart-log.txt")

    print("==> public probe")
    for path in (
        "/",
        "/home",
        "/api/device/latest",
        "/api/v1/admin/live-snapshot",
        "/api/v1/meta/contracts",
    ):
        url = "http://47.103.152.175:8080" + path
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                print(path, resp.status)
        except urllib.error.HTTPError as e:
            print(path, e.code)
        except Exception as e:
            print(path, "ERR", e)
    print("DEPLOY_OK")


if __name__ == "__main__":
    main()
