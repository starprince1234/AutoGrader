"""Engine and session plumbing.

Two engines, on purpose:

* ``app`` - the least-privilege question-bank role. Every public route and every
  non-protected domain operation uses it. It has no grant on
  ``b3.reference_answers`` / ``b3.test_cases`` / ``b3.blobs``, so a coding mistake
  in a public path fails loudly at the database instead of leaking protected
  content (acceptance 3-10-3).
* ``protected`` - the role used only by the publish/compile path.

Neither engine may point at a superuser, and neither touches ``b2``/``b4``.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings


class Database:
    """Holds the two engines and hands out sessions."""

    def __init__(self, settings: Settings, *, echo: bool = False) -> None:
        self._app_engine: Engine = create_engine(
            settings.database_url, pool_pre_ping=True, echo=echo, future=True
        )
        self._protected_engine: Engine = create_engine(
            settings.protected_database_url, pool_pre_ping=True, echo=echo, future=True
        )
        self._app_factory = sessionmaker(bind=self._app_engine, expire_on_commit=False, future=True)
        self._protected_factory = sessionmaker(
            bind=self._protected_engine, expire_on_commit=False, future=True
        )

    @property
    def app_engine(self) -> Engine:
        return self._app_engine

    @property
    def protected_engine(self) -> Engine:
        return self._protected_engine

    def app_session_factory(self) -> sessionmaker[Session]:
        return self._app_factory

    def protected_session_factory(self) -> sessionmaker[Session]:
        return self._protected_factory

    @contextmanager
    def app_session(self) -> Iterator[Session]:
        session = self._app_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def protected_session(self) -> Iterator[Session]:
        session = self._protected_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self._app_engine.dispose()
        self._protected_engine.dispose()

    def ping(self) -> bool:
        from sqlalchemy import text

        with self._app_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
