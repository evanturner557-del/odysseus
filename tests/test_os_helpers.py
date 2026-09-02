"""OS test helper — isolated sqlite, no live network, no secrets."""

from __future__ import annotations

import os

# Must run before core.database import in each test module that uses this.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("LOCALHOST_BYPASS", "true")


def make_os_db():
    """Create tables on the process engine and return a SessionLocal session.

    Tests that need isolation should still prefer a dedicated engine via
    tests.helpers.sqlite_db.make_temp_sqlite when they patch SessionLocal.
    """
    from core.database import Base, SessionLocal, engine
    import autonomy.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    return SessionLocal()
