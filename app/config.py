import os
import time
from dotenv import load_dotenv

load_dotenv()


def _is_postgres(url):
    return bool(url) and url.startswith("postgres")


def make_pg_creator(url=None):
    """Return a pooled-connection creator that retries transient
    DNS/network failures (e.g. Neon pooler host flapping) up to 5 times."""
    import psycopg2

    url = url or os.getenv("DATABASE_URL", "")
    attempts = 5
    backoff_secs = [0.5, 1, 2, 3, 3]

    def creator():
        last_err = None
        for i in range(attempts):
            try:
                return psycopg2.connect(url, connect_timeout=10)
            except Exception as exc:  # noqa: BLE001 - retry any transient connect error
                last_err = exc
                time.sleep(backoff_secs[i] if i < len(backoff_secs) else 3)
        raise last_err

    return creator


def postgres_reachable(url=None, retries=3):
    """Quick startup check: can we actually open a Postgres connect?"""
    import psycopg2

    url = url or os.getenv("DATABASE_URL", "")
    if not _is_postgres(url):
        return True
    for i in range(retries):
        try:
            conn = psycopg2.connect(url, connect_timeout=10)
            conn.close()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1 + i)
    return False


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///tms.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    if not SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {
            "sslmode": "require",
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
        }
    SKIP_INIT_SEED = (
        os.getenv("SKIP_INIT_SEED", "0").lower() in ("1", "true", "yes")
        or bool(os.getenv("VERCEL"))
    )
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

    TDS_EXEMPTION_PERIODS = [
        {"start": "2026-04-01", "end": "2027-03-31"},  # FY 2026-27
        {"start": "2025-04-01", "end": "2026-03-31"},  # FY 2025-26
        {"start": "2024-04-01", "end": "2025-03-31"},  # FY 2024-25
    ]
