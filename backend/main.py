from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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

from fastapi import Body, FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

try:
    import cv2
    import numpy as np
except ImportError:  # 生产环境可替换为商业活体/身份核验服务
    cv2 = None
    np = None

try:
    import qrcode
except ImportError:
    qrcode = None


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "device_data.db"))
CORS_ORIGIN_REGEX = os.environ.get(
    "CORS_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
)
LIVE_SNAPSHOT_URL = os.environ.get(
    "LIVE_SNAPSHOT_URL",
    "http://47.103.152.175:8080/api/v1/admin/live-snapshot",
)
LIVE_SNAPSHOT_TIMEOUT = float(os.environ.get("LIVE_SNAPSHOT_TIMEOUT", "6"))
LIVE_SNAPSHOT_CACHE_SECONDS = float(
    os.environ.get("LIVE_SNAPSHOT_CACHE_SECONDS", "3")
)
PRECHECK_MAX_DATA_AGE_SECONDS = float(
    os.environ.get("PRECHECK_MAX_DATA_AGE_SECONDS", "120")
)
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
BOX_STATUSES = ["BOX_OPEN", "BOX_CLOSED"]
MOVE_STATUSES = ["STABLE", "MILD", "SEVERE", "IMPACT", "FREE_FALL"]
TEMPERATURE_STATUSES = ["TEMP_OK", "TEMP_ALERT"]

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
    ],
    "sender": ["view_task", "start_task", "view_report"],
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
    note: Optional[str] = Field(None, max_length=200)


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
    image_base64: Optional[str] = None
    qr_token: Optional[str] = Field(None, max_length=160)
    capture_file_id: Optional[str] = Field(None, max_length=80)
    liveness_token: Optional[str] = Field(None, max_length=160)
    liveness_passed: bool = False
    similarity_score: float = Field(default=0, ge=0, le=1)
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


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def fetch_live_hardware_snapshot() -> dict:
    """读取远端真实硬件快照，短时缓存避免多个页面重复请求设备服务。"""
    global _live_snapshot_cache
    now = time.monotonic()
    if (
        _live_snapshot_cache
        and now - _live_snapshot_cache[0] < LIVE_SNAPSHOT_CACHE_SECONDS
    ):
        return _live_snapshot_cache[1]
    request = urllib.request.Request(
        LIVE_SNAPSHOT_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "coldchain-backend/0.2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=LIVE_SNAPSHOT_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"live hardware snapshot unavailable: {exc}") from exc
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError("live hardware snapshot returned invalid response")
    data = payload["data"]
    _live_snapshot_cache = (now, data)
    return data


def migrate_legacy_users_table(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if not table:
        return
    columns = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(users)").fetchall()
    }
    if {"username", "salt", "display_name"}.issubset(columns) and (
        columns["id"]["type"] or ""
    ).upper() == "INTEGER":
        return

    legacy_rows = conn.execute("SELECT * FROM users ORDER BY rowid").fetchall()
    conn.execute("DROP TABLE IF EXISTS users_v2_migration")
    conn.execute(
        """
        CREATE TABLE users_v2_migration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT,
            created_at TEXT NOT NULL,
            phone TEXT,
            name TEXT,
            organization TEXT,
            status TEXT
        )
        """
    )
    id_mapping = {}
    used_usernames = set()
    for index, row in enumerate(legacy_rows, start=1):
        item = row_to_dict(row)
        username = str(item.get("username") or item.get("phone") or f"legacy_user_{index}")
        original_username = username
        suffix = 2
        while username in used_usernames:
            username = f"{original_username}_{suffix}"
            suffix += 1
        used_usernames.add(username)
        cursor = conn.execute(
            """
            INSERT INTO users_v2_migration (
                username, password_hash, salt, role, display_name, created_at,
                phone, name, organization, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                item.get("password_hash") or "",
                item.get("salt") or item.get("password_salt") or "",
                item.get("role") or "sender",
                item.get("display_name") or item.get("name") or username,
                item.get("created_at") or now_iso(),
                item.get("phone") or username,
                item.get("name") or item.get("display_name") or username,
                item.get("organization") or "",
                item.get("status") or "active",
            ),
        )
        id_mapping[str(item.get("id"))] = cursor.lastrowid

    task_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'task_handoff'"
    ).fetchone()
    if task_table:
        task_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(task_handoff)").fetchall()
        }
        for column in {"owner_user_id", "carrier_user_id", "receiver_user_id"} & task_columns:
            for old_id, new_id in id_mapping.items():
                conn.execute(
                    f"UPDATE task_handoff SET {column} = ? WHERE CAST({column} AS TEXT) = ?",
                    (new_id, old_id),
                )

    token_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'auth_tokens'"
    ).fetchone()
    if token_table:
        conn.execute("DELETE FROM auth_tokens")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_v2_migration RENAME TO users")


def migrate_legacy_auth_tokens_table(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'auth_tokens'"
    ).fetchone()
    if not table:
        return
    columns = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(auth_tokens)").fetchall()
    }
    is_current = (
        set(columns) == {"token", "user_id", "created_at"}
        and (columns["user_id"]["type"] or "").upper() == "INTEGER"
    )
    if is_current:
        return
    conn.execute("DROP TABLE auth_tokens")
    conn.execute(
        """
        CREATE TABLE auth_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )


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
        migrate_legacy_users_table(conn)
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
        migrate_legacy_auth_tokens_table(conn)
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
                note TEXT,
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
        ensure_column(conn, "device_data", "sequence", "INTEGER")
        ensure_column(conn, "device_data", "battery", "INTEGER")
        ensure_column(conn, "device_data", "lat", "REAL")
        ensure_column(conn, "device_data", "lng", "REAL")
        ensure_column(conn, "device_data", "accuracy", "REAL")
        ensure_column(conn, "event_log", "alarm_status", "TEXT")
        ensure_column(conn, "event_log", "acknowledged_at", "TEXT")
        ensure_column(conn, "event_log", "resolved_at", "TEXT")
        ensure_column(conn, "event_log", "resolution", "TEXT")
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
        ensure_column(conn, "handoffs", "note", "TEXT")
        migrate_qr_tokens_schema(conn)
        migrate_face_verifications_schema(conn)
        ensure_demo_task(conn)
        migrate_task_status_column(conn)


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
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )


def migrate_qr_tokens_schema(conn: sqlite3.Connection) -> None:
    """将旧版二维码表迁移到动态二维码所需结构，并保留可关联的历史记录。"""
    columns = {
        row["name"]: row["type"]
        for row in conn.execute("PRAGMA table_info(qr_tokens)").fetchall()
    }
    required = {
        "id",
        "token_hash",
        "task_id",
        "handoff_id",
        "action",
        "issuer_user_id",
        "nonce",
        "status",
        "expires_at",
        "consumed_at",
        "revoked_at",
        "created_at",
    }
    if required.issubset(columns) and columns.get("id", "").upper() == "INTEGER":
        return

    legacy_rows = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM qr_tokens").fetchall()
    ]
    conn.execute("ALTER TABLE qr_tokens RENAME TO qr_tokens_legacy_migration")
    conn.execute(
        """
        CREATE TABLE qr_tokens (
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
    for legacy in legacy_rows:
        handoff = conn.execute(
            "SELECT task_id, from_user_id FROM handoffs WHERE handoff_id = ?",
            (legacy.get("handoff_id"),),
        ).fetchone()
        task_id = legacy.get("task_id") or (handoff["task_id"] if handoff else None)
        if not task_id:
            continue
        status = legacy.get("status")
        if not status:
            status = (
                "consumed"
                if legacy.get("consumed_at")
                else "revoked"
                if legacy.get("revoked_at")
                else "active"
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO qr_tokens (
                token_hash, task_id, handoff_id, action, issuer_user_id,
                nonce, status, expires_at, consumed_at, revoked_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy["token_hash"],
                task_id,
                legacy["handoff_id"],
                legacy.get("action") or "handoff_send",
                legacy.get("issuer_user_id")
                or (handoff["from_user_id"] if handoff else None),
                legacy.get("nonce") or str(legacy.get("id") or secrets.token_hex(12)),
                status,
                legacy["expires_at"],
                legacy.get("consumed_at"),
                legacy.get("revoked_at"),
                legacy["created_at"],
            ),
        )
    conn.execute("DROP TABLE qr_tokens_legacy_migration")


def migrate_face_verifications_schema(conn: sqlite3.Connection) -> None:
    """兼容旧版 OpenCV 检测记录，并升级为新版活体/相似度验证结构。"""
    columns = {
        row["name"]: row["type"]
        for row in conn.execute("PRAGMA table_info(face_verifications)").fetchall()
    }
    required = {
        "id",
        "verification_id",
        "user_id",
        "handoff_id",
        "liveness_passed",
        "similarity_score",
        "threshold",
        "verified",
        "manual_review_required",
        "status",
        "created_at",
    }
    if required.issubset(columns) and columns.get("id", "").upper() == "INTEGER":
        return

    legacy_rows = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM face_verifications").fetchall()
    ]
    conn.execute(
        "ALTER TABLE face_verifications RENAME TO face_verifications_legacy_migration"
    )
    conn.execute(
        """
        CREATE TABLE face_verifications (
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
    for legacy in legacy_rows:
        raw_user_id = legacy.get("user_id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            user = conn.execute(
                """
                SELECT id FROM users
                WHERE username = ? OR phone = ? OR name = ? OR display_name = ?
                ORDER BY id DESC LIMIT 1
                """,
                (raw_user_id, raw_user_id, raw_user_id, raw_user_id),
            ).fetchone()
            user_id = user["id"] if user else None
        if not user_id or not legacy.get("handoff_id"):
            continue
        score = float(legacy.get("similarity_score") or legacy.get("quality_score") or 0)
        if score > 1:
            score /= 100
        score = min(1.0, max(0.0, score))
        liveness_passed = bool(
            legacy.get("liveness_passed", legacy.get("detected", False))
        )
        threshold = float(legacy.get("threshold") or 0.72)
        verified = bool(legacy.get("verified", liveness_passed and score >= threshold))
        conn.execute(
            """
            INSERT OR IGNORE INTO face_verifications (
                verification_id, user_id, handoff_id, qr_token,
                capture_file_id, liveness_token, liveness_passed,
                similarity_score, threshold, verified,
                manual_review_required, location_lat, location_lng,
                location_accuracy, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy.get("verification_id")
                or legacy.get("id")
                or f"FV-{secrets.token_hex(8).upper()}",
                user_id,
                legacy["handoff_id"],
                legacy.get("qr_token"),
                legacy.get("capture_file_id"),
                legacy.get("liveness_token"),
                int(liveness_passed),
                score,
                threshold,
                int(verified),
                int(legacy.get("manual_review_required", not verified)),
                legacy.get("location_lat"),
                legacy.get("location_lng"),
                legacy.get("location_accuracy"),
                legacy.get("status") or ("verified" if verified else "pending_review"),
                legacy["created_at"],
            ),
        )
    conn.execute("DROP TABLE face_verifications_legacy_migration")


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
    yield


app = FastAPI(
    title="Cold Chain Traceability Backend",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def build_event_items(data: DeviceDataIn, event_type: str) -> list[tuple[str, str, str]]:
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
        events.append(("TEMP_ALERT", "温度异常", "温度超出当前演示阈值"))

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


def save_device_data(
    conn: sqlite3.Connection,
    data: DeviceDataIn,
    sequence: Optional[int] = None,
    battery: Optional[int] = None,
    location: Optional[LocationIn] = None,
) -> tuple[Optional[sqlite3.Row], Optional[JSONResponse]]:
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
            location.lat if location else None,
            location.lng if location else None,
            location.accuracy if location else None,
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
    if not device or not device["device_secret_hash"]:
        return None
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


def telemetry_temp_status(payload: DeviceTelemetryIn, task: Optional[sqlite3.Row]) -> str:
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


def serialize_device(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
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


def serialize_binding(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def serialize_handoff(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["handoff_db_id"] = item["id"]
    return item


def enrich_handoff(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = serialize_handoff(row)
    item["issuer_user_id"] = item["from_user_id"]
    item["recipient_user_id"] = item["to_user_id"]
    qr_row = conn.execute(
        """
        SELECT consumed_at, expires_at, action FROM qr_tokens
        WHERE handoff_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (item["handoff_id"],),
    ).fetchone()
    face_rows = conn.execute(
        """
        SELECT * FROM face_verifications
        WHERE handoff_id = ? AND verified = 1
        ORDER BY id DESC
        """,
        (item["handoff_id"],),
    ).fetchall()
    faces = {}
    for face_row in face_rows:
        party = "issuer" if face_row["user_id"] == item["from_user_id"] else "recipient"
        if party not in faces:
            face = serialize_face_verification(face_row)
            faces[party] = {
                "verified": True,
                "quality_score": face["similarity_score"],
                "verified_at": face["created_at"],
            }
    item["faces"] = faces
    item["qr_verified_at"] = qr_row["consumed_at"] if qr_row else None
    item["expires_at"] = qr_row["expires_at"] if qr_row else None
    item["action"] = qr_row["action"] if qr_row else item["handoff_type"]
    if item["status"] == "pending" and qr_row and qr_row["consumed_at"]:
        item["status"] = "qr_verified"
    return item


def serialize_audit_log(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


def serialize_qr_token(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["token_id"] = item["id"]
    return item


def serialize_file(row: sqlite3.Row) -> dict:
    return row_to_dict(row)


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
    item = row_to_dict(row)
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
        (task_id, from_status, to_status, reason, actor_user_id, changed_at or now_iso()),
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


def device_signature(secret: str, device_id: str, timestamp: str, nonce: str, payload: BaseModel) -> str:
    message = f"{device_id}.{timestamp}.{nonce}.{canonical_body_hash(payload)}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_qr_token(token: str) -> str:
    return hash_token_value(token)


def qr_image_data_url(payload: str) -> Optional[str]:
    if qrcode is None:
        return None
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def detect_face_image(image_base64: str) -> dict:
    if cv2 is None or np is None:
        raise ValueError("OpenCV face detector is unavailable")
    raw_value = image_base64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(raw_value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid face image") from exc
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid face image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(80, 80),
    )
    image_area = max(1, image.shape[0] * image.shape[1])
    largest_area = max((width * height for _, _, width, height in faces), default=0)
    quality = min(0.99, round(0.9 + min(0.09, largest_area / image_area), 3))
    return {
        "face_count": len(faces),
        "quality_score": quality if len(faces) == 1 else 0,
        "passed": len(faces) == 1,
    }


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
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
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


def ljy_legacy_hash_password(password: str, salt: str) -> Optional[str]:
    try:
        salt_bytes = bytes.fromhex(salt)
    except (ValueError, TypeError):
        return None
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_bytes,
        210_000,
    ).hex()


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
    ljy_hash = ljy_legacy_hash_password(password, salt)
    if ljy_hash and secrets.compare_digest(ljy_hash, stored_hash):
        return True
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
        return task["task_id"] == DEMO_TASK["task_id"]
    if user["role"] == "admin":
        return True
    user_id = str(user["user_id"])
    if task.get("owner_user_id") is not None and str(task["owner_user_id"]) == user_id:
        return True
    if task.get("carrier_user_id") is not None and str(task["carrier_user_id"]) == user_id:
        return True
    if task.get("receiver_user_id") is not None and str(task["receiver_user_id"]) == user_id:
        return True
    return False


def can_modify_task(user: dict, task: dict) -> bool:
    return user["role"] == "admin" or (
        task.get("owner_user_id") is not None
        and str(task["owner_user_id"]) == str(user["user_id"])
    )


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


def normalize_telemetry(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    box_status = item["box_status"].upper()
    temp_status = item["temp_status"].upper()
    move_status = item["move_status"].upper()
    item["box_status"] = {
        "CLOSED": "BOX_CLOSED",
        "OPEN": "BOX_OPEN",
    }.get(box_status, box_status)
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


def normalize_event(row: sqlite3.Row) -> dict:
    item = row_to_dict(row)
    item["event_id"] = item["id"]
    item["description"] = item["event_detail"]
    item["event_level"] = event_level(item["event_type"])
    item["event_display"] = EVENT_DISPLAY_LABELS.get(
        item["event_type"],
        item["event_name"],
    )
    item["alarm_status"] = item.get("alarm_status") or "new"
    return item


def task_latest_telemetry(conn: sqlite3.Connection, task_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT * FROM device_data
        WHERE task_id = ? ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return normalize_telemetry(row) if row else None


def task_abnormal_count(conn: sqlite3.Connection, task_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS count FROM event_log WHERE task_id = ?",
        (task_id,),
    ).fetchone()["count"]


def enrich_task(row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    task = serialize_task(row)
    task_id = task["task_id"]
    latest = task_latest_telemetry(conn, task_id)
    task["abnormal_count"] = task_abnormal_count(conn, task_id)
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
        ORDER BY id DESC
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
    init_db()
    with get_connection() as conn:
        row, error = save_device_data(conn, data)
        if error:
            return error

    return {"ok": True, "data": row_to_dict(row)}


@app.get("/api/device/latest")
def get_latest_device_data() -> dict:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM device_data ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return normalize_telemetry(row) if row else {}


@app.get("/api/device/history")
def get_device_history() -> list[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM device_data ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return [normalize_telemetry(row) for row in rows]


@app.get("/api/device/events")
def get_device_events() -> list[dict]:
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
    init_db()
    with get_connection() as conn:
        row = get_task_row(conn)
    return legacy_task_view(enrich_task(row, conn))


@app.get("/api/task/report")
def get_task_report() -> dict:
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


@app.post("/api/v1/auth/login")
def login_user(payload: LoginIn):
    init_db()
    account = (payload.username or payload.phone or "").strip()
    if not account:
        return api_error(422, 42203, "username or phone required")
    with get_connection() as conn:
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
        task = get_task_by_id(conn, payload.task_id)
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
                    payload.task_id,
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
            task_id=payload.task_id,
            temperature=payload.temperature,
            humidity=payload.humidity,
            light_raw=payload.light_raw,
            box_status=payload.box_status,
            move_status=payload.move_status,
            temp_status=telemetry_temp_status(payload, task),
            acc_total=payload.acc_total if payload.acc_total is not None else 0.0,
            motion_score=payload.motion_score if payload.motion_score is not None else 0.0,
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
            payload.task_id,
            payload.battery,
            row["timestamp"],
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
            timestamp,
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
        conn.execute(
            """
            INSERT INTO devices (
                device_id, device_name, model, status, current_task_id,
                battery, last_seen_at, created_at, updated_at, device_secret_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_name = excluded.device_name,
                model = excluded.model,
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
                hash_device_secret(payload.device_secret) if payload.device_secret else None,
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
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM devices ORDER BY updated_at DESC"
        ).fetchall()
    return api_success({"items": [serialize_device(row) for row in rows]})


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

        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not device:
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (device_id, "available", timestamp, timestamp),
            )
        else:
            device_data = row_to_dict(device)
            if (
                device_data.get("current_task_id")
                and device_data["current_task_id"] != payload.task_id
            ):
                return api_error(409, 40920, "device already bound")

        conn.execute(
            """
            UPDATE device_bindings
            SET status = ?, unbound_at = ?
            WHERE device_id = ? AND status = ?
            """,
            ("unbound", timestamp, device_id, "bound"),
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
        rows = conn.execute(
            """
            SELECT * FROM device_bindings
            WHERE device_id = ?
            ORDER BY id DESC
            """,
            (device_id,),
        ).fetchall()
    return api_success({"items": [serialize_binding(row) for row in rows]})


@app.post("/api/v1/devices/{device_id}/bind-check")
def check_v1_device_binding(
    device_id: str,
    payload: DeviceBindCheckIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    if not require_user(authorization):
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        device = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
    occupied_task_id = device["current_task_id"] if device else None
    available = not occupied_task_id
    return api_success(
        {
            "device_id": device_id,
            "box_id": payload.box_id,
            "seal_id": payload.seal_id,
            "available": available,
            "occupied_task_id": occupied_task_id,
            "checked_at": now_iso(),
            "message": "设备可绑定" if available else "设备已绑定其他运单",
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
        available_device_ids = sorted(
            {
                str(item.get("device_id"))
                for item in (snapshot.get("latest_telemetry_by_task") or {}).values()
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
        row = conn.execute(
            "SELECT * FROM device_data WHERE device_id = ? ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    selected_local_row = row if allow_local else None
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
                    "真实硬件接口暂不可用，真实预检不会使用本地测试数据"
                    if hardware_error
                    else "真实硬件接口在线，但没有与该设备编号匹配的监测数据"
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
    fresh = (
        age_seconds is not None
        and age_seconds <= PRECHECK_MAX_DATA_AGE_SECONDS
    )
    passed = (
        fresh
        and min_temp <= telemetry["temperature"] <= max_temp
        and telemetry["box_status"] == "BOX_CLOSED"
    )
    if not fresh:
        reason = (
            f"{'真实硬件' if source == 'hardware' else '本地后端'}数据已过期"
            + (f"（{age_seconds} 秒前）" if age_seconds is not None else "（时间无效）")
            + "，不能作为当前预检结果"
        )
    elif passed:
        reason = f"{'真实硬件' if source == 'hardware' else '本地后端'}数据：温度与箱体状态合格"
    else:
        reason = f"{'真实硬件' if source == 'hardware' else '本地后端'}数据：请检查温度范围与箱体封闭状态"
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
    if os.environ.get("ALLOW_LOCAL_SIMULATION", "1") != "1":
        return api_error(403, 40301, "local simulation disabled")
    with get_connection() as conn:
        device_row = conn.execute(
            "SELECT current_task_id FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        target_task_id = payload.task_id or (
            device_row["current_task_id"] if device_row else None
        )
        if target_task_id:
            task_row = get_task_by_id(conn, target_task_id)
            if not task_row or not can_view_task(user, serialize_task(task_row)):
                return api_error(404, 40401, "task not found")
            if task_row["device_id"] and str(task_row["device_id"]) != str(device_id):
                return api_error(409, 40921, "device does not match task")
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
        tasks = [
            enrich_task(row, conn)
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
        carrier_user = None
        receiver_user = None
        if payload.carrier:
            carrier_user = conn.execute(
                """
                SELECT * FROM users
                WHERE role = 'carrier' AND status = 'active'
                  AND (username = ? OR phone = ? OR name = ? OR display_name = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (payload.carrier, payload.carrier, payload.carrier, payload.carrier),
            ).fetchone()
        if payload.receiver:
            receiver_user = conn.execute(
                """
                SELECT * FROM users
                WHERE role = 'receiver' AND status = 'active'
                  AND (username = ? OR phone = ? OR name = ? OR display_name = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (payload.receiver, payload.receiver, payload.receiver, payload.receiver),
            ).fetchone()
        cursor = conn.execute(
            """
            INSERT INTO task_handoff (
                task_id, device_id, sample_name, sender, receiver, carrier,
                status, started_at, signed_at, updated_at,
                owner_user_id, carrier_user_id, receiver_user_id,
                batch, expected_arrival, box_id, seal_id,
                temperature_min, temperature_max, idempotency_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                payload.device_id,
                payload.sample_name,
                sender,
                payload.receiver or "",
                payload.carrier or "",
                "pending_pack",
                None,
                None,
                timestamp,
                user["user_id"],
                carrier_user["id"] if carrier_user else None,
                receiver_user["id"] if receiver_user else None,
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
    with get_connection() as conn:
        row = get_task_by_id(conn, task_id)
        if row and not can_view_task(user, serialize_task(row)):
            return api_error(404, 40401, "task not found")
    if not row:
        return api_error(404, 40401, "task not found")
    return api_success(enrich_task(row, conn))


@app.patch("/api/v1/tasks/{task_id}")
@app.put("/api/v1/tasks/{task_id}")
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

        allowed = {
            "sample_name",
            "batch",
            "receiver",
            "carrier",
            "expected_arrival",
            "device_id",
            "box_id",
            "seal_id",
            "temperature_min",
            "temperature_max",
        }
        changes = payload.model_dump(exclude_unset=True)
        if "carrier" in changes:
            carrier_user = conn.execute(
                """
                SELECT id FROM users
                WHERE role = 'carrier' AND status = 'active'
                  AND (username = ? OR phone = ? OR name = ? OR display_name = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (
                    changes["carrier"],
                    changes["carrier"],
                    changes["carrier"],
                    changes["carrier"],
                ),
            ).fetchone()
            changes["carrier_user_id"] = carrier_user["id"] if carrier_user else None
        if "receiver" in changes:
            receiver_user = conn.execute(
                """
                SELECT id FROM users
                WHERE role = 'receiver' AND status = 'active'
                  AND (username = ? OR phone = ? OR name = ? OR display_name = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (
                    changes["receiver"],
                    changes["receiver"],
                    changes["receiver"],
                    changes["receiver"],
                ),
            ).fetchone()
            changes["receiver_user_id"] = receiver_user["id"] if receiver_user else None
        assignments = []
        values = []
        for key, value in changes.items():
            if key in allowed or key in {"carrier_user_id", "receiver_user_id"}:
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
            record_audit(
                conn,
                "task.update",
                user_id=user["user_id"],
                resource_type="task",
                resource_id=task_id,
                task_id=task_id,
                detail=",".join(changes.keys()),
            )
        updated = get_task_by_id(conn, task_id)
        result = enrich_task(updated, conn)
    return api_success(result, "task updated")


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
        if task["status"] not in {"pending_pack", "pending_handoff"}:
            return task_state_conflict()

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
        if task["status"] in {"in_transit", "arrived", "signed", "rejected", "canceled"}:
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
        if not to_user:
            return api_error(404, 40404, "target user not found")
        existing = conn.execute(
            """
            SELECT * FROM handoffs
            WHERE task_id = ? AND status = ?
            """,
            (task_id, "pending"),
        ).fetchone()
        if existing:
            if (
                existing["from_user_id"] == user["user_id"]
                and existing["to_user_id"] == payload.to_user_id
            ):
                return api_success(serialize_handoff(existing), "handoff reused")
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

    qr_payload = f"coldchain://handoff?token={token}"
    return api_success(
        {
            "token_id": row["id"],
            "token": token,
            "expires_at": row["expires_at"],
            "ttl_seconds": payload.ttl_seconds,
            "refresh_after": min(45, max(1, payload.ttl_seconds - 15)),
            "qr_payload": qr_payload,
            "qr_image_data_url": qr_image_data_url(qr_payload),
            "handoff_id": payload.handoff_id,
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
        row = conn.execute(
            "SELECT * FROM qr_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return api_error(410, 41010, "qr token invalid")
        qr = serialize_qr_token(row)
        now_dt = datetime.now(timezone.utc).astimezone()
        expires_at = datetime.fromisoformat(qr["expires_at"])
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
            SET status = ?, consumed_at = ?
            WHERE id = ?
            """,
            ("consumed", timestamp, qr["id"]),
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
    face_count = 0
    quality_score = payload.similarity_score
    liveness_passed = payload.liveness_passed
    if payload.image_base64:
        try:
            detection = detect_face_image(payload.image_base64)
        except ValueError as exc:
            return api_error(422, 42205, str(exc))
        face_count = detection["face_count"]
        quality_score = detection["quality_score"]
        liveness_passed = detection["passed"]
    verified = liveness_passed and quality_score >= threshold
    manual_review_required = not verified
    verification_id = generate_verification_id()
    location = payload.location
    with get_connection() as conn:
        profile = conn.execute(
            "SELECT * FROM face_profiles WHERE user_id = ? AND status = ?",
            (user["user_id"], "active"),
        ).fetchone()
        if not profile and payload.image_base64 and verified:
            conn.execute(
                """
                INSERT INTO face_profiles (
                    user_id, template_id, consent_at, quality_score,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["user_id"],
                    f"opencv-local-{user['user_id']}",
                    timestamp,
                    quality_score,
                    "active",
                    timestamp,
                    timestamp,
                ),
            )
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
        if user["role"] != "admin" and user["user_id"] not in {
            handoff["from_user_id"],
            handoff["to_user_id"],
        }:
            return api_error(403, 40301, "forbidden")
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
                payload.qr_token,
                payload.capture_file_id,
                payload.liveness_token,
                int(liveness_passed),
                quality_score,
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
    result = serialize_face_verification(row)
    result["face_count"] = face_count or (1 if result["verified"] else 0)
    result["quality_score"] = result["similarity_score"]
    result["party"] = "issuer" if user["user_id"] == handoff["from_user_id"] else "recipient"
    return api_success(result, "face verified")


@app.post("/api/v1/face/simulate-verify")
def simulate_v1_face(
    payload: FaceSimulateIn,
    authorization: Optional[str] = Header(None),
):
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    if os.environ.get("ALLOW_LOCAL_SIMULATION", "1") != "1":
        return api_error(403, 40301, "local simulation disabled")
    timestamp = now_iso()
    with get_connection() as conn:
        handoff = conn.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (payload.handoff_id,),
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "party": "issuer" if user["user_id"] == handoff["from_user_id"] else "recipient",
        }
    )
    return api_success(result, "local face simulation verified")


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
    return api_success({"limit": limit, "items": [serialize_face_review(row) for row in rows]})


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

    item["download_url"] = item["storage_url"] or f"local://files/{file_id}"
    item["expires_in"] = 300
    return api_success(item)


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
        result = enrich_handoff(conn, row)
    return api_success(result)


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
        task_row = get_task_by_id(conn, handoff["task_id"])
        previous_status = canonical_task_status(row_to_dict(task_row)) if task_row else None
        qr_verified = conn.execute(
            """
            SELECT 1 FROM qr_tokens
            WHERE handoff_id = ? AND consumed_at IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (handoff_id,),
        ).fetchone()
        verified_users = {
            face["user_id"]
            for face in conn.execute(
                """
                SELECT user_id FROM face_verifications
                WHERE handoff_id = ? AND verified = 1
                """,
                (handoff_id,),
            ).fetchall()
        }
        if not qr_verified:
            return api_error(409, 40932, "handoff qr not verified")
        if handoff["from_user_id"] not in verified_users:
            return api_error(409, 40933, "issuer face not verified")
        if handoff["to_user_id"] not in verified_users:
            return api_error(409, 40934, "recipient face not verified")

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
                note = ?, confirmed_at = ?, updated_at = ?
            WHERE handoff_id = ?
            """,
            (
                "confirmed",
                location.lat if location else None,
                location.lng if location else None,
                location.accuracy if location else None,
                certificate_no,
                trace_hash,
                payload.note.strip() if payload.note else None,
                timestamp,
                timestamp,
                handoff_id,
            ),
        )
        conn.execute(
            """
            UPDATE task_handoff
            SET status = ?, started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE task_id = ?
            """,
            ("in_transit", timestamp, timestamp, handoff["task_id"]),
        )
        if previous_status != "in_transit":
            record_status_history(
                conn,
                task_id=handoff["task_id"],
                from_status=previous_status,
                to_status="in_transit",
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
    result["task_status"] = "in_transit"
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
def get_v1_latest_telemetry(task_id: str):
    init_db()
    with get_connection() as conn:
        task = get_task_by_id(conn, task_id)
        if not task:
            return api_error(404, 40401, "task not found")
        row = conn.execute(
            """
            SELECT * FROM device_data
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    return api_success(normalize_telemetry(row) if row else None)


@app.get("/api/v1/tasks/{task_id}/hardware/snapshot")
def get_v1_task_hardware_snapshot(
    task_id: str,
    authorization: Optional[str] = Header(None),
):
    """代理真实硬件快照，并且只返回与当前任务或绑定设备严格匹配的数据。"""
    init_db()
    user = require_user(authorization)
    if not user:
        return api_error(401, 40102, "unauthorized")
    with get_connection() as conn:
        task_row = get_task_by_id(conn, task_id)
        if not task_row:
            return api_error(404, 40401, "task not found")
        task = serialize_task(task_row)
        if not can_view_task(user, task):
            return api_error(404, 40401, "task not found")

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


@app.get("/api/v1/tasks/{task_id}/telemetry/history")
def get_v1_telemetry_history(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    cursor: Optional[int] = Query(default=None, ge=1),
    downsample: int = Query(default=1, ge=1, le=60),
):
    init_db()
    start_time = normalize_query_time(start_time)
    end_time = normalize_query_time(end_time)
    with get_connection() as conn:
        task = get_task_by_id(conn, task_id)
        if not task:
            return api_error(404, 40401, "task not found")
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
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    items = [normalize_telemetry(row) for index, row in enumerate(rows)]
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
):
    init_db()
    with get_connection() as conn:
        task = get_task_by_id(conn, task_id)
        if not task:
            return api_error(404, 40401, "task not found")
        ensure_task_offline_alarm(conn, task)
        rows = conn.execute(
            """
            SELECT * FROM event_log
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
    return api_success(
        {"limit": limit, "items": [normalize_event(row) for row in rows]}
    )


@app.post("/api/v1/alarms/{alarm_id}/ack")
def ack_v1_alarm(alarm_id: int):
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40403, "alarm not found")
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
def resolve_v1_alarm(alarm_id: int, payload: ResolveAlarmIn):
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM event_log WHERE id = ?",
            (alarm_id,),
        ).fetchone()
        if not row:
            return api_error(404, 40403, "alarm not found")
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
    return api_success({"limit": limit, "items": [serialize_audit_log(row) for row in rows]})


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
            "SELECT * FROM task_handoff ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = [enrich_task(row, conn) for row in rows]
    return api_success({"limit": limit, "items": items})


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
    return api_success({"limit": limit, "items": [serialize_notification(row) for row in rows]})


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
):
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:start"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
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
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.start",
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
):
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:arrive"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
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
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.arrive",
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
):
    init_db()
    timestamp = now_iso()
    with get_connection() as conn:
        scope = f"task:{task_id}:sign"
        existing_result = get_idempotent_result(conn, scope, idempotency_key)
        if existing_result:
            return api_success(existing_result["data"], existing_result["message"])
        row = get_task_by_id(conn, task_id)
        if not row:
            return api_error(404, 40401, "task not found")
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
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.sign",
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
):
    init_db()
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
            changed_at=timestamp,
        )
        record_audit(
            conn,
            "task.reject",
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
def get_v1_trace_report(task_id: str):
    init_db()
    with get_connection() as conn:
        report = get_trace_report_data(conn, task_id)
    if not report:
        return api_error(404, 40401, "task not found")
    return api_success(report)


@app.get("/api/v1/tasks/{task_id}/trace-report.pdf")
def get_v1_trace_report_pdf(task_id: str):
    init_db()
    with get_connection() as conn:
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
        headers={"Content-Disposition": f'attachment; filename="{task_id}-trace-report.pdf"'},
    )


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
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
