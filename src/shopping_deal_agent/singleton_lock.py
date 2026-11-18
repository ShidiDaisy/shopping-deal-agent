"""Cross-process mutual exclusion for ``check``/``run``.

``RobotsGuard`` and ``DomainRateLimiter`` state lives in process memory. If a
second ``sda run``/``sda check`` against the same database starts (a bad
restart, or running the README's cron suggestion alongside a long-running
daemon), its rate limiter knows nothing about the first process's requests and
the two together can hit a watched site twice as fast as configured. This is
a simple flock-based advisory lock, scoped to one database file, that refuses
a second instance outright instead of letting that happen silently.
"""
from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Another process already holds the lock for this database."""


class SingletonLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            # Non-POSIX platform: best-effort only, no cross-process guarantee.
            pass
        except OSError as exc:
            fh.close()
            raise AlreadyRunning(
                f"another shopping-deal-agent process already holds {self.path} "
                "(delete it only if you're sure no other instance is running)"
            ) from exc
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "SingletonLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
