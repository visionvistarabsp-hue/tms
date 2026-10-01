import os
import time
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

# SQLAlchemy 2.x resolves a bare "postgresql://" URL to the psycopg2 dialect.
# We run psycopg 3 (psycopg[binary]), which ships prebuilt wheels that work on
# serverless Lambda, so the dialect is pinned explicitly instead of guessed.
PG_DIALECT = "postgresql+psycopg"


def _is_postgres(url):
    return bool(url) and url.startswith("postgres")


def normalize_db_url(url):
    """Pin a bare postgres URL to the psycopg3 dialect, leaving any explicit
    driver suffix (or a non-postgres URL such as sqlite) untouched."""
    if not _is_postgres(url):
        return url
    parts = urlsplit(url)
    if "+" in parts.scheme:
        return url
    return urlunsplit((PG_DIALECT, parts.netloc, parts.path, parts.query, parts.fragment))


def libpq_dsn(url):
    """Strip SQLAlchemy's "+driver" dialect suffix for direct psycopg.connect().

    SQLAlchemy resolves "postgresql+psycopg://" to the psycopg3 dialect, but the
    DBAPI itself parses conninfo and has no concept of a driver suffix -- it
    rejects the whole URL as an invalid option. Raw connections need the plain
    libpq form ("postgresql://"), keeping any query string such as sslmode.
    """
    if not _is_postgres(url):
        return url
    parts = urlsplit(url)
    if "+" not in parts.scheme:
        return url
    return urlunsplit(
        (parts.scheme.split("+", 1)[0], parts.netloc, parts.path, parts.query, parts.fragment)
    )


def make_pg_creator(url=None):
    """Return a pooled-connection creator that retries transient
    DNS/network failures (e.g. Neon pooler host flapping) up to 5 times."""
    import psycopg

    url = libpq_dsn(normalize_db_url(url or os.getenv("DATABASE_URL", "")))
    attempts = 5
    backoff_secs = [0.5, 1, 2, 3, 3]

    def creator():
        last_err = None
        for i in range(attempts):
            try:
                return psycopg.connect(url, connect_timeout=10)
            except Exception as exc:  # noqa: BLE001 - retry any transient connect error
                last_err = exc
                time.sleep(backoff_secs[i] if i < len(backoff_secs) else 3)
        raise last_err

    return creator


def postgres_reachable(url=None, retries=3):
    """Quick startup check: can we actually open a Postgres connect?"""
    import psycopg

    url = libpq_dsn(normalize_db_url(url or os.getenv("DATABASE_URL", "")))
    if not _is_postgres(url):
        return True
    for i in range(retries):
        try:
            conn = psycopg.connect(url, connect_timeout=10)
            conn.close()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1 + i)
    return False


class Config:
    SQLALCHEMY_DATABASE_URI = normalize_db_url(
        os.getenv("DATABASE_URL", "sqlite:///tms.db")
    )
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
