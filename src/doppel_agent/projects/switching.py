"""Serialized single-kernel switching, with no force-kill or parallel owners."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from .catalog import ProjectCatalog, ProjectCatalogPersistenceError


@dataclass
class _KernelConstructionLifetime:
    target: Path
    candidate: object = None
    factory_attempted: bool = False
    factory_returned: bool = False
    cleanup_uncertain: bool = False


class ProjectSwitcher:
    def __init__(self, catalog: ProjectCatalog, kernel, factory):
        self.catalog = catalog
        self.kernel = kernel
        self.factory = factory
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._construction_uncertain = threading.Event()
        self._construction_source = None
        self._unresolved_kernel_sources: dict[int, _KernelConstructionLifetime] = {}

    def _construct_original(self, target: Path):
        source = self._construction_source = _KernelConstructionLifetime(target, factory_attempted=True)
        try:
            source.candidate = self.factory(target)
            source.factory_returned = True
            candidate = source.candidate
            if (candidate is None or candidate.workspace != target
                    or not callable(candidate.start) or not callable(candidate.close)):
                raise ValueError('original candidate identity unavailable')
        except BaseException:
            source.cleanup_uncertain = True
            self._unresolved_kernel_sources[id(source)] = source
            self._construction_uncertain.set()
            # Unknown original factory effect isn't permission to close an
            # unusable identity, restore old project, or allocate another kernel.
            raise RuntimeError('project construction cleanup is unresolved') from None
        self.kernel = candidate  # Exact validated original before its start can fail.
        return candidate

    def switch(self, key: str) -> dict:
        if self._closing.is_set() or self._construction_uncertain.is_set() or not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "project operation is busy or closing"}
        try:
            if self._construction_uncertain.is_set():
                return {'ok': False, 'error': 'project construction cleanup is unresolved'}
            target = self.catalog.resolve(key)  # Before stopping the old kernel.
            old = self.kernel
            if old is not None and target == old.workspace and old.ready:
                return {"ok": True, "url": old.url, "changed": False}
            previous: Path | None = old.workspace if old is not None else None
            if old is not None:
                old.close()  # A timeout/failure retains the owner; target cannot start.
                self.kernel = None
            if self._closing.is_set():
                return {"ok": False, "error": "desktop is closing"}
            candidate = None
            try:
                if self.catalog.resolve(key) != target:
                    raise RuntimeError("selected project target changed")
                candidate = self._construct_original(target)
                candidate.start()
            except Exception:
                if self._construction_uncertain.is_set():
                    raise  # No candidate publication, synthetic close, restore or another factory.
                if candidate is not None:
                    candidate.close()
                self.kernel = None
                if previous is not None and not self._closing.is_set():
                    restored = self._construct_original(previous)
                    restored.start()
                    return {"ok": False, "error": "target startup failed; previous project restored", "url": restored.url}
                raise
            if self._closing.is_set():
                candidate.close()
                self.kernel = None
                return {"ok": False, "error": "desktop is closing"}
            try:
                self.catalog.mark_opened(key)
            except ProjectCatalogPersistenceError:
                # Target is ALREADY the same live original kernel. Keep it, do
                # not claim a healthy catalog receipt or manufacture old restore.
                return {'ok': False, 'error': 'project catalog is unavailable', 'url': candidate.url}
            except Exception:
                # A catalog timestamp failure must not hide the actual live kernel.
                pass
            return {"ok": True, "url": candidate.url, "changed": True}
        except Exception:
            return {"ok": False, "error": "project switch failed; owned resources must finish cleanup"}
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            if self._construction_uncertain.is_set():
                raise RuntimeError('project construction cleanup is unresolved') from None
            if self.kernel is not None:
                self.kernel.close()
                self.kernel = None
