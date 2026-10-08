"""Cancellation cannot abandon an accepted local worker or its SQLite receipt."""

import asyncio
import sqlite3
import threading
from contextlib import closing, contextmanager

import pytest

from doppel_agent.runtime import create_runtime  # noqa: F401 - initialize the runtime package first
from doppel_agent.graph.nodes import FocusedGraphNodes
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger, ToolLedgerPersistenceError
from doppel_agent.provider import MockProvider, ToolCall
from doppel_agent.tools import Tool, ToolRegistry


def row(ledger):
    with closing(sqlite3.connect(ledger.database)) as db:
        return db.execute('SELECT status,result,error FROM tool_executions').fetchone()


@pytest.mark.parametrize('phase', ['begin', 'finish'])
@pytest.mark.parametrize('cancel_count', [1, 2])
def test_async_ledger_drains_owned_sqlite_before_cancellation(tmp_path, monkeypatch, phase, cancel_count):
    async def scenario():
        ledger = ToolExecutionLedger(tmp_path / 'ledger.sqlite3')
        entered, release = threading.Event(), threading.Event()
        original = ledger._connect
        connections = 0
        operations = []

        @contextmanager
        def gated():
            nonlocal connections
            connections += 1
            selected = connections == (1 if phase == 'begin' else 2)
            # Gate the accepted original SQL transaction after its body, not a
            # future admission that the cancellation fault correctly refuses.
            with original() as connection:
                yield connection
                if selected:
                    entered.set()
                    assert release.wait(10)

        monkeypatch.setattr(ledger, '_connect', gated)

        async def operation():
            operations.append('executed')
            return 'receipt'

        task = asyncio.create_task(ledger.aexecute_once('run', 'call', 'tool', {}, operation))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(.03)
            assert not task.done(), 'SQLite worker was abandoned'
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert row(ledger) == (('running', '', '') if phase == 'begin' else ('completed', 'receipt', ''))
        assert operations == ([] if phase == 'begin' else ['executed'])
        assert ledger.failed and not ledger.cleanup_uncertain
        with pytest.raises(ToolLedgerPersistenceError):
            await ledger.aexecute_once('run', 'call', 'tool', {}, operation)
        assert operations == ([] if phase == 'begin' else ['executed'])
    asyncio.run(scenario())


@pytest.mark.parametrize('with_ledger', [False, True])
def test_sync_tool_worker_drains_and_reports_actual_effect_after_cancel(tmp_path, with_ledger):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        target = tmp_path / 'effect.txt'
        registry = ToolRegistry(PermissionManager(frozenset({'workspace_write'})))

        def handler(arguments):
            entered.set()
            assert release.wait(10)
            target.write_text('applied', encoding='utf-8')
            return 'actual receipt'

        registry.register(Tool('propose_patch', 'fixture', 'workspace_write', {}, handler))
        ledger = ToolExecutionLedger(tmp_path / 'ledger.sqlite3') if with_ledger else None
        events = []

        class Sink:
            async def emit(self, kind, **payload):
                events.append((kind, payload))

        nodes = FocusedGraphNodes(MockProvider(), registry, ledger=ledger, sink=Sink())
        task = asyncio.create_task(nodes._execute_one('run', ToolCall('call', 'propose_patch', {})))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(.03)
            task.cancel()
            await asyncio.sleep(.03)
            assert not task.done(), 'Tool worker was abandoned'
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert target.read_text(encoding='utf-8') == 'applied'
        if ledger:
            assert row(ledger) == ('completed', 'actual receipt', '')
            assert events == [
                ('patch.applied', {'tool_call_id': 'call', 'result': 'actual receipt', 'cancel_requested': True}),
                ('graph.tool_finished', {'tool': 'propose_patch', 'tool_call_id': 'call', 'result': 'actual receipt', 'replayed': False, 'cancel_requested': True}),
            ]
    asyncio.run(scenario())


@pytest.mark.parametrize('async_operation', [False, True])
def test_failure_receipt_does_not_persist_raw_exception_secrets(tmp_path, async_operation):
    ledger = ToolExecutionLedger(tmp_path / 'ledger.sqlite3')
    sentinel = 'fixture-secret-do-not-persist'

    def fail():
        raise ConnectionError(sentinel)

    async def afail():
        fail()

    with pytest.raises(ConnectionError):
        if async_operation:
            asyncio.run(ledger.aexecute_once('run', 'call', 'tool', {}, afail))
        else:
            ledger.execute_once('run', 'call', 'tool', {}, fail)
    assert row(ledger) == ('failed', '', 'ConnectionError: tool execution failed')
    with pytest.raises(ValueError) as replay:
        ledger.execute_once('run', 'call', 'tool', {}, fail)
    assert sentinel not in str(replay.value)
