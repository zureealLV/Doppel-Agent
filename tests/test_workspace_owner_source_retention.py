"""C FIRST original workspace file/lock source definitions, ALL UNRUN.

Disposable original file/OS lock proxies only. Held reference after failed close
is NOT proof OS lock survived an effect-then-throw; no production retry/reset.
"""

from pathlib import Path

import pytest

from doppel_agent.persistence.ownership import WorkspaceOwner, WorkspaceOwnershipError
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def observe(monkeypatch, path, phase):
    original, handles, calls = Path.open, [], []

    class File:
        def __init__(self, raw):
            self.raw = raw
            handles.append(self)

        def seek(self, *args):
            if phase == "setup_close":
                raise OSError("PRIVATE_OWNER_SEEK")
            return self.raw.seek(*args)

        def tell(self):
            return self.raw.tell()

        def write(self, value):
            return self.raw.write(value)

        def flush(self):
            return self.raw.flush()

        def fileno(self):
            return self.raw.fileno()

        def close(self):
            calls.append(self)
            if phase in {"close", "setup_close"}:
                raise OSError("PRIVATE_OWNER_CLOSE")
            result = self.raw.close()
            if phase == "closed_then_throw":
                raise OSError("PRIVATE_OWNER_ALREADY_CLOSED")
            return result

    def open_file(target, *args, **kwargs):
        if target != path:
            return original(target, *args, **kwargs)
        handle = File(original(target, *args, **kwargs))
        if phase == "opaque":
            raise OSError("PRIVATE_OWNER_LOST_RETURN")
        return None if phase == "none" else handle

    monkeypatch.setattr(Path, "open", open_file)
    return handles, calls


@pytest.mark.parametrize("phase", ["close", "closed_then_throw"])
def test_original_owner_release_failure_retains_same_file_attempt_and_fences_borrow_or_second_close(
    tmp_path, monkeypatch, phase
):
    path, fault = tmp_path / "original.lock", ProviderReceiptFault()
    handles, calls = observe(monkeypatch, path, phase)
    owner = WorkspaceOwner(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    owner.acquire()
    frame = owner._source
    try:
        for action in (
            owner.release,
            owner.release,
            owner.acquire,
            owner.borrow().acquire,
            owner.borrow().release,
        ):
            with pytest.raises(WorkspaceOwnershipError) as error:
                action()
            assert error.value.source is owner and "PRIVATE_" not in str(error.value)
        assert owner.held and owner.cleanup_uncertain and owner._file is handles[0]
        assert (
            frame.handle is handles[0]
            and frame.lock_returned
            and frame.close_attempted
            and not frame.close_returned
        )
        assert owner._unresolved_sources[id(frame)] is frame and calls == [handles[0]]
        assert fault.cleanup_uncertain and fault._cleanup_sources[id(owner)] is owner
    finally:
        for handle in handles:
            handle.raw.close()  # Isolated file teardown ONLY; no owner recovery.


@pytest.mark.parametrize("phase", ["opaque", "none", "setup_close"])
def test_original_owner_acquisition_unknown_keeps_pre_factory_or_failed_setup_source(
    tmp_path, monkeypatch, phase
):
    path, fault = tmp_path / "original.lock", ProviderReceiptFault()
    handles, calls = observe(monkeypatch, path, phase)
    owner = WorkspaceOwner(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    try:
        with pytest.raises(WorkspaceOwnershipError):
            owner.acquire()
        frame = owner._source
        assert frame.open_attempted and frame.open_returned is (phase != "opaque")
        assert owner.cleanup_uncertain and fault.cleanup_uncertain and not owner.held
        if phase == "setup_close":
            assert frame.handle is handles[0] and frame.close_attempted and calls == [handles[0]]
        else:
            assert frame.handle is None and not frame.close_attempted and calls == []
        for action in (owner.acquire, owner.release, owner.borrow().acquire):
            with pytest.raises(WorkspaceOwnershipError):
                action()
        assert len(handles) == 1 and fault._cleanup_sources[id(owner)] is owner
    finally:
        for handle in handles:
            handle.raw.close()


def test_original_known_lock_refusal_closes_same_handle_and_allows_later_explicit_acquisition(tmp_path):
    path = tmp_path / "original.lock"
    first, other = WorkspaceOwner(path), WorkspaceOwner(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            other.acquire()
        original = other._source
        assert original.close_attempted and original.close_returned and not original.lock_returned
        assert not other.cleanup_uncertain and not other.held
        first.release()
        other.acquire()
        assert other.held and other._source is not original and not other.cleanup_uncertain
        other.release()
    finally:
        first.release()
        other.release()


def test_original_owner_close_reentry_fences_acquire_and_late_close_return_cannot_clear_fault(
    tmp_path, monkeypatch
):
    path, fault = tmp_path / "original.lock", ProviderReceiptFault()
    handles, calls = observe(monkeypatch, path, "healthy")
    owner = WorkspaceOwner(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    owner.acquire()
    frame, handle = owner._source, handles[0]

    def close():
        calls.append(handle)
        with pytest.raises(WorkspaceOwnershipError):
            owner.acquire()
        return handle.raw.close()  # Late original return isn't authority to clear the earlier fault.

    monkeypatch.setattr(handle, "close", close)
    try:
        with pytest.raises(WorkspaceOwnershipError):
            owner.release()
        assert owner.cleanup_uncertain and frame.close_attempted and not frame.close_returned
        assert owner._file is handle and owner.held and calls == [handle] and fault.cleanup_uncertain
        with pytest.raises(WorkspaceOwnershipError):
            owner.release()
        assert calls == [handle]
    finally:
        handle.raw.close()


def test_original_owner_setup_cancellation_known_close_preserves_original_cancellation(tmp_path, monkeypatch):
    import asyncio

    path = tmp_path / "original.lock"
    handles, calls = observe(monkeypatch, path, "healthy")
    original_open = Path.open
    cancellation = asyncio.CancelledError("original caller cancellation")

    def open_with_cancel(target, *args, **kwargs):
        handle = original_open(target, *args, **kwargs)
        if target == path:

            def seek(*args):
                raise cancellation

            handle.seek = seek
        return handle

    monkeypatch.setattr(Path, "open", open_with_cancel)
    owner = WorkspaceOwner(path)
    try:
        with pytest.raises(asyncio.CancelledError) as error:
            owner.acquire()
        assert error.value is cancellation and not owner.cleanup_uncertain and not owner.held
        assert owner._source.close_returned and calls == [handles[0]]
        owner.release()
        assert calls == [handles[0]]
    finally:
        for handle in handles:
            handle.raw.close()


def test_original_run_service_owner_failed_release_retains_same_close_task_and_receipt_root(
    tmp_path, monkeypatch
):
    import asyncio
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.service import RunService

    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        handles, calls = observe(monkeypatch, service._owner.path, "close")
        try:
            await service.start()
            with pytest.raises(WorkspaceOwnershipError):
                await service.close()
            original = service._close_task
            with pytest.raises(WorkspaceOwnershipError):
                await service.close()
            assert service._close_task is original and not service.cleanup_complete and service._owner.held
            assert calls == [handles[0]] and service._provider_receipt_fault.cleanup_uncertain
            assert service._provider_receipt_fault._cleanup_sources[id(service._owner)] is service._owner
        finally:
            for handle in handles:
                handle.raw.close()

    asyncio.run(scenario())
