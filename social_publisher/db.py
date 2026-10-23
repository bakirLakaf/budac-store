"""اتصال PostgreSQL + إنشاء جداول الأداة (sp_posts, sp_media, sp_results, sp_tokens)."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sp_posts (
    id            SERIAL PRIMARY KEY,
    caption       TEXT NOT NULL DEFAULT '',
    platforms     TEXT[] NOT NULL,
    -- scheduled | publishing | done | partial | failed | cancelled
    status        TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    product_id    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sp_posts_due_idx ON sp_posts (status, scheduled_at);

CREATE TABLE IF NOT EXISTS sp_media (
    id          SERIAL PRIMARY KEY,
    post_id     INT NOT NULL REFERENCES sp_posts(id) ON DELETE CASCADE,
    position    INT NOT NULL DEFAULT 0,
    source      TEXT NOT NULL,            -- gallery (صورة المتجر، ما نكوبيوهاش) | upload (ملف خارجي مؤقت)
    kind        TEXT NOT NULL,            -- image | video
    url         TEXT,                     -- gallery: الرابط العام تاع الصورة
    local_path  TEXT,                     -- مسار الملف في السيرفر
    sha256      TEXT,                     -- upload: باش ما نكرروش نفس الملف
    purged      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS sp_media_post_idx ON sp_media (post_id);
CREATE INDEX IF NOT EXISTS sp_media_sha_idx ON sp_media (sha256);

CREATE TABLE IF NOT EXISTS sp_results (
    post_id     INT NOT NULL REFERENCES sp_posts(id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    remote_id   TEXT,
    error       TEXT,
    attempts    INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, platform)
);

CREATE TABLE IF NOT EXISTS sp_tokens (
    platform    TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_pool(dsn):
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 8, dsn)


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def cursor(commit=True):
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def init_schema():
    with cursor() as cur:
        # قفل باش لو كان بزاف workers يبداو مع بعض ما يتصادموش
        cur.execute("SELECT pg_advisory_xact_lock(7355608)")
        cur.execute(SCHEMA)
