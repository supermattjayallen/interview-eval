import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def database_enabled() -> bool:
    return bool(settings.database_url.strip())


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        if not database_enabled():
            raise RuntimeError("DATABASE_URL is not configured")
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_database() -> None:
    if not database_enabled():
        logger.info("DATABASE_URL not set — question bank will use JSON files only")
        return

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info("PostgreSQL schema ready")
