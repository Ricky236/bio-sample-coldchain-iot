import argparse
import getpass
import secrets
import sqlite3

from main import (
    get_connection,
    hash_password,
    init_db,
    now_iso,
    record_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a local administrator without enabling public admin registration."
    )
    parser.add_argument("--phone", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--organization", default="")
    args = parser.parse_args()

    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        parser.error("passwords do not match")
    if len(password) < 8:
        parser.error("password must contain at least 8 characters")

    init_db()
    salt = secrets.token_hex(16)
    connection = get_connection()
    try:
        with connection as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? OR phone = ?",
                (args.phone, args.phone),
            ).fetchone()
            if existing:
                parser.error("account already exists")
            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, salt, role, display_name, created_at,
                    phone, name, organization, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    args.phone,
                    hash_password(password, salt),
                    salt,
                    "admin",
                    args.name,
                    now_iso(),
                    args.phone,
                    args.name,
                    args.organization,
                    "active",
                ),
            )
            record_audit(
                conn,
                "admin.bootstrap",
                user_id=cursor.lastrowid,
                resource_type="user",
                resource_id=str(cursor.lastrowid),
            )
    except sqlite3.IntegrityError:
        parser.error("account already exists")
    finally:
        connection.close()

    print("Administrator created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
