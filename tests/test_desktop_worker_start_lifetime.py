"""C2c2b FIRST definitions, ALL UNRUN: original kernel, synthetic worker faults.

Not real native/thread/socket/process validation. An uncertain start attempt is
not evidence that the original worker never entered; do not replace its handle.
"""

from types import SimpleNamespace

import pytest

from doppel_agent.projects.desktop_kernel import DesktopKernel


class StartFaultWorker:
    def __init__(self, *, entered):
        self.ident = 123 if entered else None
        self.live = entered
        self.starts = 0
        self.joins = 0

    def start(self):
        self.starts += 1
        raise RuntimeError("PRIVATE_START_EXCEPTION")

    def is_alive(self):
        return self.live

    def join(self, timeout):
        self.joins += 1


def kernel_fixture(tmp_path):
    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    calls = []
    service = SimpleNamespace(cleanup_complete=False)

    async def close():
        calls.append("original never-entered service cleanup")
        service.cleanup_complete = True

    service.close = close
    kernel.app = SimpleNamespace(state=SimpleNamespace(run_service=service, runtime_lifespan_entered=False))
    kernel.api = SimpleNamespace(should_exit=False)
    kernel.api_socket = SimpleNamespace(close=lambda: calls.append("original socket close"))
    return kernel, calls


@pytest.mark.parametrize(
    "worker_name, started_flag",
    [
        ("api_worker", "_api_started"),
        ("legacy_worker", "_legacy_started"),
        ("_legacy_closer", "_legacy_closer_started"),
    ],
)
def test_start_exception_after_original_worker_entry_still_joins_same_handle(
    tmp_path, worker_name, started_flag
):
    kernel, calls = kernel_fixture(tmp_path)
    original = StartFaultWorker(entered=True)
    setattr(kernel, worker_name, original)
    try:
        with pytest.raises(RuntimeError, match="PRIVATE_START_EXCEPTION"):
            kernel._start_owned_worker(worker_name, started_flag)
        assert getattr(kernel, started_flag)
        with pytest.raises(RuntimeError, match="still draining"):
            kernel.close()
        assert kernel.owner.held and calls == [] and original.starts == 1 and original.joins == 1
        original.live = False
        kernel.close()
        assert calls == ["original never-entered service cleanup", "original socket close"]
        assert not kernel.owner.held and original.starts == 1 and original.joins == 2
    finally:
        kernel.owner.release()


@pytest.mark.parametrize(
    "worker_name, started_flag",
    [
        ("api_worker", "_api_started"),
        ("legacy_worker", "_legacy_started"),
        ("_legacy_closer", "_legacy_closer_started"),
    ],
)
def test_unresolved_start_attempt_never_grants_foreign_cleanup_or_replacement(
    tmp_path, worker_name, started_flag
):
    kernel, calls = kernel_fixture(tmp_path)
    original = StartFaultWorker(entered=False)
    setattr(kernel, worker_name, original)
    try:
        with pytest.raises(RuntimeError):
            kernel._start_owned_worker(worker_name, started_flag)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="startup is unresolved") as failure:
                kernel.close()
            assert "PRIVATE" not in str(failure.value)
        assert kernel.owner.held and calls == [] and original.starts == 1 and original.joins == 0
        assert getattr(kernel, worker_name) is original
        # Later original identity is evidence of entry, NOT permission to restart.
        original.ident = 123
        kernel.close()
        assert calls == ["original never-entered service cleanup", "original socket close"]
        assert not kernel.owner.held and original.starts == 1 and original.joins == 1
    finally:
        kernel.owner.release()
