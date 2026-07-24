from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# 尽早加载 backend/.env，便于生产用环境变量覆盖默认值
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

from fastapi import Body, FastAPI, File, Form, Header, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

try:
    import qrcode
except ImportError:
    qrcode = None


BASE_DIR = Path(__file__).resolve().parent


def _env_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_data_path(env_name: str, default_relative: str) -> Path:
    raw = os.environ.get(env_name, default_relative)
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


DATABASE_PATH = _resolve_data_path("DATABASE_PATH", "data/device_data.db")
FILE_STORAGE_DIR = _resolve_data_path("FILE_STORAGE_DIR", "data/uploads")
CORS_ORIGIN_REGEX = os.environ.get(
    "CORS_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
)
ALLOW_ADMIN_SELF_REGISTER = _env_flag("ALLOW_ADMIN_SELF_REGISTER", "false")
# 生产默认关闭：无鉴权旧接口、演示看板、OpenAPI 文档
ENABLE_LEGACY_DEMO_API = _env_flag("ENABLE_LEGACY_DEMO_API", "false")
ENABLE_DEMO_DASHBOARD = _env_flag("ENABLE_DEMO_DASHBOARD", "false")
ENABLE_API_DOCS = _env_flag("ENABLE_API_DOCS", "false")
ALLOW_LOCAL_SIMULATION = _env_flag("ALLOW_LOCAL_SIMULATION", "false")
# 仅当公网快照可见该设备时允许自动登记（不再硬编码 CLD-001）
ALLOW_DEVICE_AUTO_REGISTER = _env_flag("ALLOW_DEVICE_AUTO_REGISTER", "true")
# 从公网硬件拉取 live-snapshot / events（仅开发机）；
# 硬件直传落库的服务器必须设 false，改为读本地库，禁止 HTTP 自拉
ENABLE_LIVE_HARDWARE_PROXY = _env_flag("ENABLE_LIVE_HARDWARE_PROXY", "true")
HARDWARE_PUBLIC_BASE = os.environ.get(
    "HARDWARE_PUBLIC_BASE",
    "http://47.103.152.175:8080",
).rstrip("/")
LIVE_SNAPSHOT_URL = os.environ.get(
    "LIVE_SNAPSHOT_URL",
    f"{HARDWARE_PUBLIC_BASE}/api/v1/admin/live-snapshot",
)
LIVE_SNAPSHOT_TIMEOUT = float(os.environ.get("LIVE_SNAPSHOT_TIMEOUT", "15"))
LIVE_SNAPSHOT_CACHE_SECONDS = float(
    os.environ.get("LIVE_SNAPSHOT_CACHE_SECONDS", "3")
)
LIVE_DEVICE_LATEST_URL = os.environ.get(
    "LIVE_DEVICE_LATEST_URL",
    f"{HARDWARE_PUBLIC_BASE}/api/device/latest",
)
LIVE_DEVICE_HISTORY_URL = os.environ.get(
    "LIVE_DEVICE_HISTORY_URL",
    f"{HARDWARE_PUBLIC_BASE}/api/device/history",
)
LIVE_DEVICE_EVENTS_URL = os.environ.get(
    "LIVE_DEVICE_EVENTS_URL",
    f"{HARDWARE_PUBLIC_BASE}/api/device/events",
)
PRECHECK_MAX_DATA_AGE_SECONDS = float(
    os.environ.get("PRECHECK_MAX_DATA_AGE_SECONDS", "120")
)
SEED_DEMO_TASK = _env_flag("SEED_DEMO_TASK", "false")
_live_snapshot_cache: tuple[float, dict] | None = None

DEMO_TASK = {
    "task_id": "TASK-001",
    "device_id": "CLD-001",
    "sample_name": "生物样本转运箱 A",
    "sender": "高校实验室",
    "receiver": "医院检验科",
    "carrier": "演示人员",
}

TASK_STATUSES = [
    "pending_pack",
    "pending_handoff",
    "in_transit",
    "arrived",
    "signed",
    "rejected",
    "canceled",
]
# 传感器上报可归属的在途/装箱态（已签收/取消等不再改写）
ACTIVE_INGEST_TASK_STATUSES = {
    "pending_pack",
    "pending_handoff",
    "in_transit",
    "arrived",
}
TERMINAL_TASK_STATUSES = {"signed", "rejected", "canceled"}
BOX_STATUSES = ["BOX_OPEN", "BOX_CLOSED"]
MOVE_STATUSES = ["STABLE", "MILD", "SEVERE", "IMPACT", "FREE_FALL"]
TEMPERATURE_STATUSES = ["TEMP_OK", "TEMP_ALERT"]
DEVICE_STATUSES = ["available", "bound", "online", "offline"]
ALARM_STATUSES = ["new", "acknowledged", "resolved"]
HANDOFF_STATUSES = ["pending", "confirmed", "rejected"]

TASK_STATUS_LABELS = {
    "pending_pack": "待发出",
    "pending_handoff": "待发出",
    "in_transit": "运输中",
    "arrived": "已到达",
    "signed": "已签收",
    "rejected": "已拒收",
    "canceled": "已取消",
}

EVENT_LEVELS = {
    "SEVERE": "high",
    "IMPACT": "high",
    "FREE_FALL": "high",
    "TEMP_ALERT": "medium",
    "BOX_OPEN": "medium",
    "MILD": "low",
    "LOW_BATTERY": "medium",
    "DEVICE_OFFLINE": "high",
}

EVENT_DISPLAY_LABELS = {
    "NORMAL": "正常",
    "BOX_OPEN": "开箱事件",
    "MILD": "轻微晃动",
    "SEVERE": "剧烈晃动",
    "IMPACT": "疑似碰撞",
    "FREE_FALL": "疑似跌落",
    "TEMP_ALERT": "温度异常",
    "LOW_BATTERY": "低电量",
    "DEVICE_OFFLINE": "设备离线",
}

LOW_BATTERY_THRESHOLD = 20
DEVICE_OFFLINE_SECONDS = 300
DEVICE_OFFLINE_SCAN_SECONDS = int(os.environ.get("DEVICE_OFFLINE_SCAN_SECONDS", "60"))
# 光敏箱体判定（暗处 raw 高、亮处 raw 低）。
# 公网当前实测关闭约 light_raw≈6400，故将关箱阈值下调，避免仍误判为开箱。
# 可用环境变量覆盖：LIGHT_BOX_STATUS_OVERRIDE / LIGHT_BOX_OPEN_THRESHOLD / LIGHT_BOX_CLOSE_THRESHOLD
LOGIN_RATE_LIMIT = 6
VERIFY_RATE_LIMIT = 10
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_EVIDENCE_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EVIDENCE_TYPES = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "application/pdf": (".pdf",),
}

AUTH_ROLES = ["admin", "sender", "carrier", "receiver"]
ROLE_PERMISSIONS = {
    "admin": [
        "view_task",
        "start_task",
        "sign_task",
        "reject_task",
        "view_report",
        "view_alarm",
        "upload_location",
        "manage_user",
        "manage_device",
        "manage_alarm",
    ],
    "sender": ["view_task", "start_task", "view_report", "manage_device"],
    "carrier": ["view_task", "view_alarm", "upload_location"],
    "receiver": ["view_task", "sign_task", "reject_task", "view_report"],
}


class DeviceDataIn(BaseModel):
    device_id: str = Field(..., examples=["CLD-001"])
    task_id: str = Field(..., examples=["TASK-001"])
    temperature: float
    humidity: float
    light_raw: int
    box_status: str
    move_status: str
    temp_status: str
    acc_total: float
    motion_score: float
    timestamp: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy: Optional[float] = None


class LocationIn(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy: Optional[float] = None


class DeviceTelemetryIn(BaseModel):
    device_id: str = Field(..., examples=["CLD-001"])
    task_id: str = Field(..., examples=["TASK-001"])
    sequence: Optional[int] = None
    captured_at: Optional[str] = None
    temperature: float
    humidity: float
    battery: Optional[int] = None
    box_status: str
    move_status: str
    temp_status: Optional[str] = None
    light_raw: int
    acc_total: Optional[float] = None
    motion_score: Optional[float] = None
    location: Optional[LocationIn] = None


class DeviceHeartbeatIn(BaseModel):
    device_id: str = Field(..., examples=["CLD-001"])
    task_id: Optional[str] = Field(None, examples=["TASK-001"])
    battery: Optional[int] = None
    rssi: Optional[int] = None
    network: Optional[str] = Field(None, max_length=40)
    timestamp: Optional[str] = None


class RegisterDeviceIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=80)
    device_name: Optional[str] = Field(None, max_length=120)
    model: Optional[str] = Field(None, max_length=120)
    device_secret: Optional[str] = Field(None, min_length=6, max_length=120)


class BindDeviceIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)


class DeviceBindCheckIn(BaseModel):
    box_id: str = Field(..., min_length=1, max_length=80)
    seal_id: str = Field(..., min_length=1, max_length=80)


class LocalDeviceReadingIn(BaseModel):
    temperature: float
    humidity: float = 60
    task_id: Optional[str] = Field(None, max_length=80)


class ResolveAlarmIn(BaseModel):
    resolution: str = Field(..., min_length=1, max_length=300)


class CreateHandoffIn(BaseModel):
    handoff_type: str = Field(..., examples=["sender_to_carrier"])
    to_user_id: int


class HandoffConfirmIn(BaseModel):
    location: Optional[LocationIn] = None


class HandoffRejectIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=200)


class CreateQrTokenIn(BaseModel):
    action: str = Field(..., max_length=60)
    handoff_id: str = Field(..., max_length=80)
    ttl_seconds: int = Field(default=60, ge=10, le=300)


class VerifyQrTokenIn(BaseModel):
    token: str = Field(..., min_length=1, max_length=160)


class UpdateUserStatusIn(BaseModel):
    status: str = Field(..., examples=["active"])


class FaceEnrollIn(BaseModel):
    template_id: str = Field(..., min_length=1, max_length=160)
    consent: bool
    quality_score: Optional[float] = Field(None, ge=0, le=1)


class FaceVerifyIn(BaseModel):
    handoff_id: str = Field(..., min_length=1, max_length=80)
    qr_token: Optional[str] = Field(None, max_length=160)
    capture_file_id: Optional[str] = Field(None, max_length=80)
    liveness_token: Optional[str] = Field(None, max_length=160)
    liveness_passed: bool = False
    similarity_score: float = Field(..., ge=0, le=1)
    location: Optional[LocationIn] = None


class FaceSimulateIn(BaseModel):
    handoff_id: str = Field(..., min_length=1, max_length=80)


class CreateFileIn(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=80)
    file_name: str = Field(..., min_length=1, max_length=200)
    file_type: str = Field(..., min_length=1, max_length=120)
    file_size: int = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64)
    usage: str = Field(..., max_length=80)
    related_type: Optional[str] = Field(None, max_length=80)
    related_id: Optional[str] = Field(None, max_length=120)
    storage_url: Optional[str] = Field(None, max_length=300)


class RejectTaskIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=200)


class RegisterIn(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=40)
    phone: Optional[str] = Field(None, min_length=3, max_length=40)
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    organization: Optional[str] = Field(None, max_length=120)
    password: str = Field(..., min_length=6, max_length=80)
    role: str = Field(..., examples=["receiver"])
    display_name: Optional[str] = Field(None, max_length=80)


class LoginIn(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=40)
    phone: Optional[str] = Field(None, min_length=1, max_length=40)
    password: str = Field(..., min_length=1, max_length=80)


class CreateTaskIn(BaseModel):
    sample_name: str = Field(..., min_length=1, max_length=120)
    batch: Optional[str] = Field(None, max_length=80)
    receiver: Optional[str] = Field(None, max_length=120)
    carrier: Optional[str] = Field(None, max_length=120)
    expected_arrival: Optional[str] = Field(None, max_length=40)
    device_id: Optional[str] = Field(None, max_length=80)
    box_id: Optional[str] = Field(None, max_length=80)
    seal_id: Optional[str] = Field(None, max_length=80)
    temperature_min: Optional[float] = None
    temperature_max: Optional[float] = None


class UpdateTaskIn(BaseModel):
    sample_name: Optional[str] = Field(None, min_length=1, max_length=120)
    batch: Optional[str] = Field(None, max_length=80)
    receiver: Optional[str] = Field(None, max_length=120)
    carrier: Optional[str] = Field(None, max_length=120)
    expected_arrival: Optional[str] = Field(None, max_length=40)
    device_id: Optional[str] = Field(None, max_length=80)
    box_id: Optional[str] = Field(None, max_length=80)
    seal_id: Optional[str] = Field(None, max_length=80)
    temperature_min: Optional[float] = None
    temperature_max: Optional[float] = None


class AssignTaskIn(BaseModel):
    carrier_user_id: Optional[int] = None
    receiver_user_id: Optional[int] = None


class PrecheckTaskIn(BaseModel):
    passed: bool
    temperature: Optional[float] = None
    seal_ok: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=300)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_query_time(value: Optional[str]) -> Optional[str]:
    return value.replace(" ", "+") if value else value


REQUEST_CONNECTIONS: ContextVar[Optional[list[sqlite3.Connection]]] = ContextVar(
    "request_connections",
    default=None,
)


class ClosingConnection(sqlite3.Connection):
    """SQLite 上下文退出时提交/回滚并真正释放 Windows 文件句柄。"""

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        # 请求内的旧接口有少量逻辑在 with 块后继续读取同一连接，
        # 由 HTTP 中间件统一关闭；脚本/测试直接调用时则在此立即关闭。
        if REQUEST_CONNECTIONS.get() is None:
            self.close()
        return result


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DATABASE_PATH,
        check_same_thread=False,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    request_connections = REQUEST_CONNECTIONS.get()
    if request_connections is not None:
        request_connections.append(conn)
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def build_local_hardware_snapshot(
    telemetry_limit: int = 20,
    alarm_limit: int = 50,
    device_history_limit: int = 50,
) -> dict:
    """从本机 SQLite 组装硬件快照（传感器已直传落库时用，禁止 HTTP 自拉）。"""
    init_db()
    with get_connection() as conn:
        task_rows = conn.execute(
            "SELECT * FROM task_handoff ORDER BY updated_at DESC"
        ).fetchall()
        tasks = [enrich_task(row, conn) for row in task_rows]

        device_rows = conn.execute(
            "SELECT * FROM devices ORDER BY updated_at DESC"
        ).fetchall()
        devices = [serialize_device(row) for row in device_rows]

        latest_telemetry_by_task: dict[str, dict] = {}
        telemetry_history_by_task: dict[str, list[dict]] = {}
        for task in tasks:
            task_id = task["task_id"]
            telem_rows = conn.execute(
                """
                SELECT * FROM device_data
                WHERE task_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (task_id, telemetry_limit),
            ).fetchall()
            if telem_rows:
                history = [normalize_telemetry(row) for row in telem_rows]
                telemetry_history_by_task[task_id] = history
                latest_telemetry_by_task[task_id] = history[0]

        # 无任务绑定的设备流也按 device 最新点补齐，便于按 device_id 回退
        if not latest_telemetry_by_task:
            latest_row = conn.execute(
                """
                SELECT * FROM device_data
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if latest_row:
                item = normalize_telemetry(latest_row)
                key = str(item.get("task_id") or "TASK-UNKNOWN")
                latest_telemetry_by_task[key] = item
                telemetry_history_by_task[key] = [item]

        # 即便已有按任务聚合，也按 device_id 补一条最新点，供预检/交接按设备匹配
        device_latest_rows = conn.execute(
            """
            SELECT d.*
            FROM device_data d
            INNER JOIN (
                SELECT device_id, MAX(id) AS max_id
                FROM device_data
                WHERE device_id IS NOT NULL AND TRIM(device_id) != ''
                GROUP BY device_id
            ) latest ON d.id = latest.max_id
            """
        ).fetchall()
        for row in device_latest_rows:
            item = normalize_telemetry(row)
            device_id = str(item.get("device_id") or "")
            if not device_id:
                continue
            already = any(
                str(v.get("device_id") or "") == device_id
                for v in latest_telemetry_by_task.values()
            )
            if already:
                continue
            key = str(item.get("task_id") or f"DEVICE-{device_id}")
            latest_telemetry_by_task[key] = item
            if key not in telemetry_history_by_task:
                hist_rows = conn.execute(
                    """
                    SELECT * FROM device_data
                    WHERE device_id = ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    (device_id, telemetry_limit),
                ).fetchall()
                telemetry_history_by_task[key] = [
                    normalize_telemetry(r) for r in hist_rows
                ]

        alarm_rows = conn.execute(
            """
            SELECT * FROM event_log
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (alarm_limit,),
        ).fetchall()
        recent_alarms = [normalize_event(row) for row in alarm_rows]

        history_rows = conn.execute(
            """
            SELECT * FROM device_data
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (device_history_limit,),
        ).fetchall()
        recent_device_data = [row_to_dict(row) for row in history_rows]

    return {
        "generated_at": now_iso(),
        "database_path": Path(DATABASE_PATH).name,
        "live_snapshot_url": None,
        "live_snapshot_sync": False,
        "source": "local_db",
        "tasks": tasks,
        "devices": devices,
        "latest_telemetry_by_task": latest_telemetry_by_task,
        "telemetry_history_by_task": telemetry_history_by_task,
        "recent_alarms": recent_alarms,
        "recent_device_data": recent_device_data,
        "limits": {
            "telemetry_limit": telemetry_limit,
            "alarm_limit": alarm_limit,
            "device_history_limit": device_history_limit,
        },
    }


def fetch_live_device_history(limit: int = 120) -> list[dict]:
    """拉取设备历史：本机落库直读；开发机才 HTTP 代理公网。"""
    if not ENABLE_LIVE_HARDWARE_PROXY:
        init_db()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM device_data
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [normalize_telemetry(row) for row in rows]
    request = urllib.request.Request(
        LIVE_DEVICE_HISTORY_URL,
        headers={"Accept": "application/json", "User-Agent": "coldchain-backend/1.0"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or []
    else:
        items = []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)][: max(1, limit)]


def fetch_live_hardware_snapshot() -> dict:
    """读取真实硬件数据。

    - 硬件直传本机（ENABLE_LIVE_HARDWARE_PROXY=false）：只读本地 SQLite，绝不 HTTP 自拉。
    - 开发/旁路机（proxy=true）：从 HARDWARE_PUBLIC_BASE 拉取公网快照。
    """
    global _live_snapshot_cache
    monotonic_now = time.monotonic()
    if (
        _live_snapshot_cache
        and monotonic_now - _live_snapshot_cache[0] < LIVE_SNAPSHOT_CACHE_SECONDS
    ):
        return _live_snapshot_cache[1]

    if not ENABLE_LIVE_HARDWARE_PROXY:
        data = build_local_hardware_snapshot(
            telemetry_limit=120,
            alarm_limit=50,
            device_history_limit=120,
        )
        _live_snapshot_cache = (monotonic_now, data)
        return data

    errors: list[str] = []
    latest_data: Optional[dict] = None

    # 1) fast path: device latest + history
    latest_request = urllib.request.Request(
        LIVE_DEVICE_LATEST_URL,
        headers={"Accept": "application/json", "User-Agent": "coldchain-backend/1.0"},
    )
    try:
        with urllib.request.urlopen(latest_request, timeout=5) as response:
            latest = json.loads(response.read().decode("utf-8"))
        if not isinstance(latest, dict) or not latest.get("device_id"):
            raise RuntimeError("device latest returned empty payload")
        task_id = str(latest.get("task_id") or "TASK-UNKNOWN")
        device_id = str(latest.get("device_id") or "")
        history_items = [latest]
        try:
            raw_history = fetch_live_device_history(120)
            matched = [
                item
                for item in raw_history
                if str(item.get("device_id") or "") == device_id
            ]
            if matched:
                history_items = matched
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ) as hist_exc:
            errors.append(f"history: {hist_exc}")
        latest_data = {
            "generated_at": latest.get("timestamp") or latest.get("created_at"),
            "latest_telemetry_by_task": {task_id: latest},
            "telemetry_history_by_task": {task_id: history_items},
            "recent_alarms": [],
            "fallback": "device_latest",
        }
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        errors.append(f"latest: {exc}")

    # 2) optional full snapshot (short timeout) — prefer when history richer
    live_request = urllib.request.Request(
        LIVE_SNAPSHOT_URL,
        headers={"Accept": "application/json", "User-Agent": "coldchain-backend/1.0"},
    )
    try:
        with urllib.request.urlopen(live_request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise RuntimeError("live hardware snapshot returned invalid response")
        data = payload["data"]
        # 若完整快照历史偏少，用 device/history 补齐
        history_by_task = data.get("telemetry_history_by_task") or {}
        max_hist = max((len(v or []) for v in history_by_task.values()), default=0)
        if latest_data and max_hist < 5:
            for key, items in (latest_data.get("telemetry_history_by_task") or {}).items():
                if len(items or []) > len(history_by_task.get(key) or []):
                    history_by_task[key] = items
            data["telemetry_history_by_task"] = history_by_task
            if not data.get("latest_telemetry_by_task"):
                data["latest_telemetry_by_task"] = latest_data.get(
                    "latest_telemetry_by_task"
                )
        _live_snapshot_cache = (monotonic_now, data)
        return data
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        errors.append(f"snapshot: {exc}")

    if latest_data:
        _live_snapshot_cache = (monotonic_now, latest_data)
        return latest_data

    raise RuntimeError(
        "live hardware snapshot unavailable: " + " | ".join(errors)
    )


def qr_image_data_url(payload: str) -> Optional[str]:
    if qrcode is None:
        return None
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                temperature REAL NOT NULL,
                humidity REAL NOT NULL,
                light_raw INTEGER NOT NULL,
                box_status TEXT NOT NULL,
                move_status TEXT NOT NULL,
                temp_status TEXT NOT NULL,
                acc_total REAL NOT NULL,
                motion_score REAL NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_handoff (
                task_id TEXT PRIMARY KEY,
                device_id TEXT,
                sample_name TEXT NOT NULL,
                sender TEXT NOT NULL,
                receiver TEXT NOT NULL,
                carrier TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                signed_at TEXT,
                rejected_at TEXT,
                rejection_reason TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_id INTEGER,
                task_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_name TEXT NOT NULL,
                event_detail TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_heartbeat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                task_id TEXT,
                battery INTEGER,
                rssi INTEGER,
                network TEXT,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                device_name TEXT,
                model TEXT,
                status TEXT NOT NULL,
                current_task_id TEXT,
                battery INTEGER,
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                bound_at TEXT NOT NULL,
                unbound_at TEXT,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handoff_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                handoff_type TEXT NOT NULL,
                from_user_id INTEGER,
                to_user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                location_lat REAL,
                location_lng REAL,
                location_accuracy REAL,
                reason TEXT,
                certificate_no TEXT,
                trace_hash TEXT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                rejected_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                task_id TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qr_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                handoff_id TEXT NOT NULL,
                action TEXT NOT NULL,
                issuer_user_id INTEGER,
                nonce TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                revoked_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                usage TEXT NOT NULL,
                related_type TEXT,
                related_id TEXT,
                storage_url TEXT,
                uploader_user_id INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                category TEXT NOT NULL,
                is_read INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_nonces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(device_id, nonce)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                reason TEXT,
                actor_user_id INTEGER,
                changed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                template_id TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                quality_score REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                handoff_id TEXT NOT NULL,
                qr_token TEXT,
                capture_file_id TEXT,
                liveness_token TEXT,
                liveness_passed INTEGER NOT NULL,
                similarity_score REAL NOT NULL,
                threshold REAL NOT NULL,
                verified INTEGER NOT NULL,
                manual_review_required INTEGER NOT NULL,
                location_lat REAL,
                location_lng REAL,
                location_accuracy REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT NOT NULL,
                reviewer_user_id INTEGER,
                status TEXT NOT NULL,
                decision_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(scope, idempotency_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limits (
                scope TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                window_started_at TEXT NOT NULL,
                request_count INTEGER NOT NULL,
                PRIMARY KEY(scope, identity_hash)
            )
            """
        )
        ensure_column(conn, "device_data", "sequence", "INTEGER")
        ensure_column(conn, "device_data", "battery", "INTEGER")
        ensure_column(conn, "device_data", "lat", "REAL")
        ensure_column(conn, "device_data", "lng", "REAL")
        ensure_column(conn, "device_data", "accuracy", "REAL")
        ensure_column(conn, "event_log", "alarm_status", "TEXT")
        ensure_column(conn, "event_log", "acknowledged_at", "TEXT")
        ensure_column(conn, "event_log", "resolved_at", "TEXT")
        ensure_column(conn, "event_log", "resolution", "TEXT")
        ensure_column(conn, "event_log", "source", "TEXT")
        ensure_column(conn, "event_log", "source_event_id", "INTEGER")
        ensure_column(conn, "event_log", "responsible_user_id", "INTEGER")
        ensure_column(conn, "event_log", "responsible_role", "TEXT")
        ensure_column(conn, "task_handoff", "device_id", "TEXT")
        ensure_column(conn, "task_handoff", "rejected_at", "TEXT")
        ensure_column(conn, "task_handoff", "rejection_reason", "TEXT")
        ensure_column(conn, "task_handoff", "arrived_at", "TEXT")
        ensure_column(conn, "task_handoff", "owner_user_id", "INTEGER")
        ensure_column(conn, "task_handoff", "carrier_user_id", "INTEGER")
        ensure_column(conn, "task_handoff", "receiver_user_id", "INTEGER")
        ensure_column(conn, "task_handoff", "batch", "TEXT")
        ensure_column(conn, "task_handoff", "expected_arrival", "TEXT")
        ensure_column(conn, "task_handoff", "box_id", "TEXT")
        ensure_column(conn, "task_handoff", "seal_id", "TEXT")
        ensure_column(conn, "task_handoff", "temperature_min", "REAL")
        ensure_column(conn, "task_handoff", "temperature_max", "REAL")
        ensure_column(conn, "task_handoff", "precheck_passed", "INTEGER")
        ensure_column(conn, "task_handoff", "precheck_temperature", "REAL")
        ensure_column(conn, "task_handoff", "precheck_seal_ok", "INTEGER")
        ensure_column(conn, "task_handoff", "precheck_note", "TEXT")
        ensure_column(conn, "task_handoff", "prechecked_at", "TEXT")
        ensure_column(conn, "task_handoff", "canceled_at", "TEXT")
        ensure_column(conn, "task_handoff", "cancel_reason", "TEXT")
        ensure_column(conn, "task_handoff", "idempotency_key", "TEXT")
        ensure_column(conn, "users", "phone", "TEXT")
        ensure_column(conn, "users", "name", "TEXT")
        ensure_column(conn, "users", "organization", "TEXT")
        ensure_column(conn, "users", "status", "TEXT")
        ensure_column(conn, "devices", "device_secret_hash", "TEXT")
        ensure_column(conn, "devices", "owner_user_id", "INTEGER")
        ensure_column(conn, "devices", "organization", "TEXT")
        ensure_column(conn, "qr_tokens", "verified_by_user_id", "INTEGER")
        ensure_column(conn, "files", "storage_path", "TEXT")
        ensure_demo_task(conn)
        migrate_task_status_column(conn)
    conn.close()


def ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def migrate_task_status_column(conn: sqlite3.Connection) -> None:
    """将旧版中文状态迁移为规范键，保留历史数据。"""
    for row in conn.execute("SELECT * FROM task_handoff").fetchall():
        task = row_to_dict(row)
        canonical = canonical_task_status(task)
        if row["status"] != canonical:
            conn.execute(
                "UPDATE task_handoff SET status = ? WHERE task_id = ?",
                (canonical, task["task_id"]),
            )


def ensure_demo_task(conn: sqlite3.Connection) -> None:
    """仅在 SEED_DEMO_TASK=true 时写入 TASK-001（单测/旧看板）；正式环境不种演示单。"""
    if not SEED_DEMO_TASK:
        return
    row = conn.execute(
        "SELECT task_id, device_id FROM task_handoff WHERE task_id = ?",
        (DEMO_TASK["task_id"],),
    ).fetchone()
    if row:
        if not row["device_id"]:
            conn.execute(
                """
                UPDATE task_handoff
                SET device_id = ?
                WHERE task_id = ?
                """,
                (DEMO_TASK["device_id"], DEMO_TASK["task_id"]),
            )
        return

    conn.execute(
        """
        INSERT INTO task_handoff (
            task_id, device_id, sample_name, sender, receiver, carrier,
            status, started_at, signed_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEMO_TASK["task_id"],
            DEMO_TASK["device_id"],
            DEMO_TASK["sample_name"],
            DEMO_TASK["sender"],
            DEMO_TASK["receiver"],
            DEMO_TASK["carrier"],
            "pending_pack",
            None,
            None,
            now_iso(),
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    monitor_task = asyncio.create_task(offline_device_monitor())
    try:
        yield
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Cold Chain Traceability Backend",
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)


@app.middleware("http")
async def close_request_database_connections(request, call_next):
    connections: list[sqlite3.Connection] = []
    token = REQUEST_CONNECTIONS.set(connections)
    try:
        return await call_next(request)
    finally:
        REQUEST_CONNECTIONS.reset(token)
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def light_box_open_threshold() -> int:
    return int(os.environ.get("LIGHT_BOX_OPEN_THRESHOLD", "5000"))


def light_box_close_threshold() -> int:
    return int(os.environ.get("LIGHT_BOX_CLOSE_THRESHOLD", "6000"))


def normalize_box_status(value: Optional[str]) -> str:
    box_status = str(value or "BOX_CLOSED").upper()
    return {
        "CLOSED": "BOX_CLOSED",
        "OPEN": "BOX_OPEN",
        "BOX_CLOSED": "BOX_CLOSED",
        "BOX_OPEN": "BOX_OPEN",
    }.get(box_status, box_status)


def resolve_box_status_from_light(
    light_raw: Optional[int | float],
    reported: Optional[str] = None,
) -> str:
    """按光敏重判箱体状态。暗高亮低：raw 越低越亮（开箱），raw 越高越暗（关箱）。"""
    reported_status = normalize_box_status(reported)
    if not _env_flag("LIGHT_BOX_STATUS_OVERRIDE", "true"):
        return reported_status
    if light_raw is None:
        return reported_status
    try:
        value = float(light_raw)
    except (TypeError, ValueError):
        return reported_status
    open_threshold = light_box_open_threshold()
    close_threshold = max(light_box_close_threshold(), open_threshold)
    if value < open_threshold:
        return "BOX_OPEN"
    if value > close_threshold:
        return "BOX_CLOSED"
    return reported_status


def apply_light_box_status(data: DeviceDataIn) -> DeviceDataIn:
    resolved = resolve_box_status_from_light(data.light_raw, data.box_status)
    if normalize_box_status(data.box_status) == resolved:
        return data
    return data.model_copy(update={"box_status": resolved})


def detect_event(data: DeviceDataIn) -> str:
    box_status = data.box_status.upper()
    move_status = data.move_status.upper()
    temp_status = data.temp_status.upper()
    active_events = [
        box_status == "BOX_OPEN",
        temp_status == "TEMP_ALERT",
        move_status in {"IMPACT", "FREE_FALL"},
    ]

    if temp_status == "SEVERE" or move_status == "SEVERE" or sum(active_events) >= 2:
        return "SEVERE"
    if move_status == "FREE_FALL":
        return "FREE_FALL"
    if move_status == "IMPACT":
        return "IMPACT"
    if move_status == "SEVERE":
        return "SEVERE"
    if temp_status == "TEMP_ALERT":
        return "TEMP_ALERT"
    if box_status == "BOX_OPEN":
        return "BOX_OPEN"
    return "NORMAL"


def build_event_items(
    data: DeviceDataIn, event_type: str
) -> list[tuple[str, str, str]]:
    events = []
    box_status = data.box_status.upper()
    move_status = data.move_status.upper()
    temp_status = data.temp_status.upper()

    if box_status == "BOX_OPEN":
        events.append(("BOX_OPEN", "开箱", "光敏检测到箱体疑似打开"))
    if move_status == "MILD":
        events.append(("MILD", "轻微晃动", "三轴加速度检测到轻微晃动"))
    if move_status == "SEVERE":
        events.append(("SEVERE", "剧烈晃动", "三轴加速度检测到剧烈晃动"))
    if move_status == "IMPACT":
        events.append(("IMPACT", "疑似碰撞", "三轴加速度检测到疑似碰撞"))
    if move_status == "FREE_FALL":
        events.append(("FREE_FALL", "疑似跌落", "三轴加速度检测到疑似自由落体"))
    if temp_status == "TEMP_ALERT":
        events.append(("TEMP_ALERT", "温度异常", "温度超出设定阈值"))

    if event_type == "SEVERE" and len(events) >= 2:
        events.append(("SEVERE", "综合严重异常", "同一条数据触发多个异常条件"))

    return events


def insert_event_logs(
    conn: sqlite3.Connection,
    data_id: int,
    data: DeviceDataIn,
    event_type: str,
    timestamp: str,
    created_at: str,
) -> None:
    events = build_event_items(data, event_type)
    for item_type, item_name, item_detail in events:
        conn.execute(
            """
            INSERT INTO event_log (
                data_id, task_id, device_id, event_type, event_name,
                event_detail, timestamp, created_at, alarm_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data_id,
                data.task_id,
                data.device_id,
                item_type,
                item_name,
                item_detail,
                timestamp,
                created_at,
                "new",
            ),
        )
        for user_id in task_user_ids(conn, data.task_id):
            create_notification(
                conn,
                user_id=user_id,
                title="冷链异常告警",
                message=f"{item_name}：{item_detail}",
                category="alarm",
                task_id=data.task_id,
            )


def insert_alarm_event(
    conn: sqlite3.Connection,
    data_id: Optional[int],
    task_id: str,
    device_id: str,
    event_type: str,
    event_name: str,
    event_detail: str,
    timestamp: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO event_log (
            data_id, task_id, device_id, event_type, event_name,
            event_detail, timestamp, created_at, alarm_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data_id,
            task_id,
            device_id,
            event_type,
            event_name,
            event_detail,
            timestamp,
            created_at,
            "new",
        ),
    )
    for user_id in task_user_ids(conn, task_id):
        create_notification(
            conn,
            user_id=user_id,
            title="冷链异常告警",
            message=f"{event_name}：{event_detail}",
            category="alarm",
            task_id=task_id,
        )


def resolve_inbound_task_id(
    conn: sqlite3.Connection,
    device_id: str,
    reported_task_id: str,
) -> str:
    """硬件常写死 TASK-001：有绑定/真实在途运单时改写为当前运单号再落库。

    - 设备已 bind → 一律归属 current_task_id
    - 上报为演示单 TASK-001 且另有非演示在途单占用该设备 → 归属该运单
    - 其它显式 task_id 不改写（避免误吞测试/其它任务数据）
    """
    if not device_id:
        return reported_task_id

    device = conn.execute(
        "SELECT current_task_id FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    bound_id = (device["current_task_id"] if device else None) or None
    if bound_id:
        bound_row = get_task_by_id(conn, bound_id)
        if bound_row:
            status = canonical_task_status(row_to_dict(bound_row))
            if status not in TERMINAL_TASK_STATUSES:
                return bound_id

    if reported_task_id != DEMO_TASK["task_id"]:
        return reported_task_id

    ranked: list[tuple[int, str]] = []
    claim_rows = conn.execute(
        """
        SELECT * FROM task_handoff
        WHERE device_id = ?
        ORDER BY updated_at DESC
        """,
        (device_id,),
    ).fetchall()
    for row in claim_rows:
        task = row_to_dict(row)
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id == DEMO_TASK["task_id"]:
            continue
        status = canonical_task_status(task)
        if status not in ACTIVE_INGEST_TASK_STATUSES:
            continue
        rank = 10
        if status in {"in_transit", "arrived"}:
            rank = 1
        elif status == "pending_handoff":
            rank = 2
        ranked.append((rank, task_id))

    if ranked:
        ranked.sort(key=lambda item: (item[0], item[1]))
        return ranked[0][1]
    return reported_task_id


def save_device_data(
    conn: sqlite3.Connection,
    data: DeviceDataIn,
    sequence: Optional[int] = None,
    battery: Optional[int] = None,
    location: Optional[LocationIn] = None,
) -> tuple[Optional[sqlite3.Row], Optional[JSONResponse]]:
    data = apply_light_box_status(data)
    resolved_task_id = resolve_inbound_task_id(conn, data.device_id, data.task_id)
    if resolved_task_id != data.task_id:
        data = data.model_copy(update={"task_id": resolved_task_id})
    timestamp = data.timestamp or now_iso()
    created_at = now_iso()
    event_type = detect_event(data)

    task = conn.execute(
        "SELECT device_id FROM task_handoff WHERE task_id = ?",
        (data.task_id,),
    ).fetchone()
    if task and task["device_id"] and task["device_id"] != data.device_id:
        return None, JSONResponse(
            status_code=409,
            content={"ok": False, "error": "device does not match task"},
        )

    if location is not None:
        lat_value = location.lat
        lng_value = location.lng
        accuracy_value = location.accuracy
    else:
        lat_value = data.lat
        lng_value = data.lng
        accuracy_value = data.accuracy

    cursor = conn.execute(
        """
        INSERT INTO device_data (
            device_id, task_id, temperature, humidity, light_raw,
            box_status, move_status, temp_status, acc_total, motion_score,
            event_type, timestamp, created_at, sequence, battery, lat, lng, accuracy
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.device_id,
            data.task_id,
            data.temperature,
            data.humidity,
            data.light_raw,
            data.box_status,
            data.move_status,
            data.temp_status,
            data.acc_total,
            data.motion_score,
            event_type,
            timestamp,
            created_at,
            sequence,
            battery,
            lat_value,
            lng_value,
            accuracy_value,
        ),
    )
    item_id = cursor.lastrowid
    insert_event_logs(conn, item_id, data, event_type, timestamp, created_at)
    if battery is not None and battery < LOW_BATTERY_THRESHOLD:
        insert_alarm_event(
            conn,
            item_id,
            data.task_id,
            data.device_id,
            "LOW_BATTERY",
            "低电量",
            f"设备电量低于 {LOW_BATTERY_THRESHOLD}%，当前电量 {battery}%",
            timestamp,
            created_at,
        )
    row = conn.execute(
        "SELECT * FROM device_data WHERE id = ?",
        (item_id,),
    ).fetchone()
    return row, None


def update_device_seen(
    conn: sqlite3.Connection,
    device_id: str,
    task_id: Optional[str],
    battery: Optional[int],
    seen_at: str,
) -> None:
    existing = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE devices
            SET status = ?, current_task_id = COALESCE(current_task_id, ?),
                battery = COALESCE(?, battery), last_seen_at = ?, updated_at = ?
            WHERE device_id = ?
            """,
            ("online", task_id, battery, seen_at, seen_at, device_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO devices (
                device_id, status, current_task_id, battery,
                last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (device_id, "online", task_id, battery, seen_at, seen_at, seen_at),
        )


def verify_device_signature(
    conn: sqlite3.Connection,
    payload: BaseModel,
    payload_device_id: str,
    x_device_id: Optional[str],
    x_timestamp: Optional[str],
    x_nonce: Optional[str],
    x_signature: Optional[str],
) -> Optional[JSONResponse]:
    device = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (payload_device_id,),
    ).fetchone()
    if not device:
        return api_error(401, 40125, "unknown device")
    if not device["device_secret_hash"]:
        return api_error(401, 40126, "device is not provisioned")
    if not all([x_device_id, x_timestamp, x_nonce, x_signature]):
        return api_error(401, 40120, "device signature required")
    if x_device_id != payload_device_id:
        return api_error(401, 40121, "device id mismatch")
    try:
        signed_at = datetime.fromisoformat(x_timestamp)
    except ValueError:
        return api_error(401, 40122, "invalid device timestamp")
    now = datetime.now(timezone.utc).astimezone()
    if abs((now - signed_at).total_seconds()) > 300:
        return api_error(401, 40123, "device timestamp expired")

    nonce_row = conn.execute(
        "SELECT id FROM device_nonces WHERE device_id = ? AND nonce = ?",
        (payload_device_id, x_nonce),
    ).fetchone()
    if nonce_row:
        return api_error(409, 40940, "device nonce replay")

    # MVP 签名验证使用登记时的明文密钥不可恢复，因此存储的是密钥摘要；
    # 这里用客户端约定：签名密钥明文的 SHA-256 摘要作为 HMAC key。
    expected = device_signature(
        device["device_secret_hash"],
        payload_device_id,
        x_timestamp,
        x_nonce,
        payload,
    )
    if not hmac.compare_digest(expected, x_signature):
        return api_error(401, 40124, "invalid device signature")
    conn.execute(
        """
        INSERT INTO device_nonces (device_id, nonce, timestamp, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (payload_device_id, x_nonce, x_timestamp, now_iso()),
    )
    return None


def telemetry_temp_status(
    payload: DeviceTelemetryIn, task: Optional[sqlite3.Row]
) -> str:
    if payload.temp_status:
        return payload.temp_status
    if task:
        task_data = row_to_dict(task)
        minimum = task_data.get("temperature_min")
        maximum = task_data.get("temperature_max")
        if minimum is not None and payload.temperature < minimum:
            return "TEMP_ALERT"
        if maximum is not None and payload.temperature > maximum:
            return "TEMP_ALERT"
    return "TEMP_OK"


def get_task_row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM task_handoff WHERE task_id = ?",
        (DEMO_TASK["task_id"],),
    ).fetchone()


def get_task_by_id(
    conn: sqlite3.Connection,
    task_id: str,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM task_handoff WHERE task_id = ?",
        (task_id,),
    ).fetchone()


def api_success(data=None, message: str = "success") -> dict:
    return {"code": 0, "message": message, "data": data}


def api_error(status_code: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": None},
    )


def check_rate_limit(
    conn: sqlite3.Connection,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
) -> Optional[JSONResponse]:
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).astimezone()
    row = conn.execute(
        """
        SELECT * FROM rate_limits
        WHERE scope = ? AND identity_hash = ?
        """,
        (scope, identity_hash),
    ).fetchone()
    if row:
        window_started = datetime.fromisoformat(row["window_started_at"])
        if (now - window_started).total_seconds() < window_seconds:
            if row["request_count"] >= limit:
                response = api_error(429, 42901, "too many requests")
                response.headers["Retry-After"] = str(
                    max(1, window_seconds - int((now - window_started).total_seconds()))
                )
                return response
            conn.execute(
                """
                UPDATE rate_limits
                SET request_count = request_count + 1
                WHERE scope = ? AND identity_hash = ?
                """,
                (scope, identity_hash),
            )
            return None
    conn.execute(
        """
        INSERT INTO rate_limits (
            scope, identity_hash, window_started_at, request_count
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(scope, identity_hash) DO UPDATE SET
            window_started_at = excluded.window_started_at,
            request_count = excluded.request_count
        """,
        (scope, identity_hash, now.isoformat(timespec="seconds"), 1),
    )
    return None


def get_idempotent_result(
    conn: sqlite3.Connection,
    scope: str,
    idempotency_key: Optional[str],
) -> Optional[dict]:
    if not idempotency_key:
        return None
    row = conn.execute(
        """
        SELECT response_json FROM idempotency_records
        WHERE scope = ? AND idempotency_key = ?
        """,
        (scope, idempotency_key),
    ).fetchone()
    return json.loads(row["response_json"]) if row else None


def save_idempotent_result(
    conn: sqlite3.Connection,
    scope: str,
    idempotency_key: Optional[str],
    data: dict,
    message: str,
) -> None:
    if not idempotency_key:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO idempotency_records (
            scope, idempotency_key, response_json, created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            scope,
            idempotency_key,
            json.dumps({"data": data, "message": message}, ensure_ascii=False),
            now_iso(),
        ),
    )


def get_user_row(conn: sqlite3.Connection, user_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


DEVICE_RESPONSE_FIELDS = {
    "device_id",
    "device_name",
    "model",
    "status",
    "current_task_id",
    "battery",
    "last_seen_at",
    "created_at",
    "updated_at",
    "owner_user_id",
    "organization",
}


def serialize_device(row: sqlite3.Row) -> dict:
    raw = row_to_dict(row)
    item = {key: raw.get(key) for key in DEVICE_RESPONSE_FIELDS}
    last_seen_at = item.get("last_seen_at")
    if last_seen_at and item.get("status") in {"online", "bound"}:
        try:
            last_seen = datetime.fromisoformat(last_seen_at)
            now = datetime.now(timezone.utc).astimezone()
            if (now - last_seen).total_seconds() > DEVICE_OFFLINE_SECONDS:
                item["status"] = "offline"
        except ValueError:
            pass
    return item


def can_manage_device(user: dict, device: dict) -> bool:
    if user["role"] == "admin":
        return True
    return str(device.get("owner_user_id") or "") == str(user.get("user_id") or "")


def device_seen_in_live_snapshot(device_id: str) -> bool:
    try:
        snapshot = fetch_live_hardware_snapshot()
    except RuntimeError:
        return False
    return any(
        str(item.get("device_id") or "") == str(device_id)
        for item in (snapshot.get("latest_telemetry_by_task") or {}).values()
    )


def ensure_device_for_bind(
    conn: sqlite3.Connection,
    device_id: str,
    user: dict,
) -> Optional[sqlite3.Row]:
    """绑定前确保本地 devices 有记录。

    仅当 ALLOW_DEVICE_AUTO_REGISTER 开启且公网快照可见该设备时自动登记，
    不再硬编码演示设备号。
    """
    device = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if device:
        return device

    if not (ALLOW_DEVICE_AUTO_REGISTER and device_seen_in_live_snapshot(device_id)):
        return None

    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO devices (
            device_id, device_name, model, status, current_task_id,
            battery, last_seen_at, created_at, updated_at, device_secret_hash,
            owner_user_id, organization
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            f"冷链终端 {device_id}",
            "UniKnect",
            "available",
            None,
            None,
            timestamp,
            timestamp,
            timestamp,
            None,
            user["user_id"],
            user.get("organization"),
        ),
    )
    record_audit(
        conn,
        "device.auto_register",
        user_id=user["user_id"],
        resource_type="device",
        resource_id=device_id,
        detail="auto-register from live snapshot",
    )
    return conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()


def can_view_device(conn: sqlite3.Connection, user: dict, device: dict) -> bool:
    if can_manage_device(user, device):
        return True
    task_id = device.get("current_task_id")
    if not task_id:
        return False
    task = get_task_by_id(conn, task_id)
    return bool(task and can_view_task(user, serialize_task(task)))


def ensure_task_offline_alarm(conn: sqlite3.Connection, task: sqlite3.Row) -> None:
    task_data = serialize_task(task)
    device_id = task_data.get("device_id")
    if not device_id:
        return
    device = conn.execute(
        "SELECT * FROM devices WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    if not device:
        return
    device_data = serialize_device(device)
    if device_data.get("status") != "offline":
        return
    existing = conn.execute(
        """
        SELECT id FROM event_log
        WHERE task_id = ? AND device_id = ? AND event_type = ?
          AND COALESCE(alarm_status, 'new') != ?
        LIMIT 1
        """,
        (task_data["task_id"], device_id, "DEVICE_OFFLINE", "resolved"),
    ).fetchone()
    if existing:
        return
    timestamp = now_iso()
    insert_alarm_event(
        conn,
        None,
        task_data["task_id"],
        device_id,
        "DEVICE_OFFLINE",
        "设备离线",
        f"设备超过 {DEVICE_OFFLINE_SECONDS // 60} 分钟未上传心跳或遥测",
        timestamp,
        timestamp,
    )


def scan_offline_devices() -> int:
    created = 0
    connection = get_connection()
    try:
        with connection as conn:
            tasks = conn.execute(
                """
                SELECT task_handoff.*
                FROM task_handoff
                JOIN devices ON devices.current_task_id = task_handoff.task_id
                WHERE devices.last_seen_at IS NOT NULL
                """
            ).fetchall()
            for task in tasks:
                device = conn.execute(
                    "SELECT * FROM devices WHERE current_task_id = ?",
                    (task["task_id"],),
                ).fetchone()
                if device and serialize_device(device)["status"] == "offline":
                    conn.execute(
                        """
                        UPDATE devices
                        SET status = ?, updated_at = ?
                        WHERE device_id = ?
                        """,
                        ("offline", now_iso(), device["device_id"]),
                    )
                before = conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM event_log
                    WHERE task_id = ? AND event_type = ?
                      AND COALESCE(alarm_status, 'new') != ?
                    """,
                    (task["task_id"], "DEVICE_OFFLINE", "resolved"),
                ).fetchone()["count"]
                ensure_task_offline_alarm(conn, task)
                after = conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM event_log
                    WHERE task_id = ? AND event_type = ?
                      AND COALESCE(alarm_status, 'new') != ?
                    """,
                    (task["task_id"], "DEVICE_OFFLINE", "resolved"),
                ).fetchone()["count"]
                created += max(0, after - before)
        return created
    finally:
        connection.close()


async def offline_device_monitor() -> None:
    while True:
        await asyncio.sleep(DEVICE_OFFLINE_SCAN_SECONDS)
        await asyncio.to_thread(scan_offline_devices)


def serialize_binding(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def serialize_handoff(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["handoff_db_id"] = item["id"]
    return item


def minimal_user_display(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if not row:
        return None
    return {
        "user_id": row["id"],
        "name": row["name"] or row["display_name"] or row["username"],
        "organization": row["organization"] or "",
        "role": row["role"],
    }


def serialize_handoff_detail(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = serialize_handoff(row)
    item["from_user"] = minimal_user_display(
        get_user_row(conn, item["from_user_id"]) if item.get("from_user_id") else None
    )
    item["to_user"] = minimal_user_display(get_user_row(conn, item["to_user_id"]))
    qr = conn.execute(
        """
        SELECT id, status, consumed_at, verified_by_user_id
        FROM qr_tokens
        WHERE handoff_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (item["handoff_id"],),
    ).fetchone()
    face = conn.execute(
        """
        SELECT verification_id, status, verified, manual_review_required
        FROM face_verifications
        WHERE handoff_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (item["handoff_id"],),
    ).fetchone()
    evidence_count = conn.execute(
        """
        SELECT COUNT(*) AS count FROM files
        WHERE related_type = ? AND related_id = ?
        """,
        ("handoff", item["handoff_id"]),
    ).fetchone()["count"]
    item["evidence"] = {
        "qr_verified": bool(qr and qr["status"] == "consumed" and qr["consumed_at"]),
        "qr_verified_by_user_id": qr["verified_by_user_id"] if qr else None,
        "face_status": face["status"] if face else "not_submitted",
        "face_verified": bool(face and face["verified"]),
        "face_manual_review_required": bool(face and face["manual_review_required"]),
        "file_count": evidence_count,
    }
    return item


def serialize_audit_log(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def serialize_qr_token(row: sqlite3.Row) -> dict:
    raw = row_to_dict(row)
    item = {
        key: raw.get(key)
        for key in {
            "id",
            "task_id",
            "handoff_id",
            "action",
            "issuer_user_id",
            "verified_by_user_id",
            "status",
            "expires_at",
            "consumed_at",
            "revoked_at",
            "created_at",
        }
    }
    item["token_id"] = item["id"]
    return item


def serialize_file(row: sqlite3.Row) -> dict:
    raw = row_to_dict(row)
    return {
        key: raw.get(key)
        for key in {
            "id",
            "file_id",
            "task_id",
            "file_name",
            "file_type",
            "file_size",
            "sha256",
            "usage",
            "related_type",
            "related_id",
            "storage_url",
            "uploader_user_id",
            "created_at",
        }
    }


def valid_file_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")
    return False


def serialize_notification(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["is_read"] = bool(item["is_read"])
    return item


def serialize_face_profile(row: Optional[sqlite3.Row]) -> dict:
    if not row:
        return {"has_profile": False}
    item = row_to_dict(row)
    item["has_profile"] = item["status"] == "active"
    return item


def serialize_face_verification(row: sqlite3.Row) -> dict:
    raw = row_to_dict(row)
    item = {
        key: raw.get(key)
        for key in {
            "id",
            "verification_id",
            "user_id",
            "handoff_id",
            "capture_file_id",
            "liveness_passed",
            "similarity_score",
            "threshold",
            "verified",
            "manual_review_required",
            "location_lat",
            "location_lng",
            "location_accuracy",
            "status",
            "created_at",
        }
    }
    item["liveness_passed"] = bool(item["liveness_passed"])
    item["verified"] = bool(item["verified"])
    item["manual_review_required"] = bool(item["manual_review_required"])
    return item


def serialize_face_review(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def serialize_status_history(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def record_audit(
    conn: sqlite3.Connection,
    action: str,
    user_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    task_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (
            user_id, action, resource_type, resource_id, task_id, detail, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, action, resource_type, resource_id, task_id, detail, now_iso()),
    )


def task_user_ids(conn: sqlite3.Connection, task_id: str) -> list[int]:
    row = get_task_by_id(conn, task_id)
    if not row:
        return []
    task = row_to_dict(row)
    ids = [
        task.get("owner_user_id"),
        task.get("carrier_user_id"),
        task.get("receiver_user_id"),
    ]
    return sorted({int(user_id) for user_id in ids if user_id})


def create_notification(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    message: str,
    category: str,
    task_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO notifications (
            user_id, task_id, title, message, category,
            is_read, created_at, read_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, task_id, title, message, category, 0, now_iso(), None),
    )


def record_status_history(
    conn: sqlite3.Connection,
    task_id: str,
    from_status: Optional[str],
    to_status: str,
    reason: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    changed_at: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_status_history (
            task_id, from_status, to_status, reason, actor_user_id, changed_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            from_status,
            to_status,
            reason,
            actor_user_id,
            changed_at or now_iso(),
        ),
    )


def generate_handoff_id() -> str:
    return f"HO-{secrets.token_hex(6).upper()}"


def generate_qr_token() -> str:
    return f"qr_{secrets.token_urlsafe(24)}"


def generate_file_id() -> str:
    return f"FILE-{secrets.token_hex(6).upper()}"


def generate_verification_id() -> str:
    return f"FV-{secrets.token_hex(6).upper()}"


def hash_token_value(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_device_secret(secret: str) -> str:
    return hash_token_value(secret)


def canonical_body_hash(payload: BaseModel) -> str:
    body = json.dumps(
        payload.model_dump(exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def device_signature(
    secret: str, device_id: str, timestamp: str, nonce: str, payload: BaseModel
) -> str:
    message = f"{device_id}.{timestamp}.{nonce}.{canonical_body_hash(payload)}"
    return hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_qr_token(token: str) -> str:
    return hash_token_value(token)


def build_trace_hash(*parts: object) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pdf_escape(text: object) -> str:
    value = "" if text is None else str(text)
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_simple_pdf(lines: list[str]) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -18 Td")
        content_lines.append(f"({pdf_escape(line)}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(pdf)


def hash_password(password: str, salt: str) -> str:
    iterations = 210_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${digest}"


def legacy_hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, digest = stored_hash.split("$", 2)
            calculated = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
        except (ValueError, TypeError):
            return False
        return secrets.compare_digest(calculated, digest)
    return secrets.compare_digest(legacy_hash_password(password, salt), stored_hash)


def serialize_user(row: sqlite3.Row) -> dict:
    return {
        "user_id": row["id"],
        "username": row["username"],
        "phone": row["phone"] or row["username"],
        "name": row["name"] or row["display_name"] or row["username"],
        "organization": row["organization"] or "",
        "status": row["status"] or "active",
        "role": row["role"],
        "display_name": row["display_name"] or row["username"],
    }


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def current_user_from_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    token_hash = hash_token_value(token)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM auth_tokens
            JOIN users ON users.id = auth_tokens.user_id
            WHERE auth_tokens.token = ? OR auth_tokens.token = ?
            """,
            (token_hash, token),
        ).fetchone()
    return serialize_user(row) if row else None


def require_user(authorization: Optional[str]) -> Optional[dict]:
    return current_user_from_token(bearer_token(authorization))


def generate_task_id(conn: sqlite3.Connection) -> str:
    today = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")
    prefix = f"WD-{today}-"
    row = conn.execute(
        """
        SELECT task_id FROM task_handoff
        WHERE task_id LIKE ?
        ORDER BY task_id DESC
        LIMIT 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    next_no = 1
    if row:
        try:
            next_no = int(row["task_id"].rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            next_no = 1
    return f"{prefix}{next_no:03d}"


def can_view_task(user: Optional[dict], task: dict) -> bool:
    if not user:
        return False
    if user["role"] == "admin":
        return True
    owner = task.get("owner_user_id")
    carrier = task.get("carrier_user_id")
    receiver = task.get("receiver_user_id")
    user_id = user["user_id"]
    if owner is not None and str(owner) == str(user_id):
        return True
    if carrier is not None and str(carrier) == str(user_id):
        return True
    if receiver is not None and str(receiver) == str(user_id):
        return True
    return False


def is_seed_demo_task(task_id: Optional[str]) -> bool:
    """仅 SEED_DEMO_TASK 开启时对 TASK-001 放宽校验；生产默认关闭。"""
    return bool(SEED_DEMO_TASK and task_id == DEMO_TASK["task_id"])


def require_legacy_demo_api():
    if ENABLE_LEGACY_DEMO_API:
        return None
    return api_error(404, 40400, "legacy demo api disabled")


def live_telemetry_for_task(task: dict) -> tuple[Optional[dict], list[dict]]:
    """本地库无遥测时，从公网 live-snapshot 按 task_id / device_id 回退。"""
    try:
        snapshot = fetch_live_hardware_snapshot()
    except RuntimeError:
        return None, []
    task_id = str(task.get("task_id") or "")
    device_id = str(task.get("device_id") or "")
    latest_by_task = snapshot.get("latest_telemetry_by_task") or {}
    latest = latest_by_task.get(task_id)
    if not latest and device_id:
        latest = next(
            (
                item
                for item in latest_by_task.values()
                if str(item.get("device_id") or "") == device_id
            ),
            None,
        )
    history_by_task = snapshot.get("telemetry_history_by_task") or {}
    history = list(history_by_task.get(task_id) or [])
    if not history and device_id:
        history = [
            item
            for items in history_by_task.values()
            for item in (items or [])
            if str(item.get("device_id") or "") == device_id
        ]
    return latest, history


def can_modify_task(user: dict, task: dict) -> bool:
    return user["role"] == "admin" or str(task.get("owner_user_id") or "") == str(
        user["user_id"]
    )


def task_custody_owner(task: dict) -> dict:
    """按运单状态返回当前责任主体（与追溯页一致）。"""
    status = canonical_task_status(task)
    if status == "signed":
        return {
            "role": "receiver",
            "user_id": task.get("receiver_user_id"),
            "label": "接收",
            "name": task.get("receiver") or "接收方",
        }
    if status in {"in_transit", "arrived"}:
        return {
            "role": "carrier",
            "user_id": task.get("carrier_user_id"),
            "label": "承运",
            "name": task.get("carrier") or "承运方",
        }
    # pending_pack / pending_handoff / rejected / canceled 等：发货方负责
    return {
        "role": "owner",
        "user_id": task.get("owner_user_id"),
        "label": "发货",
        "name": task.get("sender") or "发货方",
    }


def can_handle_alarm(user: Optional[dict], task: dict, alarm: Optional[dict] = None) -> bool:
    """告警由发生时（或当前）责任人处置；管理员可代处置。"""
    if not user:
        return False
    if user["role"] == "admin":
        return True
    responsible_id = None
    if alarm:
        responsible_id = alarm.get("responsible_user_id")
    if responsible_id is None:
        responsible_id = task_custody_owner(task).get("user_id")
    if responsible_id is None:
        return False
    return str(responsible_id) == str(user["user_id"])


def canonical_task_status(task: dict) -> str:
    if task.get("canceled_at") or task.get("status") == "canceled":
        return "canceled"
    if task.get("rejected_at") or task.get("rejection_reason"):
        return "rejected"
    if task.get("signed_at"):
        return "signed"
    if task.get("arrived_at") or task.get("status") == "arrived":
        return "arrived"
    if task.get("started_at"):
        return "in_transit"
    if task.get("precheck_passed"):
        return "pending_handoff"
    return task.get("status") if task.get("status") in TASK_STATUSES else "pending_pack"


def serialize_task(row: sqlite3.Row) -> dict:
    task = row_to_dict(row)
    # 兼容早期 SQLite 表中以 TEXT 保存的责任人 ID，避免 "5" 与 5
    # 在权限判断时被当成不同用户而错误返回 404。
    for field in ("owner_user_id", "carrier_user_id", "receiver_user_id"):
        value = task.get(field)
        if isinstance(value, str) and value.strip().isdigit():
            task[field] = int(value.strip())
    task["status"] = canonical_task_status(task)
    if task.get("precheck_passed") is not None:
        task["precheck_passed"] = bool(task["precheck_passed"])
    if task.get("precheck_seal_ok") is not None:
        task["precheck_seal_ok"] = bool(task["precheck_seal_ok"])
    return task


def build_handoff_nodes(task: dict) -> list[dict]:
    nodes = []
    if task.get("started_at"):
        nodes.append({"type": "started", "timestamp": task["started_at"]})
    if task.get("arrived_at"):
        nodes.append({"type": "arrived", "timestamp": task["arrived_at"]})
    if task.get("signed_at"):
        nodes.append({"type": "signed", "timestamp": task["signed_at"]})
    if task.get("rejected_at"):
        nodes.append(
            {
                "type": "rejected",
                "timestamp": task["rejected_at"],
                "reason": task.get("rejection_reason"),
            }
        )
    return nodes


def normalize_telemetry(row: sqlite3.Row | dict) -> dict:
    item = dict(row) if isinstance(row, dict) else row_to_dict(row)
    box_status = normalize_box_status(item.get("box_status"))
    temp_status = str(item.get("temp_status") or "").upper()
    move_status = str(item.get("move_status") or "").upper()
    item["box_status"] = resolve_box_status_from_light(item.get("light_raw"), box_status)
    item["move_status"] = move_status
    item["temp_status"] = {
        "NORMAL": "TEMP_OK",
        "OK": "TEMP_OK",
    }.get(temp_status, temp_status)
    item["event_display"] = telemetry_event_display(item)
    return item


def telemetry_event_display(item: dict) -> str:
    if item["temp_status"] == "TEMP_ALERT":
        return EVENT_DISPLAY_LABELS["TEMP_ALERT"]
    if item["move_status"] == "FREE_FALL":
        return EVENT_DISPLAY_LABELS["FREE_FALL"]
    if item["move_status"] == "IMPACT":
        return EVENT_DISPLAY_LABELS["IMPACT"]
    if item["move_status"] == "SEVERE":
        return EVENT_DISPLAY_LABELS["SEVERE"]
    if item["move_status"] == "MILD":
        return EVENT_DISPLAY_LABELS["MILD"]
    if item["box_status"] == "BOX_OPEN":
        return EVENT_DISPLAY_LABELS["BOX_OPEN"]
    return EVENT_DISPLAY_LABELS.get(item["event_type"], item["event_type"])


def event_level(event_type: str) -> str:
    return EVENT_LEVELS.get(event_type, "medium")


def normalize_event(row: sqlite3.Row | dict) -> dict:
    item = dict(row) if isinstance(row, dict) else row_to_dict(row)
    if item.get("id") is None and item.get("event_id") is not None:
        item["id"] = item["event_id"]
    item["event_id"] = item.get("event_id") or item.get("id")
    item["description"] = item.get("event_detail") or item.get("description") or ""
    item["event_level"] = item.get("event_level") or event_level(str(item.get("event_type") or ""))
    item["event_display"] = EVENT_DISPLAY_LABELS.get(
        str(item.get("event_type") or ""),
        item.get("event_name") or str(item.get("event_type") or ""),
    )
    item["alarm_status"] = item.get("alarm_status") or "new"
    item["source"] = item.get("source") or "local"
    return item


def fetch_live_device_events(limit: int = 100) -> list[dict]:
    """设备异常事件：本机落库直读；开发机才 HTTP 代理公网。"""
    if not ENABLE_LIVE_HARDWARE_PROXY:
        init_db()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM event_log
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [normalize_event(row) for row in rows]
    request = urllib.request.Request(
        LIVE_DEVICE_EVENTS_URL,
        headers={"Accept": "application/json", "User-Agent": "coldchain-backend/1.0"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or []
    else:
        items = []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)][: max(1, limit)]


def _live_alarm_source_event_id(item: dict) -> Optional[int]:
    raw = item.get("id") if item.get("id") is not None else item.get("event_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def sync_live_alarms_to_local(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    device_id: str,
    live_items: list[dict],
    task: Optional[dict] = None,
) -> int:
    """
    公网事件写入本地 event_log，供 ack/resolve 等处置逻辑使用。
    已存在记录不覆盖本地 alarm_status；新建时写入当时责任人。
    """
    if not device_id:
        return 0
    custody = task_custody_owner(task or {"task_id": task_id, "status": "in_transit"})
    responsible_user_id = custody.get("user_id")
    responsible_role = custody.get("role")
    created = 0
    created_at = now_iso()
    for item in live_items:
        if str(item.get("device_id") or "") != device_id:
            continue
        event_type = str(item.get("event_type") or "").strip()
        if not event_type:
            continue
        timestamp = str(item.get("timestamp") or item.get("created_at") or created_at)
        event_detail = str(item.get("event_detail") or item.get("description") or "")
        event_name = str(
            item.get("event_name")
            or EVENT_DISPLAY_LABELS.get(event_type)
            or event_type
        )
        source_event_id = _live_alarm_source_event_id(item)
        existing = None
        if source_event_id is not None:
            existing = conn.execute(
                """
                SELECT id, responsible_user_id FROM event_log
                WHERE task_id = ? AND source = 'live' AND source_event_id = ?
                LIMIT 1
                """,
                (task_id, source_event_id),
            ).fetchone()
        if existing is None:
            existing = conn.execute(
                """
                SELECT id, responsible_user_id FROM event_log
                WHERE task_id = ?
                  AND device_id = ?
                  AND event_type = ?
                  AND timestamp = ?
                  AND event_detail = ?
                LIMIT 1
                """,
                (task_id, device_id, event_type, timestamp, event_detail),
            ).fetchone()
        if existing:
            # 补齐来源与责任人（不覆盖已有责任人）
            conn.execute(
                """
                UPDATE event_log
                SET source = COALESCE(NULLIF(source, ''), 'live'),
                    source_event_id = COALESCE(source_event_id, ?),
                    responsible_user_id = COALESCE(responsible_user_id, ?),
                    responsible_role = COALESCE(NULLIF(responsible_role, ''), ?)
                WHERE id = ?
                """,
                (
                    source_event_id,
                    responsible_user_id,
                    responsible_role,
                    existing["id"],
                ),
            )
            continue
        data_id = item.get("data_id")
        try:
            data_id = int(data_id) if data_id is not None else None
        except (TypeError, ValueError):
            data_id = None
        conn.execute(
            """
            INSERT INTO event_log (
                data_id, task_id, device_id, event_type, event_name,
                event_detail, timestamp, created_at, alarm_status,
                source, source_event_id, responsible_user_id, responsible_role
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data_id,
                task_id,
                device_id,
                event_type,
                event_name,
                event_detail,
                timestamp,
                created_at,
                "new",
                "live",
                source_event_id,
                responsible_user_id,
                responsible_role,
            ),
        )
        created += 1
    return created


def merge_task_alarms(
    local_items: list[dict],
    live_items: list[dict],
    *,
    task_id: str,
    device_id: str,
    limit: int,
) -> list[dict]:
    """本地 event_log + 公网同设备事件去重合并。"""
    merged: list[dict] = []
    seen: set[str] = set()

    def key_of(item: dict) -> str:
        return "|".join(
            [
                str(item.get("device_id") or ""),
                str(item.get("event_type") or ""),
                str(item.get("timestamp") or item.get("created_at") or ""),
                str(item.get("event_detail") or item.get("description") or ""),
            ]
        )

    for item in local_items:
        normalized = normalize_event(item)
        normalized["source"] = "local"
        normalized["task_id"] = task_id
        seen.add(key_of(normalized))
        merged.append(normalized)

    for item in live_items:
        if device_id and str(item.get("device_id") or "") != device_id:
            continue
        normalized = normalize_event(item)
        normalized["source"] = "live"
        # 展示归属当前运单，便于小程序按 task_id 打开详情
        normalized["task_id"] = task_id
        fingerprint = key_of(normalized)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        # 公网 id 可能与本地冲突：用负数段避免覆盖本地告警 id
        raw_id = normalized.get("id") or normalized.get("event_id") or 0
        try:
            raw_id = int(raw_id)
        except (TypeError, ValueError):
            raw_id = abs(hash(fingerprint)) % 100000000
        normalized["id"] = -abs(raw_id) if raw_id >= 0 else raw_id
        normalized["event_id"] = normalized["id"]
        merged.append(normalized)

    merged.sort(
        key=lambda item: str(item.get("timestamp") or item.get("created_at") or ""),
        reverse=True,
    )
    return merged[:limit]


def task_latest_telemetry(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT * FROM device_data
        WHERE task_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return normalize_telemetry(row) if row else None


def task_abnormal_count(conn: sqlite3.Connection, task_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS count FROM event_log WHERE task_id = ?",
        (task_id,),
    ).fetchone()["count"]


def enrich_task(
    row: sqlite3.Row,
    conn: sqlite3.Connection,
    live_by_device: Optional[dict[str, dict]] = None,
) -> dict:
    task = serialize_task(row)
    task_id = task["task_id"]
    device_id = str(task.get("device_id") or "")
    latest = task_latest_telemetry(conn, task_id)
    if not latest and device_id:
        device_row = conn.execute(
            """
            SELECT * FROM device_data
            WHERE device_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        latest = normalize_telemetry(device_row) if device_row else None
    if not latest and live_by_device and device_id:
        live_item = live_by_device.get(device_id)
        if live_item:
            latest = normalize_telemetry(live_item)
    task["abnormal_count"] = task_abnormal_count(conn, task_id)
    min_temp = task.get("temperature_min")
    max_temp = task.get("temperature_max")
    if min_temp is not None and max_temp is not None:
        task["temperature_range"] = f"{min_temp}~{max_temp}℃"
    elif not task.get("temperature_range"):
        task["temperature_range"] = None
    if latest:
        task["latest_temperature"] = latest["temperature"]
        task["latest_humidity"] = latest["humidity"]
        task["latest_box_status"] = latest["box_status"]
        task["latest_move_status"] = latest["move_status"]
        task["latest_temp_status"] = latest["temp_status"]
    else:
        task["latest_temperature"] = None
        task["latest_humidity"] = None
        task["latest_box_status"] = None
        task["latest_move_status"] = None
        task["latest_temp_status"] = None
    return task


def live_telemetry_index_by_device() -> dict[str, dict]:
    """一次拉取公网最新值，按 device_id 索引，供任务列表补全实测温度。"""
    try:
        snapshot = fetch_live_hardware_snapshot()
    except RuntimeError:
        return {}
    index: dict[str, dict] = {}
    for item in (snapshot.get("latest_telemetry_by_task") or {}).values():
        device_id = str(item.get("device_id") or "")
        if device_id and device_id not in index:
            index[device_id] = item
    return index


def legacy_task_view(task: dict) -> dict:
    view = dict(task)
    view["status"] = TASK_STATUS_LABELS.get(view["status"], view["status"])
    return view


def get_trace_report_data(conn: sqlite3.Connection, task_id: str) -> dict:
    task_row = get_task_by_id(conn, task_id)
    if not task_row:
        return {}

    latest = conn.execute(
        """
        SELECT * FROM device_data
        WHERE task_id = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_records,
            MIN(temperature) AS min_temperature,
            MAX(temperature) AS max_temperature,
            ROUND(AVG(temperature), 2) AS avg_temperature,
            MIN(humidity) AS min_humidity,
            MAX(humidity) AS max_humidity
        FROM device_data
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    event_count = conn.execute(
        "SELECT COUNT(*) AS count FROM event_log WHERE task_id = ?",
        (task_id,),
    ).fetchone()["count"]
    events = conn.execute(
        """
        SELECT * FROM event_log
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (task_id,),
    ).fetchall()
    evidence_files = conn.execute(
        """
        SELECT * FROM files
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (task_id,),
    ).fetchall()
    status_history = conn.execute(
        """
        SELECT * FROM task_status_history
        WHERE task_id = ?
        ORDER BY id ASC
        """,
        (task_id,),
    ).fetchall()

    summary = row_to_dict(stats)
    summary["event_count"] = event_count
    summary["evidence_count"] = len(evidence_files)
    task = enrich_task(task_row, conn)
    evidence_items = [serialize_file(row) for row in evidence_files]
    trace_hash = build_trace_hash(
        task_id,
        task.get("status"),
        task.get("started_at"),
        task.get("arrived_at"),
        task.get("signed_at"),
        task.get("rejected_at"),
        event_count,
        ",".join(item["sha256"] for item in evidence_items),
    )
    return {
        "report_version": "MVP-1",
        "trace_hash": trace_hash,
        "task": task,
        "latest": normalize_telemetry(latest) if latest else None,
        "summary": summary,
        "events": [normalize_event(row) for row in events],
        "evidence_files": evidence_items,
        "status_history": [serialize_status_history(row) for row in status_history],
        "handoff_nodes": build_handoff_nodes(task),
    }


@app.post("/api/device/data")
def receive_device_data(data: DeviceDataIn):
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        row, error = save_device_data(conn, data)
        if error:
            return error

    return {"ok": True, "data": row_to_dict(row)}


@app.get("/api/device/latest")
def get_latest_device_data() -> dict:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM device_data ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return normalize_telemetry(row) if row else {}


@app.get("/api/device/history")
def get_device_history() -> list[dict]:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM device_data ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return [normalize_telemetry(row) for row in rows]


@app.get("/api/device/events")
def get_device_events() -> list[dict]:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM event_log
            WHERE event_type IN ('BOX_OPEN', 'TEMP_ALERT', 'SEVERE', 'IMPACT', 'FREE_FALL', 'MILD')
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()
    return [normalize_event(row) for row in rows]


@app.post("/api/task/start")
def start_task() -> dict:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        ensure_demo_task(conn)
        row = get_task_row(conn)
        if canonical_task_status(row_to_dict(row)) == "in_transit":
            return {"ok": True, "task": legacy_task_view(enrich_task(row, conn))}
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, started_at = ?, signed_at = NULL,
                arrived_at = NULL, rejected_at = NULL,
                rejection_reason = NULL, updated_at = ?
            WHERE task_id = ?
            """,
            ("in_transit", timestamp, timestamp, DEMO_TASK["task_id"]),
        )
        row = get_task_row(conn)
    return {"ok": True, "task": legacy_task_view(enrich_task(row, conn))}


@app.post("/api/task/sign")
def sign_task() -> dict:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        ensure_demo_task(conn)
        row = get_task_row(conn)
        if canonical_task_status(row_to_dict(row)) == "signed":
            return {"ok": True, "task": legacy_task_view(enrich_task(row, conn))}

        timestamp = now_iso()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, signed_at = ?, rejected_at = NULL,
                rejection_reason = NULL, updated_at = ?
            WHERE task_id = ?
            """,
            ("signed", timestamp, timestamp, DEMO_TASK["task_id"]),
        )
        row = get_task_row(conn)
    return {"ok": True, "task": legacy_task_view(enrich_task(row, conn))}


@app.get("/api/task/current")
def get_current_task() -> dict:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        row = get_task_row(conn)
    return legacy_task_view(enrich_task(row, conn))


@app.get("/api/task/report")
def get_task_report() -> dict:
    disabled = require_legacy_demo_api()
    if disabled:
        return disabled
    init_db()
    with get_connection() as conn:
        task = legacy_task_view(enrich_task(get_task_row(conn), conn))
        latest = conn.execute(
            """
            SELECT * FROM device_data
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (DEMO_TASK["task_id"],),
        ).fetchone()
        stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total_records,
                MIN(temperature) AS min_temperature,
                MAX(temperature) AS max_temperature,
                ROUND(AVG(temperature), 2) AS avg_temperature,
                MIN(humidity) AS min_humidity,
                MAX(humidity) AS max_humidity
            FROM device_data
            WHERE task_id = ?
            """,
            (DEMO_TASK["task_id"],),
        ).fetchone()
        event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM event_log WHERE task_id = ?",
            (DEMO_TASK["task_id"],),
        ).fetchone()["count"]
        events = conn.execute(
            """
            SELECT * FROM event_log
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (DEMO_TASK["task_id"],),
        ).fetchall()

    summary = row_to_dict(stats)
    summary["event_count"] = event_count
    return {
        "task": task,
        "latest": normalize_telemetry(latest) if latest else {},
        "summary": summary,
        "events": [normalize_event(row) for row in events],
    }


@app.get("/api/v1/meta/contracts")
def get_v1_contracts() -> dict:
    return api_success(
        {
            "task_statuses": TASK_STATUSES,
            "task_status_labels": TASK_STATUS_LABELS,
            "box_statuses": BOX_STATUSES,
            "move_statuses": MOVE_STATUSES,
            "temperature_statuses": TEMPERATURE_STATUSES,
            "device_statuses": DEVICE_STATUSES,
            "alarm_statuses": ALARM_STATUSES,
            "handoff_statuses": HANDOFF_STATUSES,
            "handoff_confirmation_requirements": {
                "qr_verified": True,
                "face_verification": "optional_placeholder",
            },
            "evidence_file_types": sorted(ALLOWED_EVIDENCE_TYPES),
            "evidence_file_max_bytes": MAX_EVIDENCE_FILE_SIZE,
            "auth_roles": AUTH_ROLES,
            "role_permissions": ROLE_PERMISSIONS,
            "timestamp_format": "ISO 8601",
            "field_naming": "snake_case",
        }
    )


@app.post("/api/v1/auth/register")
def register_user(payload: RegisterIn):
    init_db()
    role = payload.role.strip().lower()
    if role not in AUTH_ROLES:
        return api_error(422, 42202, "invalid role")
    if role == "admin" and not ALLOW_ADMIN_SELF_REGISTER:
        return api_error(403, 40303, "admin self-registration disabled")
    username = (payload.username or payload.phone or "").strip()
    if not username:
        return api_error(422, 42203, "username or phone required")

    salt = secrets.token_hex(16)
    password_hash = hash_password(payload.password, salt)
    display_name = payload.display_name or payload.name or username
    created_at = now_iso()
    phone = (payload.phone or username).strip()
    name = (payload.name or display_name).strip()
    organization = (payload.organization or "").strip()

    try:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? OR phone = ?",
                (username, phone),
            ).fetchone()
            if existing:
                return api_error(409, 40902, "username already exists")
            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, salt, role, display_name, created_at,
                    phone, name, organization, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    password_hash,
                    salt,
                    role,
                    display_name,
                    created_at,
                    phone,
                    name,
                    organization,
                    "active",
                ),
            )
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            record_audit(
                conn,
                "auth.register",
                user_id=row["id"],
                resource_type="user",
                resource_id=str(row["id"]),
            )
    except sqlite3.IntegrityError:
        return api_error(409, 40902, "username already exists")

    return api_success({"user": serialize_user(row)}, "registered")


@app.get("/api/v1/users")
def list_v1_assignment_candidates(
    role: str = Query(...),
    keyword: Optional[str] = Query(default=None),
    organization: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] not in {"sender", "admin"}:
        return api_error(403, 40301, "forbidden")
    role = role.strip().lower()
    if role not in {"carrier", "receiver"}:
        return api_error(422, 42202, "role must be carrier or receiver")

    conditions = ["role = ?", "COALESCE(status, 'active') = 'active'"]
    params: list[object] = [role]
    if keyword:
        conditions.append(
            "(LOWER(COALESCE(name, display_name, username)) LIKE ? "
            "OR LOWER(COALESCE(organization, '')) LIKE ?)"
        )
        search = f"%{keyword.strip().lower()}%"
        params.extend([search, search])
    if organization:
        conditions.append("organization = ?")
        params.append(organization.strip())

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS count FROM users WHERE {' AND '.join(conditions)}",
            params,
        ).fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT * FROM users
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(name, display_name, username), id
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    items = [
        {
            "user_id": row["id"],
            "name": row["name"] or row["display_name"] or row["username"],
            "display_name": row["display_name"] or row["username"],
            "organization": row["organization"] or "",
            "role": row["role"],
            "status": row["status"] or "active",
        }
        for row in rows
    ]
    return api_success(
        {"items": items, "page": page, "page_size": page_size, "total": total}
    )


@app.post("/api/v1/auth/login")
def login_user(payload: LoginIn):
    init_db()
    account = (payload.username or payload.phone or "").strip()
    if not account:
        return api_error(422, 42203, "username or phone required")
    with get_connection() as conn:
        rate_error = check_rate_limit(
            conn,
            "auth.login",
            account.lower(),
            LOGIN_RATE_LIMIT,
        )
        if rate_error:
            return rate_error
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? OR phone = ?",
            (account, account),
        ).fetchone()
        if not row:
            return api_error(401, 40101, "invalid username or password")
        if row["status"] and row["status"] != "active":
            return api_error(403, 40302, "user disabled")

        if not verify_password(payload.password, row["salt"], row["password_hash"]):
            return api_error(401, 40101, "invalid username or password")
        if not row["password_hash"].startswith("pbkdf2_sha256$"):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(payload.password, row["salt"]), row["id"]),
            )

        token = secrets.token_urlsafe(32)
        token_hash = hash_token_value(token)
        conn.execute(
            """
            INSERT INTO auth_tokens (token, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, row["id"], now_iso()),
        )
        record_audit(
            conn,
            "auth.login",
            user_id=row["id"],
            resource_type="user",
            resource_id=str(row["id"]),
        )

    return api_success(
        {
            "token": token,
            "token_type": "bearer",
            "user": serialize_user(row),
        },
        "login success",
    )


@app.get("/api/v1/auth/me")
def get_auth_me(authorization: Optional[str] = Header(None)):
    init_db()
    user = current_user_from_token(bearer_token(authorization))
    if not user:
        return api_error(401, 40102, "unauthorized")
    return api_success(user)


@app.get("/api/v1/auth/permissions")
def get_auth_permissions(authorization: Optional[str] = Header(None)):
    init_db()
    user = current_user_from_token(bearer_token(authorization))
    if not user:
        return api_error(401, 40102, "unauthorized")
    return api_success(
        {
            "role": user["role"],
            "permissions": ROLE_PERMISSIONS.get(user["role"], []),
        }
    )


@app.post("/api/v1/auth/logout")
def logout_user(authorization: Optional[str] = Header(None)):
    init_db()
    token = bearer_token(authorization)
    if not token:
        return api_error(401, 40102, "unauthorized")
    token_hash = hash_token_value(token)
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM auth_tokens WHERE token = ? OR token = ?",
            (token_hash, token),
        )
        if cursor.rowcount:
            record_audit(conn, "auth.logout", resource_type="auth_token")
    if cursor.rowcount == 0:
        return api_error(401, 40102, "unauthorized")
    return api_success({"logged_out": True}, "logout success")


@app.post("/api/v1/auth/refresh")
def refresh_auth_token(authorization: Optional[str] = Header(None)):
    init_db()
    old_token = bearer_token(authorization)
    if not old_token:
        return api_error(401, 40102, "unauthorized")
    old_token_hash = hash_token_value(old_token)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM auth_tokens
            JOIN users ON users.id = auth_tokens.user_id
            WHERE auth_tokens.token = ? OR auth_tokens.token = ?
            """,
            (old_token_hash, old_token),
        ).fetchone()
        if not row:
            return api_error(401, 40102, "unauthorized")

        new_token = secrets.token_urlsafe(32)
        new_token_hash = hash_token_value(new_token)
        conn.execute(
            "DELETE FROM auth_tokens WHERE token = ? OR token = ?",
            (old_token_hash, old_token),
        )
        conn.execute(
            """
            INSERT INTO auth_tokens (token, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (new_token_hash, row["id"], now_iso()),
        )
        record_audit(
            conn,
            "auth.refresh",
            user_id=row["id"],
            resource_type="user",
            resource_id=str(row["id"]),
        )

    return api_success(
        {
            "token": new_token,
            "token_type": "bearer",
            "user": serialize_user(row),
        },
        "refresh success",
    )


@app.post("/api/v1/device/telemetry")
def receive_v1_device_telemetry(
    payload: DeviceTelemetryIn,
    x_device_id: Optional[str] = Header(None),
    x_timestamp: Optional[str] = Header(None),
    x_nonce: Optional[str] = Header(None),
    x_signature: Optional[str] = Header(None),
):
    init_db()
    with get_connection() as conn:
        signature_error = verify_device_signature(
            conn,
            payload,
            payload.device_id,
            x_device_id,
            x_timestamp,
            x_nonce,
            x_signature,
        )
        if signature_error:
            return signature_error
        device = conn.execute(
            "SELECT current_task_id FROM devices WHERE device_id = ?",
            (payload.device_id,),
        ).fetchone()
        resolved_task_id = resolve_inbound_task_id(
            conn, payload.device_id, payload.task_id
        )
        if not device or device["current_task_id"] != resolved_task_id:
            return api_error(409, 40920, "device does not match task")
        task = get_task_by_id(conn, resolved_task_id)
        if payload.sequence is not None:
            existing = conn.execute(
                """
                SELECT * FROM device_data
                WHERE device_id = ? AND sequence = ?
                """,
                (payload.device_id, payload.sequence),
            ).fetchone()
            if existing:
                update_device_seen(
                    conn,
                    payload.device_id,
                    resolved_task_id,
                    payload.battery,
                    now_iso(),
                )
                return api_success(
                    {
                        "saved": 0,
                        "duplicate": True,
                        "items": [normalize_telemetry(existing)],
                    },
                    "telemetry duplicated",
                )
        data = DeviceDataIn(
            device_id=payload.device_id,
            task_id=resolved_task_id,
            temperature=payload.temperature,
            humidity=payload.humidity,
            light_raw=payload.light_raw,
            box_status=payload.box_status,
            move_status=payload.move_status,
            temp_status=telemetry_temp_status(payload, task),
            acc_total=payload.acc_total if payload.acc_total is not None else 0.0,
            motion_score=(
                payload.motion_score if payload.motion_score is not None else 0.0
            ),
            timestamp=payload.captured_at,
        )
        row, error = save_device_data(
            conn,
            data,
            sequence=payload.sequence,
            battery=payload.battery,
            location=payload.location,
        )
        if error:
            return api_error(409, 40920, "device does not match task")
        update_device_seen(
            conn,
            payload.device_id,
            resolved_task_id,
            payload.battery,
            now_iso(),
        )

    return api_success(
        {
            "saved": 1,
            "items": [normalize_telemetry(row)],
        },
        "telemetry saved",
    )


@app.post("/api/v1/device/heartbeat")
def receive_v1_device_heartbeat(
    payload: DeviceHeartbeatIn,
    x_device_id: Optional[str] = Header(None),
    x_timestamp: Optional[str] = Header(None),
    x_nonce: Optional[str] = Header(None),
    x_signature: Optional[str] = Header(None),
):
    init_db()
    timestamp = payload.timestamp or now_iso()
    created_at = now_iso()
    with get_connection() as conn:
        signature_error = verify_device_signature(
            conn,
            payload,
            payload.device_id,
            x_device_id,
            x_timestamp,
            x_nonce,
            x_signature,
        )
        if signature_error:
            return signature_error
        device = conn.execute(
            "SELECT current_task_id FROM devices WHERE device_id = ?",
            (payload.device_id,),
        ).fetchone()
        if payload.task_id and (
            not device or device["current_task_id"] != payload.task_id
        ):
            return api_error(409, 40920, "device does not match task")
        cursor = conn.execute(
            """
            INSERT INTO device_heartbeat (
                device_id, task_id, battery, rssi, network,
                status, timestamp, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.device_id,
                payload.task_id,
                payload.battery,
                payload.rssi,
                payload.network,
                "online",
                timestamp,
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM device_heartbeat WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        update_device_seen(
            conn,
            payload.device_id,
            payload.task_id,
            payload.battery,
            created_at,
        )

    return api_success(row_to_dict(row), "heartbeat saved")


@app.post("/api/v1/devices")
def register_v1_device(
    payload: RegisterDeviceIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] not in {"sender", "admin"}:
        return api_error(403, 40301, "forbidden")

    timestamp = now_iso()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (payload.device_id,),
        ).fetchone()
        if existing and not can_manage_device(user, row_to_dict(existing)):
            return api_error(409, 40921, "device id already registered")
        conn.execute(
            """
            INSERT INTO devices (
                device_id, device_name, model, status, current_task_id,
                battery, last_seen_at, created_at, updated_at, device_secret_hash,
                owner_user_id, organization
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name = COALESCE(excluded.device_name, devices.device_name),
                model = COALESCE(excluded.model, devices.model),
                device_secret_hash = COALESCE(excluded.device_secret_hash, devices.device_secret_hash),
                updated_at = excluded.updated_at
            """,
            (
                payload.device_id,
                payload.device_name,
                payload.model,
                "available",
                None,
                None,
                None,
                timestamp,
                timestamp,
                (
                    hash_device_secret(payload.device_secret)
                    if payload.device_secret
                    else None
                ),
                user["user_id"],
                user["organization"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (payload.device_id,),
        ).fetchone()
        record_audit(
            conn,
            "device.register",
            user_id=user["user_id"],
            resource_type="device",
            resource_id=payload.device_id,
        )
    return api_success(serialize_device(row), "device registered")


@app.get("/api/v1/devices")
def list_v1_devices(
    authorization: Optional[str] = Header(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY updated_at DESC").fetchall()
        items = [
            serialize_device(row)
            for row in rows
            if can_view_device(conn, user, row_to_dict(row))
        ]
    start = (page - 1) * page_size
    return api_success(
        {
            "items": items[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": len(items),
        }
    )


@app.post("/api/v1/devices/{device_id}/bind")
def bind_v1_device(
    device_id: str,
    payload: BindDeviceIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    timestamp = now_iso()
    with get_connection() as conn:
        task_row = get_task_by_id(conn, payload.task_id)
        if not task_row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")

        device = ensure_device_for_bind(conn, device_id, user)
        if not device:
            return api_error(404, 40402, "device not found")
        device_data = row_to_dict(device)
        if not can_manage_device(user, device_data):
            return api_error(404, 40402, "device not found")
        if (
            device_data.get("current_task_id")
            and device_data["current_task_id"] != payload.task_id
        ):
            return api_error(409, 40920, "device already bound")

        old_device_id = task.get("device_id")
        if old_device_id and old_device_id != device_id:
            conn.execute(
                """
                UPDATE device_bindings
                SET status = ?, unbound_at = ?
                WHERE device_id = ? AND task_id = ? AND status = ?
                """,
                ("unbound", timestamp, old_device_id, payload.task_id, "bound"),
            )
            conn.execute(
                """
                UPDATE devices
                SET status = ?, current_task_id = NULL, updated_at = ?
                WHERE device_id = ? AND current_task_id = ?
                """,
                ("available", timestamp, old_device_id, payload.task_id),
            )

        conn.execute(
            """
            UPDATE device_bindings
            SET status = ?, unbound_at = ?
            WHERE (device_id = ? OR task_id = ?) AND status = ?
            """,
            ("unbound", timestamp, device_id, payload.task_id, "bound"),
        )
        cursor = conn.execute(
            """
            INSERT INTO device_bindings (
                device_id, task_id, bound_at, unbound_at, status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, payload.task_id, timestamp, None, "bound"),
        )
        conn.execute(
            """
            UPDATE devices
            SET status = ?, current_task_id = ?, updated_at = ?
            WHERE device_id = ?
            """,
            ("bound", payload.task_id, timestamp, device_id),
        )
        conn.execute(
            """
            UPDATE task_handoff
            SET device_id = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (device_id, timestamp, payload.task_id),
        )
        row = conn.execute(
            "SELECT * FROM device_bindings WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        record_audit(
            conn,
            "device.bind",
            user_id=user["user_id"],
            resource_type="device",
            resource_id=device_id,
            task_id=payload.task_id,
        )
    return api_success(serialize_binding(row), "device bound")


@app.post("/api/v1/devices/{device_id}/unbind")
def unbind_v1_device(
    device_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] not in {"sender", "admin"}:
        return api_error(403, 40301, "forbidden")

    timestamp = now_iso()
    with get_connection() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not device:
            return api_error(404, 40402, "device not found")
        device_data = row_to_dict(device)
        if not can_manage_device(user, device_data):
            return api_error(404, 40402, "device not found")
        task_id = device_data.get("current_task_id")
        if task_id:
            task_row = get_task_by_id(conn, task_id)
            if task_row and not can_modify_task(user, serialize_task(task_row)):
                return api_error(404, 40402, "device not found")
        conn.execute(
            """
            UPDATE device_bindings
            SET status = ?, unbound_at = ?
            WHERE device_id = ? AND status = ?
            """,
            ("unbound", timestamp, device_id, "bound"),
        )
        conn.execute(
            """
            UPDATE devices
            SET status = ?, current_task_id = NULL, updated_at = ?
            WHERE device_id = ?
            """,
            ("available", timestamp, device_id),
        )
        if task_id:
            conn.execute(
                """
                UPDATE task_handoff
                SET device_id = NULL, updated_at = ?
                WHERE task_id = ? AND device_id = ?
                """,
                (timestamp, task_id, device_id),
            )
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        record_audit(
            conn,
            "device.unbind",
            user_id=user["user_id"],
            resource_type="device",
            resource_id=device_id,
        )
    return api_success(serialize_device(row), "device unbound")


@app.get("/api/v1/devices/{device_id}/bindings")
def get_v1_device_bindings(
    device_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not device or not can_view_device(conn, user, row_to_dict(device)):
            return api_error(404, 40402, "device not found")
        rows = conn.execute(
            """
            SELECT * FROM device_bindings
            WHERE device_id = ?
            ORDER BY id DESC
            """,
            (device_id,),
        ).fetchall()
    return api_success({"items": [serialize_binding(row) for row in rows]})


@app.post("/api/v1/devices/{device_id}/rotate-secret")
def rotate_v1_device_secret(
    device_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    new_secret = secrets.token_urlsafe(32)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not row or not can_manage_device(user, row_to_dict(row)):
            return api_error(404, 40402, "device not found")
        conn.execute(
            """
            UPDATE devices
            SET device_secret_hash = ?, updated_at = ?
            WHERE device_id = ?
            """,
            (hash_device_secret(new_secret), timestamp, device_id),
        )
        conn.execute("DELETE FROM device_nonces WHERE device_id = ?", (device_id,))
        record_audit(
            conn,
            "device.rotate_secret",
            user_id=user["user_id"],
            resource_type="device",
            resource_id=device_id,
        )
    return api_success(
        {
            "device_id": device_id,
            "device_secret": new_secret,
            "delivered_once": True,
            "rotated_at": timestamp,
        },
        "device secret rotated",
    )


@app.get("/api/v1/tasks")
def list_v1_tasks(
    authorization: Optional[str] = Header(None),
    status: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    updated_after: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    init_db()
    updated_after = normalize_query_time(updated_after)
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM task_handoff ORDER BY updated_at DESC"
        ).fetchall()
        live_by_device = live_telemetry_index_by_device()
        tasks = [
            enrich_task(row, conn, live_by_device)
            for row in rows
            if can_view_task(user, serialize_task(row))
        ]

    if status:
        tasks = [task for task in tasks if task["status"] == status]
    if keyword:
        lowered = keyword.lower()
        tasks = [
            task
            for task in tasks
            if lowered in task["task_id"].lower()
            or lowered in task["sample_name"].lower()
            or lowered in (task.get("batch") or "").lower()
        ]
    if updated_after:
        tasks = [
            task
            for task in tasks
            if task.get("updated_at") and task["updated_at"] > updated_after
        ]

    start = (page - 1) * page_size
    end = start + page_size
    return api_success(
        {
            "items": tasks[start:end],
            "page": page,
            "page_size": page_size,
            "total": len(tasks),
        }
    )


@app.post("/api/v1/tasks")
def create_v1_task(
    payload: CreateTaskIn,
    authorization: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] not in {"sender", "admin"}:
        return api_error(403, 40301, "forbidden")
    if payload.device_id is not None:
        return api_error(422, 42206, "bind devices through the device binding endpoint")

    timestamp = now_iso()
    with get_connection() as conn:
        if idempotency_key:
            existing = conn.execute(
                """
                SELECT * FROM task_handoff
                WHERE owner_user_id = ? AND idempotency_key = ?
                """,
                (user["user_id"], idempotency_key),
            ).fetchone()
            if existing:
                return api_success(enrich_task(existing, conn), "task created")

        task_id = generate_task_id(conn)
        sender = user["organization"] or user["display_name"]
        cursor = conn.execute(
            """
            INSERT INTO task_handoff (
                task_id, device_id, sample_name, sender, receiver, carrier,
                status, started_at, signed_at, updated_at,
                owner_user_id, batch, expected_arrival, box_id, seal_id,
                temperature_min, temperature_max, idempotency_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                None,
                payload.sample_name,
                sender,
                payload.receiver or "",
                payload.carrier or "",
                "pending_pack",
                None,
                None,
                timestamp,
                user["user_id"],
                payload.batch,
                payload.expected_arrival,
                payload.box_id,
                payload.seal_id,
                payload.temperature_min,
                payload.temperature_max,
                idempotency_key,
            ),
        )
        row = conn.execute(
            "SELECT * FROM task_handoff WHERE rowid = ?",
            (cursor.lastrowid,),
        ).fetchone()
        record_status_history(
            conn,
            task_id=task_id,
            from_status=None,
            to_status="pending_pack",
            reason="task created",
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.create",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
        )
    return api_success(enrich_task(row, conn), "task created")


@app.get("/api/v1/tasks/{task_id}")
def get_v1_task(task_id: str, authorization: Optional[str] = Header(None)):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if row and not can_view_task(user, serialize_task(row)):
            return api_error(404, 40401, "task not found")
    if not row:
        return api_error(404, 40401, "task not found")
    return api_success(enrich_task(row, conn))


@app.patch("/api/v1/tasks/{task_id}")
def update_v1_task(
    task_id: str,
    payload: UpdateTaskIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        if task["status"] not in {"pending_pack", "pending_handoff"}:
            return task_state_conflict()
        if "device_id" in payload.model_fields_set:
            return api_error(
                422, 42206, "bind devices through the device binding endpoint"
            )
        allowed = {
            "sample_name",
            "batch",
            "receiver",
            "carrier",
            "expected_arrival",
            "box_id",
            "seal_id",
            "temperature_min",
            "temperature_max",
        }
        changes = payload.model_dump(exclude_unset=True)
        assignments = []
        values = []
        for key, value in changes.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                values.append(value)
        if assignments:
            assignments.append("updated_at = ?")
            values.append(now_iso())
            values.append(task_id)
            conn.execute(
                f"UPDATE task_handoff SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
        updated = get_task_by_id(conn, task_id)
    return api_success(enrich_task(updated, conn), "task updated")


@app.post("/api/v1/tasks/{task_id}/assign")
def assign_v1_task(
    task_id: str,
    payload: AssignTaskIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        receiver_recovery = (
            task["status"] in {"in_transit", "arrived"}
            and not task.get("receiver_user_id")
            and payload.receiver_user_id is not None
            and payload.carrier_user_id == task.get("carrier_user_id")
        )
        if task["status"] not in {"pending_pack", "pending_handoff"} and not receiver_recovery:
            return task_state_conflict()
        if payload.carrier_user_id is not None:
            carrier = get_user_row(conn, payload.carrier_user_id)
            if (
                not carrier
                or carrier["role"] != "carrier"
                or (carrier["status"] or "active") != "active"
            ):
                return api_error(422, 42207, "invalid carrier")
        if payload.receiver_user_id is not None:
            receiver = get_user_row(conn, payload.receiver_user_id)
            if (
                not receiver
                or receiver["role"] != "receiver"
                or (receiver["status"] or "active") != "active"
            ):
                return api_error(422, 42208, "invalid receiver")

        conn.execute(
            """
            UPDATE task_handoff
            SET carrier_user_id = ?, receiver_user_id = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                payload.carrier_user_id,
                payload.receiver_user_id,
                now_iso(),
                task_id,
            ),
        )
        record_audit(
            conn,
            "task.assign_receiver_recovery" if receiver_recovery else "task.assign",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
            detail=(
                f"receiver_user_id={payload.receiver_user_id}"
                if receiver_recovery
                else None
            ),
        )
        updated = get_task_by_id(conn, task_id)
    return api_success(enrich_task(updated, conn), "task assigned")


@app.post("/api/v1/tasks/{task_id}/cancel")
def cancel_v1_task(
    task_id: str,
    authorization: Optional[str] = Header(None),
    reason: Optional[str] = Body(default=None, embed=True),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    timestamp = now_iso()
    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        if task["status"] in {
            "in_transit",
            "arrived",
            "signed",
            "rejected",
            "canceled",
        }:
            return task_state_conflict()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, canceled_at = ?, cancel_reason = ?, updated_at = ?
            WHERE task_id = ?
            """,
            ("canceled", timestamp, reason, timestamp, task_id),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=task["status"],
            to_status="canceled",
            reason=reason,
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        updated = get_task_by_id(conn, task_id)
    return api_success(enrich_task(updated, conn), "task canceled")


def purge_task_records(conn: sqlite3.Connection, task_id: str) -> None:
    """硬删除运单及其关联记录（运输开始前的测试单清理）。"""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    for table in tables:
        if table in {"sqlite_sequence", "users", "auth_tokens", "rate_limits"}:
            continue
        columns = [
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        if "task_id" not in columns:
            continue
        conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))


@app.delete("/api/v1/tasks/{task_id}")
def delete_v1_task(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if is_seed_demo_task(task_id):
        return api_error(403, 40303, "demo task cannot be deleted")

    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        if task["status"] not in {"pending_pack", "pending_handoff", "canceled"}:
            return task_state_conflict()
        purge_task_records(conn, task_id)
        record_audit(
            conn,
            "task.delete",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=None,
            detail=f"deleted status={task['status']}",
        )
    return api_success({"task_id": task_id}, "task deleted")


@app.post("/api/v1/tasks/{task_id}/precheck")
def precheck_v1_task(
    task_id: str,
    payload: PrecheckTaskIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    timestamp = now_iso()
    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        if task["status"] != "pending_pack":
            return task_state_conflict()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, precheck_passed = ?, precheck_temperature = ?,
                precheck_seal_ok = ?, precheck_note = ?, prechecked_at = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                "pending_handoff" if payload.passed else "pending_pack",
                1 if payload.passed else 0,
                payload.temperature,
                None if payload.seal_ok is None else int(payload.seal_ok),
                payload.note,
                timestamp,
                timestamp,
                task_id,
            ),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=task["status"],
            to_status="pending_handoff" if payload.passed else "pending_pack",
            reason=payload.note or "precheck",
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        updated = get_task_by_id(conn, task_id)
    return api_success(enrich_task(updated, conn), "task prechecked")


@app.post("/api/v1/tasks/{task_id}/handoffs")
def create_v1_handoff(
    task_id: str,
    payload: CreateHandoffIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    if payload.handoff_type not in {
        "sender_to_carrier",
        "carrier_to_carrier",
        "carrier_to_receiver",
    }:
        return api_error(422, 42204, "invalid handoff type")

    timestamp = now_iso()
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        if not can_view_task(user, task):
            return api_error(404, 40401, "task not found")
        to_user = get_user_row(conn, payload.to_user_id)
        if not to_user or (to_user["status"] or "active") != "active":
            return api_error(404, 40404, "target user not found")
        if payload.handoff_type == "sender_to_carrier":
            if (
                not can_modify_task(user, task)
                or task.get("carrier_user_id") != payload.to_user_id
                or to_user["role"] != "carrier"
            ):
                return api_error(403, 40301, "forbidden")
        elif payload.handoff_type == "carrier_to_receiver":
            if (
                user["role"] != "admin"
                and task.get("carrier_user_id") != user["user_id"]
            ):
                return api_error(403, 40301, "forbidden")
            if (
                task.get("receiver_user_id") != payload.to_user_id
                or to_user["role"] != "receiver"
            ):
                return api_error(422, 42209, "invalid handoff target")
        elif user["role"] != "admin" and task.get("carrier_user_id") != user["user_id"]:
            return api_error(403, 40301, "forbidden")
        if payload.to_user_id == user["user_id"]:
            return api_error(422, 42209, "invalid handoff target")
        existing = conn.execute(
            """
            SELECT * FROM handoffs
            WHERE task_id = ? AND status = ?
            """,
            (task_id, "pending"),
        ).fetchone()
        if existing:
            return api_error(409, 40930, "handoff already pending")

        handoff_id = generate_handoff_id()
        cursor = conn.execute(
            """
            INSERT INTO handoffs (
                handoff_id, task_id, handoff_type, from_user_id,
                to_user_id, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                task_id,
                payload.handoff_type,
                user["user_id"],
                payload.to_user_id,
                "pending",
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM handoffs WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        record_audit(
            conn,
            "handoff.create",
            user_id=user["user_id"],
            resource_type="handoff",
            resource_id=handoff_id,
            task_id=task_id,
        )
    return api_success(serialize_handoff(row), "handoff created")


@app.post("/api/v1/tasks/{task_id}/qr-tokens")
def create_v1_qr_token(
    task_id: str,
    payload: CreateQrTokenIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    now = datetime.now(timezone.utc).astimezone()
    created_at = now.isoformat(timespec="seconds")
    expires_at_dt = now + timedelta(seconds=payload.ttl_seconds)
    expires_at = expires_at_dt.isoformat(timespec="seconds")
    token = generate_qr_token()
    nonce = secrets.token_hex(12)

    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row:
            return api_error(404, 40401, "task not found")
        if not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        handoff = conn.execute(
            """
            SELECT * FROM handoffs
            WHERE handoff_id = ? AND task_id = ?
            """,
            (payload.handoff_id, task_id),
        ).fetchone()
        if not handoff:
            return api_error(404, 40405, "handoff not found")
        handoff_data = serialize_handoff(handoff)
        if handoff_data["status"] != "pending":
            return api_error(409, 40931, "handoff state conflict")
        if handoff_data["from_user_id"] != user["user_id"] and user["role"] != "admin":
            return api_error(403, 40301, "forbidden")

        cursor = conn.execute(
            """
            INSERT INTO qr_tokens (
                token_hash, task_id, handoff_id, action, issuer_user_id,
                nonce, status, expires_at, consumed_at, revoked_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hash_qr_token(token),
                task_id,
                payload.handoff_id,
                payload.action,
                user["user_id"],
                nonce,
                "active",
                expires_at,
                None,
                None,
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM qr_tokens WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        record_audit(
            conn,
            "qr.create",
            user_id=user["user_id"],
            resource_type="qr_token",
            resource_id=str(cursor.lastrowid),
            task_id=task_id,
        )

    return api_success(
        {
            "token_id": row["id"],
            "handoff_id": row["handoff_id"],
            "token": token,
            "expires_at": row["expires_at"],
            "ttl_seconds": payload.ttl_seconds,
            "refresh_after": min(45, max(1, payload.ttl_seconds - 15)),
            "qr_payload": f"coldchain://handoff?token={token}",
            "qr_image_data_url": qr_image_data_url(
                f"coldchain://handoff?token={token}"
            ),
        },
        "qr token created",
    )


@app.post("/api/v1/qr-tokens/verify")
def verify_v1_qr_token(
    payload: VerifyQrTokenIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    timestamp = now_iso()
    token_hash = hash_qr_token(payload.token)
    with get_connection() as conn:
        rate_error = check_rate_limit(
            conn,
            "qr.verify",
            str(user["user_id"]),
            VERIFY_RATE_LIMIT,
        )
        if rate_error:
            return rate_error
        row = conn.execute(
            "SELECT * FROM qr_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return api_error(410, 41010, "qr token invalid")
        qr = serialize_qr_token(row)
        handoff = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (qr["handoff_id"],),
        ).fetchone()
        if (
            not handoff
            or handoff["status"] != "pending"
            or (handoff["to_user_id"] != user["user_id"] and user["role"] != "admin")
        ):
            return api_error(403, 40301, "forbidden")
        now_dt = datetime.now(timezone.utc).astimezone()
        expires_at = datetime.fromisoformat(qr["expires_at"])
        if (
            qr["status"] == "consumed"
            and qr.get("consumed_at")
            and qr.get("verified_by_user_id") == user["user_id"]
            and not qr.get("revoked_at")
        ):
            return api_success(
                {
                    "valid": True,
                    "idempotent": True,
                    "token_id": qr["id"],
                    "task_id": qr["task_id"],
                    "handoff_id": qr["handoff_id"],
                    "action": qr["action"],
                },
                "qr token already verified by current recipient",
            )
        if (
            qr["status"] != "active"
            or qr.get("consumed_at")
            or qr.get("revoked_at")
            or expires_at < now_dt
        ):
            return api_error(410, 41010, "qr token expired or used")

        conn.execute(
            """
            UPDATE qr_tokens
            SET status = ?, consumed_at = ?, verified_by_user_id = ?
            WHERE id = ?
            """,
            ("consumed", timestamp, user["user_id"], qr["id"]),
        )
        record_audit(
            conn,
            "qr.verify",
            user_id=user["user_id"],
            resource_type="qr_token",
            resource_id=str(qr["id"]),
            task_id=qr["task_id"],
        )

    return api_success(
        {
            "valid": True,
            "token_id": qr["id"],
            "task_id": qr["task_id"],
            "handoff_id": qr["handoff_id"],
            "action": qr["action"],
        },
        "qr token verified",
    )


@app.post("/api/v1/qr-tokens/{token_id}/revoke")
def revoke_v1_qr_token(
    token_id: int,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM qr_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40406, "qr token not found")
        qr = serialize_qr_token(row)
        if qr["status"] != "active" or qr.get("consumed_at"):
            return api_error(410, 41010, "qr token expired or used")
        task_row = get_task_by_id(conn, qr["task_id"])
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40406, "qr token not found")
        conn.execute(
            """
            UPDATE qr_tokens
            SET status = ?, revoked_at = ?
            WHERE id = ?
            """,
            ("revoked", timestamp, token_id),
        )
        updated = conn.execute(
            "SELECT * FROM qr_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        record_audit(
            conn,
            "qr.revoke",
            user_id=user["user_id"],
            resource_type="qr_token",
            resource_id=str(token_id),
            task_id=qr["task_id"],
        )
    return api_success(serialize_qr_token(updated), "qr token revoked")


@app.post("/api/v1/face/enroll")
def enroll_v1_face(
    payload: FaceEnrollIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if not payload.consent:
        return api_error(400, 40020, "face consent required")
    timestamp = now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO face_profiles (
                user_id, template_id, consent_at, quality_score,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                template_id = excluded.template_id,
                consent_at = excluded.consent_at,
                quality_score = excluded.quality_score,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                user["user_id"],
                payload.template_id,
                timestamp,
                payload.quality_score,
                "active",
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM face_profiles WHERE user_id = ?",
            (user["user_id"],),
        ).fetchone()
        record_audit(
            conn,
            "face.enroll",
            user_id=user["user_id"],
            resource_type="face_profile",
            resource_id=str(row["id"]),
        )
    return api_success(serialize_face_profile(row), "face enrolled")


@app.get("/api/v1/face/profile")
def get_v1_face_profile(authorization: Optional[str] = Header(None)):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM face_profiles WHERE user_id = ? AND status = ?",
            (user["user_id"], "active"),
        ).fetchone()
    return api_success(serialize_face_profile(row))


@app.delete("/api/v1/face/profile")
def delete_v1_face_profile(authorization: Optional[str] = Header(None)):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE face_profiles
            SET status = ?, updated_at = ?
            WHERE user_id = ?
            """,
            ("deleted", timestamp, user["user_id"]),
        )
        record_audit(
            conn,
            "face.delete",
            user_id=user["user_id"],
            resource_type="face_profile",
        )
    return api_success({"deleted": True}, "face profile deleted")


@app.post("/api/v1/face/verify")
def verify_v1_face(
    payload: FaceVerifyIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    threshold = 0.8
    verified = payload.liveness_passed and payload.similarity_score >= threshold
    manual_review_required = not verified
    verification_id = generate_verification_id()
    location = payload.location
    with get_connection() as conn:
        rate_error = check_rate_limit(
            conn,
            "face.verify",
            str(user["user_id"]),
            VERIFY_RATE_LIMIT,
        )
        if rate_error:
            return rate_error
        profile = conn.execute(
            "SELECT * FROM face_profiles WHERE user_id = ? AND status = ?",
            (user["user_id"], "active"),
        ).fetchone()
        if not profile:
            return api_error(400, 40021, "face profile not enrolled")
        handoff = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (payload.handoff_id,),
        ).fetchone()
        if not handoff:
            return api_error(404, 40405, "handoff not found")
        if handoff["to_user_id"] != user["user_id"] and user["role"] != "admin":
            return api_error(403, 40301, "forbidden")
        qr_verified = conn.execute(
            """
            SELECT id FROM qr_tokens
            WHERE handoff_id = ? AND status = ? AND consumed_at IS NOT NULL
              AND verified_by_user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (payload.handoff_id, "consumed", user["user_id"]),
        ).fetchone()
        if not qr_verified:
            return api_error(409, 40932, "verified qr token required")
        conn.execute(
            """
            INSERT INTO face_verifications (
                verification_id, user_id, handoff_id, qr_token,
                capture_file_id, liveness_token, liveness_passed,
                similarity_score, threshold, verified,
                manual_review_required, location_lat, location_lng,
                location_accuracy, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                user["user_id"],
                payload.handoff_id,
                None,
                payload.capture_file_id,
                None,
                int(payload.liveness_passed),
                payload.similarity_score,
                threshold,
                int(verified),
                int(manual_review_required),
                location.lat if location else None,
                location.lng if location else None,
                location.accuracy if location else None,
                "verified" if verified else "pending_review",
                timestamp,
            ),
        )
        if manual_review_required:
            conn.execute(
                """
                INSERT INTO face_reviews (
                    verification_id, reviewer_user_id, status,
                    decision_at, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (verification_id, None, "pending_review", None, timestamp),
            )
        row = conn.execute(
            "SELECT * FROM face_verifications WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
        record_audit(
            conn,
            "face.verify",
            user_id=user["user_id"],
            resource_type="face_verification",
            resource_id=verification_id,
            task_id=handoff["task_id"],
        )
    return api_success(serialize_face_verification(row), "face verified")


@app.get("/api/v1/admin/face-reviews")
def list_v1_admin_face_reviews(
    authorization: Optional[str] = Header(None),
    limit: int = Query(default=100, ge=1, le=200),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM face_reviews
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return api_success(
        {"limit": limit, "items": [serialize_face_review(row) for row in rows]}
    )


@app.post("/api/v1/admin/face-reviews/{review_id}/approve")
def approve_v1_admin_face_review(
    review_id: int,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    timestamp = now_iso()
    with get_connection() as conn:
        review = conn.execute(
            "SELECT * FROM face_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if not review:
            return api_error(404, 40409, "face review not found")
        conn.execute(
            """
            UPDATE face_reviews
            SET status = ?, reviewer_user_id = ?, decision_at = ?
            WHERE id = ?
            """,
            ("approved", user["user_id"], timestamp, review_id),
        )
        conn.execute(
            """
            UPDATE face_verifications
            SET status = ?, verified = ?, manual_review_required = ?
            WHERE verification_id = ?
            """,
            ("verified", 1, 0, review["verification_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM face_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        record_audit(
            conn,
            "face_review.approve",
            user_id=user["user_id"],
            resource_type="face_review",
            resource_id=str(review_id),
        )
    return api_success(serialize_face_review(updated), "face review approved")


@app.post("/api/v1/admin/face-reviews/{review_id}/reject")
def reject_v1_admin_face_review(
    review_id: int,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    timestamp = now_iso()
    with get_connection() as conn:
        review = conn.execute(
            "SELECT * FROM face_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if not review:
            return api_error(404, 40409, "face review not found")
        conn.execute(
            """
            UPDATE face_reviews
            SET status = ?, reviewer_user_id = ?, decision_at = ?
            WHERE id = ?
            """,
            ("rejected", user["user_id"], timestamp, review_id),
        )
        conn.execute(
            """
            UPDATE face_verifications
            SET status = ?, verified = ?, manual_review_required = ?
            WHERE verification_id = ?
            """,
            ("rejected", 0, 0, review["verification_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM face_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        record_audit(
            conn,
            "face_review.reject",
            user_id=user["user_id"],
            resource_type="face_review",
            resource_id=str(review_id),
        )
    return api_success(serialize_face_review(updated), "face review rejected")


@app.post("/api/v1/files/upload")
async def upload_v1_file(
    task_id: str = Form(...),
    usage: str = Form(...),
    related_type: Optional[str] = Form(default=None),
    related_id: Optional[str] = Form(default=None),
    expected_sha256: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    content_type = (file.content_type or "").lower()
    allowed_extensions = ALLOWED_EVIDENCE_TYPES.get(content_type)
    original_name = Path(file.filename or "evidence").name
    extension = Path(original_name).suffix.lower()
    if not allowed_extensions or extension not in allowed_extensions:
        return api_error(415, 41501, "unsupported evidence file type")
    content = await file.read(MAX_EVIDENCE_FILE_SIZE + 1)
    await file.close()
    if len(content) > MAX_EVIDENCE_FILE_SIZE:
        return api_error(413, 41301, "evidence file too large")
    if not content or not valid_file_signature(content_type, content):
        return api_error(415, 41502, "invalid evidence file content")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and not secrets.compare_digest(
        digest,
        expected_sha256.strip().lower(),
    ):
        return api_error(409, 40950, "file sha256 mismatch")
    usage = usage.strip()
    if not usage or len(usage) > 80:
        return api_error(422, 42210, "invalid file usage")

    timestamp = now_iso()
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        if related_type == "handoff":
            handoff = conn.execute(
                """
                SELECT id FROM handoffs
                WHERE handoff_id = ? AND task_id = ?
                """,
                (related_id, task_id),
            ).fetchone()
            if not handoff:
                return api_error(404, 40405, "handoff not found")
        duplicate = conn.execute(
            """
            SELECT * FROM files
            WHERE task_id = ? AND sha256 = ? AND usage = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, digest, usage),
        ).fetchone()
        if duplicate:
            item = serialize_file(duplicate)
            item["download_url"] = f"/api/v1/files/{item['file_id']}/download"
            return api_success(item, "file already exists")

        file_id = generate_file_id()
        FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{file_id}{extension}"
        storage_path = FILE_STORAGE_DIR / stored_name
        try:
            storage_path.write_bytes(content)
            cursor = conn.execute(
                """
                INSERT INTO files (
                    file_id, task_id, file_name, file_type, file_size,
                    sha256, usage, related_type, related_id, storage_url,
                    uploader_user_id, created_at, storage_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    task_id,
                    original_name,
                    content_type,
                    len(content),
                    digest,
                    usage,
                    related_type,
                    related_id,
                    None,
                    user["user_id"],
                    timestamp,
                    str(storage_path),
                ),
            )
            row = conn.execute(
                "SELECT * FROM files WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            record_audit(
                conn,
                "file.upload",
                user_id=user["user_id"],
                resource_type="file",
                resource_id=file_id,
                task_id=task_id,
                detail=usage,
            )
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise

    item = serialize_file(row)
    item["download_url"] = f"/api/v1/files/{file_id}/download"
    return api_success(item, "file uploaded")


@app.post("/api/v1/files")
def create_v1_file(
    payload: CreateFileIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()

    with get_connection() as conn:
        task_row = get_task_by_id(conn, payload.task_id)
        if not task_row:
            return api_error(404, 40401, "task not found")
        if not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")

        file_id = generate_file_id()
        cursor = conn.execute(
            """
            INSERT INTO files (
                file_id, task_id, file_name, file_type, file_size,
                sha256, usage, related_type, related_id, storage_url,
                uploader_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                payload.task_id,
                payload.file_name,
                payload.file_type,
                payload.file_size,
                payload.sha256.lower(),
                payload.usage,
                payload.related_type,
                payload.related_id,
                payload.storage_url,
                user["user_id"],
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM files WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        record_audit(
            conn,
            "file.create",
            user_id=user["user_id"],
            resource_type="file",
            resource_id=file_id,
            task_id=payload.task_id,
            detail=payload.usage,
        )
    return api_success(serialize_file(row), "file recorded")


@app.get("/api/v1/files/{file_id}")
def get_v1_file(
    file_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40407, "file not found")
        item = serialize_file(row)
        task_row = get_task_by_id(conn, item["task_id"])
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40407, "file not found")

    item["download_url"] = item["storage_url"] or f"/api/v1/files/{file_id}/download"
    item["expires_in"] = 300
    return api_success(item)


@app.get("/api/v1/files/{file_id}/download")
def download_v1_file(
    file_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40407, "file not found")
        raw = row_to_dict(row)
        task = get_task_by_id(conn, raw["task_id"])
        if not task or not can_view_task(user, serialize_task(task)):
            return api_error(404, 40407, "file not found")
        storage_path_value = raw.get("storage_path")
        if not storage_path_value:
            return api_error(404, 40410, "file content not stored")
        storage_path = Path(storage_path_value).resolve()
        storage_root = FILE_STORAGE_DIR.resolve()
        if storage_root not in storage_path.parents or not storage_path.is_file():
            return api_error(404, 40410, "file content not stored")
        record_audit(
            conn,
            "file.download",
            user_id=user["user_id"],
            resource_type="file",
            resource_id=file_id,
            task_id=raw["task_id"],
        )
        file_name = raw["file_name"]
        media_type = raw["file_type"]
    return FileResponse(
        path=storage_path,
        media_type=media_type,
        filename=file_name,
    )


@app.get("/api/v1/tasks/{task_id}/handoffs")
def list_v1_task_handoffs(
    task_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task = get_task_by_id(conn, task_id)
        if not task or not can_view_task(user, serialize_task(task)):
            return api_error(404, 40401, "task not found")
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM handoffs WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
        rows = conn.execute(
            """
            SELECT * FROM handoffs
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (task_id, page_size, (page - 1) * page_size),
        ).fetchall()
        items = [serialize_handoff_detail(conn, row) for row in rows]
    return api_success(
        {"items": items, "page": page, "page_size": page_size, "total": total}
    )


@app.get("/api/v1/handoffs/{handoff_id}")
def get_v1_handoff(
    handoff_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40405, "handoff not found")
        handoff = serialize_handoff(row)
        task_row = get_task_by_id(conn, handoff["task_id"])
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40405, "handoff not found")
        detail = serialize_handoff_detail(conn, row)
    return api_success(detail)


@app.post("/api/v1/handoffs/{handoff_id}/confirm")
def confirm_v1_handoff(
    handoff_id: str,
    payload: HandoffConfirmIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40405, "handoff not found")
        handoff = serialize_handoff(row)
        if handoff["status"] != "pending":
            return api_error(409, 40931, "handoff state conflict")
        if handoff["to_user_id"] != user["user_id"] and user["role"] != "admin":
            return api_error(403, 40301, "forbidden")
        qr_evidence = conn.execute(
            """
            SELECT * FROM qr_tokens
            WHERE handoff_id = ? AND status = ? AND consumed_at IS NOT NULL
              AND verified_by_user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (handoff_id, "consumed", handoff["to_user_id"]),
        ).fetchone()
        if not qr_evidence:
            return api_error(409, 40932, "verified qr token required")
        task_row = get_task_by_id(conn, handoff["task_id"])
        previous_status = (
            canonical_task_status(row_to_dict(task_row)) if task_row else None
        )
        next_task_status = (
            "arrived"
            if handoff["handoff_type"] == "carrier_to_receiver"
            else "in_transit"
        )

        certificate_no = f"HO-CERT-{handoff['id']:06d}"
        trace_hash = build_trace_hash(
            handoff["handoff_id"],
            handoff["task_id"],
            handoff["from_user_id"],
            handoff["to_user_id"],
            timestamp,
        )
        location = payload.location
        conn.execute(
            """
            UPDATE handoffs
            SET status = ?, location_lat = ?, location_lng = ?,
                location_accuracy = ?, certificate_no = ?, trace_hash = ?,
                confirmed_at = ?, updated_at = ?
            WHERE handoff_id = ?
            """,
            (
                "confirmed",
                location.lat if location else None,
                location.lng if location else None,
                location.accuracy if location else None,
                certificate_no,
                trace_hash,
                timestamp,
                timestamp,
                handoff_id,
            ),
        )
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, started_at = COALESCE(started_at, ?),
                arrived_at = CASE WHEN ? = 'arrived' THEN ? ELSE arrived_at END,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                next_task_status,
                timestamp,
                next_task_status,
                timestamp,
                timestamp,
                handoff["task_id"],
            ),
        )
        if previous_status != next_task_status:
            record_status_history(
                conn,
                task_id=handoff["task_id"],
                from_status=previous_status,
                to_status=next_task_status,
                reason="handoff confirmed",
                actor_user_id=user["user_id"],
                changed_at=timestamp,
            )
        updated = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        record_audit(
            conn,
            "handoff.confirm",
            user_id=user["user_id"],
            resource_type="handoff",
            resource_id=handoff_id,
            task_id=handoff["task_id"],
        )

    result = serialize_handoff(updated)
    result["task_status"] = next_task_status
    result["current_custodian"] = result["to_user_id"]
    result["handoff_certificate_no"] = result["certificate_no"]
    return api_success(result, "handoff confirmed")


@app.post("/api/v1/handoffs/{handoff_id}/reject")
def reject_v1_handoff(
    handoff_id: str,
    payload: HandoffRejectIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40405, "handoff not found")
        handoff = serialize_handoff(row)
        if handoff["status"] != "pending":
            return api_error(409, 40931, "handoff state conflict")
        if handoff["to_user_id"] != user["user_id"] and user["role"] != "admin":
            return api_error(403, 40301, "forbidden")
        conn.execute(
            """
            UPDATE handoffs
            SET status = ?, reason = ?, rejected_at = ?, updated_at = ?
            WHERE handoff_id = ?
            """,
            ("rejected", payload.reason, timestamp, timestamp, handoff_id),
        )
        updated = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        record_audit(
            conn,
            "handoff.reject",
            user_id=user["user_id"],
            resource_type="handoff",
            resource_id=handoff_id,
            task_id=handoff["task_id"],
            detail=payload.reason,
        )
    return api_success(serialize_handoff(updated), "handoff rejected")


@app.get("/api/v1/tasks/{task_id}/telemetry/latest")
def get_v1_latest_telemetry(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        row = conn.execute(
            """
            SELECT * FROM device_data
            WHERE task_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not row and task.get("device_id"):
            row = conn.execute(
                """
                SELECT * FROM device_data
                WHERE device_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (task["device_id"],),
            ).fetchone()
    if row:
        return api_success(normalize_telemetry(row))
    live_latest, _ = live_telemetry_for_task(task)
    return api_success(normalize_telemetry(live_latest) if live_latest else None)


@app.get("/api/v1/tasks/{task_id}/telemetry/history")
def get_v1_telemetry_history(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    cursor: Optional[int] = Query(default=None, ge=1),
    downsample: int = Query(default=1, ge=1, le=60),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    start_time = normalize_query_time(start_time)
    end_time = normalize_query_time(end_time)
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        conditions = ["task_id = ?"]
        params: list[object] = [task_id]
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if cursor:
            conditions.append("id < ?")
            params.append(cursor)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT * FROM device_data
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        if not rows and task.get("device_id") and not cursor:
            device_conditions = ["device_id = ?"]
            device_params: list[object] = [task["device_id"]]
            if start_time:
                device_conditions.append("timestamp >= ?")
                device_params.append(start_time)
            if end_time:
                device_conditions.append("timestamp <= ?")
                device_params.append(end_time)
            device_params.append(limit)
            rows = conn.execute(
                f"""
                SELECT * FROM device_data
                WHERE {' AND '.join(device_conditions)}
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                device_params,
            ).fetchall()
    items = [normalize_telemetry(row) for index, row in enumerate(rows)]
    if not items and not cursor:
        _, live_history = live_telemetry_for_task(task)
        items = [normalize_telemetry(item) for item in live_history[:limit]]
        if start_time:
            items = [item for item in items if str(item.get("timestamp") or "") >= start_time]
        if end_time:
            items = [item for item in items if str(item.get("timestamp") or "") <= end_time]
    if downsample > 1:
        items = [item for index, item in enumerate(items) if index % downsample == 0]
    return api_success(
        {
            "limit": limit,
            "start_time": start_time,
            "end_time": end_time,
            "cursor": cursor,
            "next_cursor": rows[-1]["id"] if rows else None,
            "downsample": downsample,
            "items": items,
        }
    )


@app.get("/api/v1/tasks/{task_id}/alarms")
def get_v1_alarms(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        device_id = str(task.get("device_id") or "")

        live_items: list[dict] = []
        if device_id:
            try:
                live_items = fetch_live_device_events(max(limit, 100))
            except (
                urllib.error.URLError,
                TimeoutError,
                ValueError,
                json.JSONDecodeError,
                RuntimeError,
            ):
                live_items = []
            try:
                snapshot = fetch_live_hardware_snapshot()
                for item in snapshot.get("recent_alarms") or []:
                    if isinstance(item, dict):
                        live_items.append(item)
            except RuntimeError:
                pass
            sync_live_alarms_to_local(
                conn,
                task_id=task_id,
                device_id=device_id,
                live_items=live_items,
                task=task,
            )

        # 公网事件同步进本地后，与本地入库告警一并返回（处置走本地逻辑）
        rows = conn.execute(
            """
            SELECT * FROM event_log
            WHERE task_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
        custody = task_custody_owner(task)
        items = []
        for row in rows:
            item = normalize_event(row)
            item["source"] = item.get("source") or "local"
            if item.get("responsible_user_id") is None:
                item["responsible_user_id"] = custody.get("user_id")
                item["responsible_role"] = custody.get("role")
            role = item.get("responsible_role") or custody.get("role")
            if role == "carrier":
                item["responsible_label"] = "承运"
                item["responsible_name"] = task.get("carrier") or "承运方"
            elif role == "receiver":
                item["responsible_label"] = "接收"
                item["responsible_name"] = task.get("receiver") or "接收方"
            else:
                item["responsible_label"] = "发货"
                item["responsible_name"] = task.get("sender") or "发货方"
            item["can_handle"] = can_handle_alarm(user, task, item)
            items.append(item)

    return api_success(
        {
            "limit": limit,
            "device_id": device_id or None,
            "items": items,
            "source": {
                "local": sum(1 for item in items if item.get("source") != "live"),
                "live": sum(1 for item in items if item.get("source") == "live"),
            },
        }
    )


@app.post("/api/v1/alarms/{alarm_id}/ack")
def ack_v1_alarm(
    alarm_id: int,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40403, "alarm not found")
        alarm = normalize_event(row)
        task_row = get_task_by_id(conn, alarm["task_id"])
        if not task_row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        if not can_view_task(user, task):
            return api_error(404, 40403, "alarm not found")
        if alarm.get("responsible_user_id") is None:
            custody = task_custody_owner(task)
            conn.execute(
                """
                UPDATE event_log
                SET responsible_user_id = ?, responsible_role = ?
                WHERE id = ? AND responsible_user_id IS NULL
                """,
                (custody.get("user_id"), custody.get("role"), alarm_id),
            )
            alarm["responsible_user_id"] = custody.get("user_id")
            alarm["responsible_role"] = custody.get("role")
        if not can_handle_alarm(user, task, alarm):
            custody = task_custody_owner(task)
            return api_error(
                403,
                40301,
                f"仅责任人可处置（{custody.get('label')} · {custody.get('name')}）",
            )
        conn.execute(
            """
            UPDATE event_log
            SET alarm_status = ?, acknowledged_at = ?
            WHERE id = ?
            """,
            ("acknowledged", timestamp, alarm_id),
        )
        record_audit(
            conn,
            "alarm.ack",
            user_id=user["user_id"],
            resource_type="alarm",
            resource_id=str(alarm_id),
            task_id=row["task_id"],
        )
        updated = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
    return api_success(normalize_event(updated), "alarm acknowledged")


@app.post("/api/v1/alarms/{alarm_id}/resolve")
def resolve_v1_alarm(
    alarm_id: int,
    payload: ResolveAlarmIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40403, "alarm not found")
        alarm = normalize_event(row)
        task_row = get_task_by_id(conn, alarm["task_id"])
        if not task_row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        if not can_view_task(user, task):
            return api_error(404, 40403, "alarm not found")
        if alarm.get("responsible_user_id") is None:
            custody = task_custody_owner(task)
            conn.execute(
                """
                UPDATE event_log
                SET responsible_user_id = ?, responsible_role = ?
                WHERE id = ? AND responsible_user_id IS NULL
                """,
                (custody.get("user_id"), custody.get("role"), alarm_id),
            )
            alarm["responsible_user_id"] = custody.get("user_id")
            alarm["responsible_role"] = custody.get("role")
        if not can_handle_alarm(user, task, alarm):
            custody = task_custody_owner(task)
            return api_error(
                403,
                40301,
                f"仅责任人可处置（{custody.get('label')} · {custody.get('name')}）",
            )
        conn.execute(
            """
            UPDATE event_log
            SET alarm_status = ?, acknowledged_at = COALESCE(acknowledged_at, ?),
                resolved_at = ?, resolution = ?
            WHERE id = ?
            """,
            ("resolved", timestamp, timestamp, payload.resolution, alarm_id),
        )
        record_audit(
            conn,
            "alarm.resolve",
            user_id=user["user_id"],
            resource_type="alarm",
            resource_id=str(alarm_id),
            task_id=row["task_id"],
        )
        updated = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
    return api_success(normalize_event(updated), "alarm resolved")


@app.get("/api/v1/admin/audit-logs")
def list_v1_audit_logs(
    authorization: Optional[str] = Header(None),
    limit: int = Query(default=100, ge=1, le=200),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return api_success(
        {"limit": limit, "items": [serialize_audit_log(row) for row in rows]}
    )


@app.get("/api/v1/admin/users")
def list_v1_admin_users(
    authorization: Optional[str] = Header(None),
    limit: int = Query(default=100, ge=1, le=200),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return api_success({"limit": limit, "items": [serialize_user(row) for row in rows]})


@app.patch("/api/v1/admin/users/{user_id}/status")
def update_v1_admin_user_status(
    user_id: int,
    payload: UpdateUserStatusIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    status = payload.status.strip().lower()
    if status not in {"active", "disabled"}:
        return api_error(422, 42205, "invalid user status")
    with get_connection() as conn:
        row = get_user_row(conn, user_id)
        if not row:
            return api_error(404, 40404, "user not found")
        conn.execute(
            "UPDATE users SET status = ? WHERE id = ?",
            (status, user_id),
        )
        if status != "active":
            conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
        updated = get_user_row(conn, user_id)
        record_audit(
            conn,
            "admin.user_status",
            user_id=user["user_id"],
            resource_type="user",
            resource_id=str(user_id),
            detail=status,
        )
    return api_success(serialize_user(updated), "user status updated")


@app.get("/api/v1/admin/tasks")
def list_v1_admin_tasks(
    authorization: Optional[str] = Header(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM task_handoff").fetchone()[
            "count"
        ]
        rows = conn.execute(
            """
            SELECT * FROM task_handoff
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, (page - 1) * page_size),
        ).fetchall()
        items = [enrich_task(row, conn) for row in rows]
    return api_success(
        {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


@app.get("/api/v1/dashboard/summary")
def get_v1_dashboard_summary(
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if user["role"] != "admin":
        return api_error(403, 40301, "forbidden")
    scan_offline_devices()
    today = (
        datetime.now(timezone.utc)
        .astimezone()
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        .isoformat(timespec="seconds")
    )
    with get_connection() as conn:
        task_rows = conn.execute("SELECT * FROM task_handoff").fetchall()
        task_statuses = [canonical_task_status(row_to_dict(row)) for row in task_rows]
        status_distribution = {
            status: task_statuses.count(status) for status in TASK_STATUSES
        }
        active_tasks = sum(
            1 for status in task_statuses if status in {"in_transit", "arrived"}
        )
        abnormal_tasks = conn.execute(
            """
            SELECT COUNT(DISTINCT task_id) AS count
            FROM event_log
            WHERE COALESCE(alarm_status, 'new') != ?
            """,
            ("resolved",),
        ).fetchone()["count"]
        device_rows = conn.execute("SELECT * FROM devices").fetchall()
        device_statuses = [serialize_device(row)["status"] for row in device_rows]
        online_devices = sum(
            1 for status in device_statuses if status in {"online", "bound"}
        )
        offline_devices = device_statuses.count("offline")
        today_alarm_count = conn.execute(
            "SELECT COUNT(*) AS count FROM event_log WHERE created_at >= ?",
            (today,),
        ).fetchone()["count"]
        alarm_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM event_log
            WHERE created_at >= ?
            GROUP BY event_type
            """,
            (today,),
        ).fetchall()
    return api_success(
        {
            "active_tasks": active_tasks,
            "abnormal_tasks": abnormal_tasks,
            "online_devices": online_devices,
            "offline_devices": offline_devices,
            "today_alarm_count": today_alarm_count,
            "status_distribution": status_distribution,
            "alarm_distribution": {
                row["event_type"]: row["count"] for row in alarm_rows
            },
            "updated_at": now_iso(),
        }
    )


@app.get("/api/v1/notifications")
def list_v1_notifications(
    authorization: Optional[str] = Header(None),
    limit: int = Query(default=100, ge=1, le=200),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM notifications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user["user_id"], limit),
        ).fetchall()
    return api_success(
        {"limit": limit, "items": [serialize_notification(row) for row in rows]}
    )


@app.post("/api/v1/notifications/{notification_id}/read")
def read_v1_notification(
    notification_id: int,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM notifications
            WHERE id = ? AND user_id = ?
            """,
            (notification_id, user["user_id"]),
        ).fetchone()
        if not row:
            return api_error(404, 40408, "notification not found")
        conn.execute(
            """
            UPDATE notifications
            SET is_read = ?, read_at = COALESCE(read_at, ?)
            WHERE id = ?
            """,
            (1, timestamp, notification_id),
        )
        updated = conn.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
    return api_success(serialize_notification(updated), "notification read")


def task_state_conflict():
    return api_error(409, 40901, "task state conflict")


@app.post("/api/v1/tasks/{task_id}/start")
def start_v1_task(
    task_id: str,
    idempotency_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:start"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if not can_modify_task(user, task):
            return api_error(404, 40401, "task not found")
        if not is_seed_demo_task(task_id) and (
            task["status"] != "pending_handoff"
            or not task.get("carrier_user_id")
            or not task.get("receiver_user_id")
        ):
            return api_error(409, 40902, "task handoff prerequisites incomplete")
        if canonical_task_status(row_to_dict(row)) not in {
            "pending_pack",
            "pending_handoff",
        }:
            return task_state_conflict()
        previous_status = canonical_task_status(row_to_dict(row))
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, started_at = ?, signed_at = NULL,
                arrived_at = NULL, rejected_at = NULL,
                rejection_reason = NULL, updated_at = ?
            WHERE task_id = ?
            """,
            ("in_transit", timestamp, timestamp, task_id),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=previous_status,
            to_status="in_transit",
            reason="task started",
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.start",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
        )
        updated = get_task_by_id(conn, task_id)
        data = enrich_task(updated, conn)
        save_idempotent_result(conn, scope, idempotency_key, data, "task started")
    return api_success(data, "task started")


@app.post("/api/v1/tasks/{task_id}/arrive")
def arrive_v1_task(
    task_id: str,
    idempotency_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:arrive"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if user["role"] != "admin" and task.get("carrier_user_id") != user["user_id"]:
            return api_error(404, 40401, "task not found")
        previous_status = canonical_task_status(row_to_dict(row))
        if previous_status != "in_transit":
            return task_state_conflict()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, arrived_at = ?, updated_at = ?
            WHERE task_id = ?
            """,
            ("arrived", timestamp, timestamp, task_id),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=previous_status,
            to_status="arrived",
            reason="task arrived",
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.arrive",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
        )
        updated = get_task_by_id(conn, task_id)
        data = enrich_task(updated, conn)
        save_idempotent_result(conn, scope, idempotency_key, data, "task arrived")
    return api_success(data, "task arrived")


@app.post("/api/v1/tasks/{task_id}/sign")
def sign_v1_task(
    task_id: str,
    idempotency_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:sign"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if user["role"] != "admin" and task.get("receiver_user_id") != user["user_id"]:
            return api_error(404, 40401, "task not found")
        if not is_seed_demo_task(task_id):
            confirmed_handoff = conn.execute(
                """
                SELECT id FROM handoffs
                WHERE task_id = ? AND handoff_type = ? AND to_user_id = ?
                  AND status = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    task_id,
                    "carrier_to_receiver",
                    task.get("receiver_user_id"),
                    "confirmed",
                ),
            ).fetchone()
            if not confirmed_handoff:
                return api_error(409, 40933, "receiver handoff evidence required")
        previous_status = canonical_task_status(row_to_dict(row))
        if previous_status not in {"in_transit", "arrived"}:
            return task_state_conflict()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, signed_at = ?, updated_at = ?
            WHERE task_id = ?
            """,
            ("signed", timestamp, timestamp, task_id),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=previous_status,
            to_status="signed",
            reason="task signed",
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.sign",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
        )
        updated = get_task_by_id(conn, task_id)
        data = enrich_task(updated, conn)
        save_idempotent_result(conn, scope, idempotency_key, data, "task signed")
    return api_success(data, "task signed")


@app.post("/api/v1/tasks/{task_id}/reject")
def reject_v1_task(
    task_id: str,
    data: RejectTaskIn,
    idempotency_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    reason = data.reason.strip()
    if not reason:
        return api_error(400, 40001, "rejection reason required")

    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:reject"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(row)
        if user["role"] != "admin" and task.get("receiver_user_id") != user["user_id"]:
            return api_error(404, 40401, "task not found")
        if not is_seed_demo_task(task_id):
            qr_evidence = conn.execute(
                """
                SELECT qr_tokens.id
                FROM qr_tokens
                JOIN handoffs ON handoffs.handoff_id = qr_tokens.handoff_id
                WHERE handoffs.task_id = ?
                  AND handoffs.handoff_type = ?
                  AND handoffs.to_user_id = ?
                  AND qr_tokens.status = ?
                  AND qr_tokens.consumed_at IS NOT NULL
                ORDER BY qr_tokens.id DESC
                LIMIT 1
                """,
                (
                    task_id,
                    "carrier_to_receiver",
                    task.get("receiver_user_id"),
                    "consumed",
                ),
            ).fetchone()
            if not qr_evidence:
                return api_error(409, 40933, "receiver handoff evidence required")
        previous_status = canonical_task_status(row_to_dict(row))
        if previous_status not in {"in_transit", "arrived"}:
            return task_state_conflict()
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, signed_at = NULL, rejected_at = ?,
                rejection_reason = ?, updated_at = ?
            WHERE task_id = ?
            """,
            ("rejected", timestamp, reason, timestamp, task_id),
        )
        record_status_history(
            conn,
            task_id=task_id,
            from_status=previous_status,
            to_status="rejected",
            reason=reason,
            actor_user_id=user["user_id"],
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.reject",
            user_id=user["user_id"],
            resource_type="task",
            resource_id=task_id,
            task_id=task_id,
            detail=reason,
        )
        updated = get_task_by_id(conn, task_id)
        result = enrich_task(updated, conn)
        save_idempotent_result(conn, scope, idempotency_key, result, "task rejected")
    return api_success(result, "task rejected")


@app.get("/api/v1/tasks/{task_id}/trace-report")
def get_v1_trace_report(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        report = get_trace_report_data(conn, task_id)
    if not report:
        return api_error(404, 40401, "task not found")
    return api_success(report)


@app.get("/api/v1/tasks/{task_id}/trace-report.pdf")
def get_v1_trace_report_pdf(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        report = get_trace_report_data(conn, task_id)
    if not report:
        return api_error(404, 40401, "task not found")

    task = report["task"]
    summary = report["summary"]
    lines = [
        "Cold Chain Traceability Report",
        f"Task ID: {task['task_id']}",
        f"Sample: {task['sample_name']}",
        f"Status: {task['status']}",
        f"Device: {task.get('device_id')}",
        f"Records: {summary.get('total_records')}",
        f"Events: {summary.get('event_count')}",
        f"Evidence Files: {summary.get('evidence_count')}",
        f"Trace Hash: {report.get('trace_hash')}",
    ]
    content = build_simple_pdf(lines)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{task_id}-trace-report.pdf"'
        },
    )


@app.post("/api/v1/devices/{device_id}/bind-check")
def check_v1_device_binding(
    device_id: str,
    payload: DeviceBindCheckIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if device and not can_manage_device(user, row_to_dict(device)):
            return api_error(404, 40402, "device not found")
    occupied_task_id = device["current_task_id"] if device else None
    return api_success(
        {
            "device_id": device_id,
            "box_id": payload.box_id,
            "seal_id": payload.seal_id,
            "available": not occupied_task_id,
            "occupied_task_id": occupied_task_id,
            "checked_at": now_iso(),
            "message": "设备可绑定" if not occupied_task_id else "设备已绑定其他运单",
        }
    )


@app.get("/api/v1/devices/{device_id}/precheck")
def precheck_v1_device(
    device_id: str,
    min_temp: float = Query(...),
    max_temp: float = Query(...),
    allow_local: bool = Query(False),
    authorization: Optional[str] = Header(None),
):
    init_db()
    if not require_user(authorization):
        return api_error(401, 40102, "unauthorized")
    hardware_telemetry = None
    hardware_error = None
    available_device_ids: list[str] = []
    try:
        snapshot = fetch_live_hardware_snapshot()
        latest_values = (snapshot.get("latest_telemetry_by_task") or {}).values()
        available_device_ids = sorted(
            {
                str(item.get("device_id"))
                for item in latest_values
                if item.get("device_id")
            }
        )
        hardware_telemetry = next(
            (
                item
                for item in (snapshot.get("latest_telemetry_by_task") or {}).values()
                if str(item.get("device_id") or "") == str(device_id)
            ),
            None,
        )
    except RuntimeError as exc:
        hardware_error = str(exc)
    with get_connection() as conn:
        local_row = conn.execute(
            "SELECT * FROM device_data WHERE device_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    selected_local_row = local_row if allow_local else None
    if not hardware_telemetry and not selected_local_row:
        return api_success(
            {
                "device_id": device_id,
                "online": False,
                "passed": False,
                "temperature": None,
                "humidity": None,
                "box_status": None,
                "move_status": None,
                "reported_at": None,
                "source": "none",
                "available_device_ids": available_device_ids,
                "suggested_device_id": (
                    available_device_ids[0] if len(available_device_ids) == 1 else None
                ),
                "reason": (
                    "真实硬件接口暂不可用，未使用本地测试数据"
                    if hardware_error
                    else "真实硬件接口在线，但设备编号没有匹配数据"
                ),
            }
        )
    telemetry = normalize_telemetry(hardware_telemetry or selected_local_row)
    source = "hardware" if hardware_telemetry else "local"
    reported_at = telemetry.get("timestamp") or telemetry.get("created_at")
    age_seconds = None
    try:
        reported_time = datetime.fromisoformat(str(reported_at).replace("Z", "+00:00"))
        if reported_time.tzinfo is None:
            reported_time = reported_time.replace(tzinfo=timezone.utc)
        age_seconds = max(
            0,
            int(
                (
                    datetime.now(timezone.utc)
                    - reported_time.astimezone(timezone.utc)
                ).total_seconds()
            ),
        )
    except (TypeError, ValueError):
        pass
    fresh = age_seconds is not None and age_seconds <= PRECHECK_MAX_DATA_AGE_SECONDS
    passed = (
        fresh
        and min_temp <= telemetry["temperature"] <= max_temp
        and telemetry["box_status"] == "BOX_CLOSED"
    )
    if not fresh:
        reason = (
            f"{'真实硬件' if source == 'hardware' else '本地后端'}数据已过期"
            + (f"（{age_seconds} 秒前）" if age_seconds is not None else "（时间无效）")
        )
    elif passed:
        reason = f"{'真实硬件' if source == 'hardware' else '本地后端'}数据：温度与箱体状态合格"
    else:
        reason = f"{'真实硬件' if source == 'hardware' else '本地后端'}数据：请检查温度与箱体状态"
    return api_success(
        {
            "device_id": device_id,
            "online": fresh,
            "passed": passed,
            "temperature": telemetry["temperature"],
            "humidity": telemetry["humidity"],
            "box_status": telemetry["box_status"],
            "move_status": telemetry["move_status"],
            "reported_at": reported_at,
            "source": source,
            "fresh": fresh,
            "age_seconds": age_seconds,
            "available_device_ids": available_device_ids,
            "suggested_device_id": None,
            "reason": reason,
        }
    )


@app.post("/api/v1/devices/{device_id}/simulate-reading")
def simulate_v1_device_reading(
    device_id: str,
    payload: LocalDeviceReadingIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if not ALLOW_LOCAL_SIMULATION:
        return api_error(403, 40301, "local simulation disabled")
    with get_connection() as conn:
        device_row = conn.execute(
            "SELECT current_task_id FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        target_task_id = payload.task_id or (
            device_row["current_task_id"] if device_row else None
        )
        if target_task_id:
            task_row = get_task_by_id(conn, target_task_id)
            if not task_row or not can_view_task(user, serialize_task(task_row)):
                return api_error(404, 40401, "task not found")
        else:
            target_task_id = f"LOCAL-PRECHECK-{user['user_id']}"
        data = DeviceDataIn(
            device_id=device_id,
            task_id=target_task_id,
            temperature=payload.temperature,
            humidity=payload.humidity,
            light_raw=120,
            box_status="BOX_CLOSED",
            move_status="STABLE",
            temp_status="TEMP_OK",
            acc_total=1.0,
            motion_score=0.0,
        )
        row, _ = save_device_data(conn, data)
    return api_success(normalize_telemetry(row), "local reading created")


@app.post("/api/v1/face/simulate-verify")
def simulate_v1_face(
    payload: FaceSimulateIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if not ALLOW_LOCAL_SIMULATION:
        return api_error(403, 40301, "local simulation disabled")
    timestamp = now_iso()
    with get_connection() as conn:
        handoff = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?", (payload.handoff_id,)
        ).fetchone()
        if not handoff:
            return api_error(404, 40405, "handoff not found")
        if user["role"] != "admin" and user["user_id"] not in {
            handoff["from_user_id"],
            handoff["to_user_id"],
        }:
            return api_error(403, 40301, "forbidden")
        verification_id = generate_verification_id()
        conn.execute(
            """
            INSERT INTO face_verifications (
                verification_id, user_id, handoff_id, liveness_passed,
                similarity_score, threshold, verified, manual_review_required,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                user["user_id"],
                payload.handoff_id,
                1,
                0.99,
                0.8,
                1,
                0,
                "verified",
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM face_verifications WHERE verification_id = ?",
            (verification_id,),
        ).fetchone()
    result = serialize_face_verification(row)
    result.update(
        {
            "face_count": 1,
            "quality_score": result["similarity_score"],
            "party": (
                "issuer"
                if user["user_id"] == handoff["from_user_id"]
                else "recipient"
            ),
        }
    )
    return api_success(result, "local face simulation verified")


@app.get("/api/v1/admin/live-snapshot")
def get_v1_admin_live_snapshot(
    telemetry_limit: int = Query(default=20, ge=1, le=100),
    alarm_limit: int = Query(default=50, ge=1, le=200),
    device_history_limit: int = Query(default=50, ge=1, le=200),
):
    """公网联调只读快照：读取本机 SQLite（8080 机即为硬件上报落库，不做自拉）。"""
    return api_success(
        build_local_hardware_snapshot(
            telemetry_limit=telemetry_limit,
            alarm_limit=alarm_limit,
            device_history_limit=device_history_limit,
        )
    )


@app.get("/api/v1/tasks/{task_id}/hardware/snapshot")
def get_v1_task_hardware_snapshot(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row or not can_view_task(user, serialize_task(task_row)):
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
    try:
        snapshot = fetch_live_hardware_snapshot()
    except RuntimeError as exc:
        return api_error(502, 50201, str(exc))
    requested_device_id = str(task.get("device_id") or "")
    latest_by_task = snapshot.get("latest_telemetry_by_task") or {}
    latest = latest_by_task.get(task_id)
    matched_by: Optional[str] = "task_id" if latest else None
    if not latest and requested_device_id:
        latest = next(
            (
                item
                for item in latest_by_task.values()
                if str(item.get("device_id") or "") == requested_device_id
            ),
            None,
        )
        if latest:
            matched_by = "device_id"
    history_by_task = snapshot.get("telemetry_history_by_task") or {}
    history = history_by_task.get(task_id) or []
    if not history and requested_device_id:
        history = [
            item
            for items in history_by_task.values()
            for item in (items or [])
            if str(item.get("device_id") or "") == requested_device_id
        ]
    alarms = [
        item
        for item in (snapshot.get("recent_alarms") or [])
        if str(item.get("task_id") or "") == task_id
        or (
            requested_device_id
            and str(item.get("device_id") or "") == requested_device_id
        )
    ]
    return api_success(
        {
            "source_url": LIVE_SNAPSHOT_URL,
            "generated_at": snapshot.get("generated_at"),
            "requested_task_id": task_id,
            "requested_device_id": requested_device_id or None,
            "matched": bool(latest),
            "matched_by": matched_by,
            "latest": normalize_telemetry(latest) if latest else None,
            "history": [normalize_telemetry(item) for item in history[:60]],
            "recent_alarms": alarms[:50],
        }
    )


@app.get("/", response_class=HTMLResponse)
def dashboard():
    if not ENABLE_DEMO_DASHBOARD:
        return HTMLResponse("demo dashboard disabled", status_code=404)
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>生物样本冷链转运交接与可信追溯系统</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f7fb; color: #172033; }
    header { background: #17324d; color: white; padding: 20px 28px; }
    h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0; }
    main { max-width: 1220px; margin: 0 auto; padding: 24px; }
    h2 { font-size: 18px; margin: 0 0 12px; color: #22344d; }
    .muted { color: #66748a; }
    .layout { display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 16px; align-items: start; }
    .summary { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .card { background: white; border: 1px solid #dde4ee; border-radius: 8px; padding: 16px; }
    .task-card { margin-bottom: 16px; }
    .task-grid { display: grid; grid-template-columns: repeat(3, minmax(160px, 1fr)); gap: 10px 18px; }
    .label { color: #617089; font-size: 13px; margin-bottom: 6px; }
    .value { font-size: 24px; font-weight: 700; }
    .status { font-size: 18px; }
    .task-status { display: inline-flex; align-items: center; min-height: 34px; padding: 0 12px; border-radius: 6px; background: #edf2f7; color: #17324d; font-weight: 700; }
    .task-status.alert { background: #fee4e2; color: #b42318; }
    .actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
    button { border: 0; border-radius: 6px; background: #17324d; color: white; padding: 10px 14px; font-size: 14px; cursor: pointer; }
    button.secondary { background: #2f6f73; }
    button:active { transform: translateY(1px); }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #dde4ee; border-radius: 8px; overflow: hidden; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e8edf4; text-align: left; font-size: 14px; white-space: nowrap; }
    th { background: #edf2f7; color: #34445a; }
    tr:last-child td { border-bottom: 0; }
    .event { font-weight: 700; color: #b42318; }
    .timeline { display: grid; gap: 10px; }
    .timeline-item { border-left: 3px solid #b42318; padding: 8px 0 8px 12px; background: #fff; }
    .timeline-title { font-weight: 700; color: #b42318; }
    .report-grid { display: grid; grid-template-columns: repeat(2, minmax(130px, 1fr)); gap: 10px; }
    .report-value { font-size: 20px; font-weight: 700; }
    .section { margin-bottom: 16px; }
    @media (max-width: 920px) {
      .layout { grid-template-columns: 1fr; }
      .summary { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
      .task-grid { grid-template-columns: 1fr; }
      main { padding: 14px; }
      table { display: block; overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>生物样本冷链转运交接与可信追溯系统</h1>
    <div class="muted" id="refreshTime">等待数据...</div>
  </header>
  <main>
    <section class="card task-card">
      <h2>转运任务交接</h2>
      <div class="task-grid">
        <div><div class="label">任务编号</div><div class="status" id="taskId">--</div></div>
        <div><div class="label">样本名称</div><div class="status" id="sampleName">--</div></div>
        <div><div class="label">当前状态</div><div class="task-status" id="taskStatus">--</div></div>
        <div><div class="label">发出单位</div><div id="sender">--</div></div>
        <div><div class="label">接收单位</div><div id="receiver">--</div></div>
        <div><div class="label">承运人员</div><div id="carrier">--</div></div>
        <div><div class="label">发出时间</div><div id="startedAt">--</div></div>
        <div><div class="label">签收时间</div><div id="signedAt">--</div></div>
        <div><div class="label">异常事件</div><div id="abnormalCount">--</div></div>
      </div>
      <div class="actions">
        <button onclick="startTask()">发出交接</button>
        <button class="secondary" onclick="signTask()">到达签收</button>
      </div>
    </section>

    <section class="summary">
      <div class="card"><div class="label">最新温度</div><div class="value" id="temperature">--</div></div>
      <div class="card"><div class="label">最新湿度</div><div class="value" id="humidity">--</div></div>
      <div class="card"><div class="label">箱体状态</div><div class="value status" id="boxStatus">--</div></div>
      <div class="card"><div class="label">运动状态</div><div class="value status" id="moveStatus">--</div></div>
      <div class="card"><div class="label">温度状态</div><div class="value status" id="tempStatus">--</div></div>
    </section>

    <div class="layout">
      <div>
        <section class="section">
          <h2>最近数据</h2>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>设备</th>
                <th>任务</th>
                <th>温度</th>
                <th>湿度</th>
                <th>光照</th>
                <th>箱体</th>
                <th>运动</th>
                <th>温度状态</th>
                <th>事件</th>
              </tr>
            </thead>
            <tbody id="historyBody">
              <tr><td colspan="10" class="muted">暂无数据</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <aside>
        <section class="card section">
          <h2>追溯报告</h2>
          <div class="report-grid">
            <div><div class="label">数据条数</div><div class="report-value" id="totalRecords">--</div></div>
            <div><div class="label">异常事件</div><div class="report-value" id="eventCount">--</div></div>
            <div><div class="label">最低温度</div><div class="report-value" id="minTemp">--</div></div>
            <div><div class="label">最高温度</div><div class="report-value" id="maxTemp">--</div></div>
          </div>
        </section>

        <section class="card section">
          <h2>异常事件时间线</h2>
          <div class="timeline" id="eventTimeline">
            <div class="muted">暂无异常事件</div>
          </div>
        </section>
      </aside>
    </div>
  </main>

  <script>
    const STATUS_LABELS = {
      pending_pack: "待发出",
      pending_handoff: "待发出",
      in_transit: "运输中",
      arrived: "已到达",
      signed: "已签收",
      rejected: "已拒收",
      canceled: "已取消"
    };

    function showValue(id, value, suffix = "") {
      document.getElementById(id).textContent = value === undefined || value === null || value === "" ? "--" : value + suffix;
    }

    async function postAction(url) {
      await fetch(url, { method: "POST" });
      await refresh();
    }

    function startTask() {
      postAction("/api/task/start").catch(console.error);
    }

    function signTask() {
      postAction("/api/task/sign").catch(console.error);
    }

    function renderTask(task) {
      showValue("taskId", task.task_id);
      showValue("sampleName", task.sample_name);
      showValue("sender", task.sender);
      showValue("receiver", task.receiver);
      showValue("carrier", task.carrier);
      showValue("startedAt", task.started_at);
      showValue("signedAt", task.signed_at);
      showValue("abnormalCount", task.abnormal_count);
      const status = document.getElementById("taskStatus");
      status.textContent = STATUS_LABELS[task.status] || task.status || "--";
      const isAlert = task.abnormal_count > 0 || task.status === "rejected";
      status.className = isAlert ? "task-status alert" : "task-status";
    }

    function renderHistory(history) {
      const body = document.getElementById("historyBody");
      if (!history.length) {
        body.innerHTML = '<tr><td colspan="10" class="muted">暂无数据</td></tr>';
        return;
      }
      body.innerHTML = history.map(item => `
        <tr>
          <td>${item.timestamp}</td>
          <td>${item.device_id}</td>
          <td>${item.task_id}</td>
          <td>${item.temperature}</td>
          <td>${item.humidity}</td>
          <td>${item.light_raw}</td>
          <td>${item.box_status}</td>
          <td>${item.move_status}</td>
          <td>${item.temp_status}</td>
          <td class="${item.event_type === "NORMAL" ? "" : "event"}">${item.event_display || item.event_type}</td>
        </tr>
      `).join("");
    }

    function renderTimeline(events) {
      const timeline = document.getElementById("eventTimeline");
      if (!events.length) {
        timeline.innerHTML = '<div class="muted">暂无异常事件</div>';
        return;
      }
      timeline.innerHTML = events.slice(0, 12).map(item => `
        <div class="timeline-item">
          <div class="timeline-title">${item.event_name}</div>
          <div>${item.event_detail}</div>
          <div class="muted">${item.timestamp}</div>
        </div>
      `).join("");
    }

    function renderReport(report) {
      const summary = report.summary || {};
      showValue("totalRecords", summary.total_records);
      showValue("eventCount", summary.event_count);
      showValue("minTemp", summary.min_temperature, summary.min_temperature === null ? "" : " °C");
      showValue("maxTemp", summary.max_temperature, summary.max_temperature === null ? "" : " °C");
    }

    async function refresh() {
      const [latestResp, historyResp, eventsResp, taskResp, reportResp] = await Promise.all([
        fetch("/api/device/latest"),
        fetch("/api/device/history"),
        fetch("/api/device/events"),
        fetch("/api/task/current"),
        fetch("/api/task/report")
      ]);
      const latest = await latestResp.json();
      const history = await historyResp.json();
      const events = await eventsResp.json();
      const task = await taskResp.json();
      const report = await reportResp.json();

      showValue("temperature", latest.temperature, latest.temperature === undefined ? "" : " °C");
      showValue("humidity", latest.humidity, latest.humidity === undefined ? "" : " %");
      showValue("boxStatus", latest.box_status);
      showValue("moveStatus", latest.move_status);
      showValue("tempStatus", latest.temp_status);
      document.getElementById("refreshTime").textContent = "最后刷新：" + new Date().toLocaleString();

      renderTask(task);
      renderHistory(history);
      renderTimeline(events);
      renderReport(report);
    }

    refresh().catch(console.error);
    setInterval(() => refresh().catch(console.error), 3000);
  </script>
</body>
</html>
"""


@app.get("/home", response_class=HTMLResponse)
def dashboard_home() -> str:
    """兼容入口 http://host:port/home 。"""
    return dashboard()
