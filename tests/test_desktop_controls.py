from types import SimpleNamespace
from unittest.mock import patch

import pytest
import socket

from doppel_agent.desktop import WindowApi
import doppel_agent.desktop as desktop
import doppel_agent.api as api_module


def test_window_api_restore_resets_maximize_state():
    calls = []
    window = SimpleNamespace(**{name: (lambda name=name: calls.append(name))
                               for name in ('minimize', 'maximize', 'restore', 'destroy')})
    api = WindowApi()
    with patch.dict('sys.modules', {'webview': SimpleNamespace(windows=[window])}):
        for action in ('maximize', 'restore', 'maximize', 'maximize', 'minimize', 'close'):
            assert api.window_action(action)
        with pytest.raises(ValueError, match='unknown window action'):
            api.window_action('delete-files')
    assert calls == ['maximize', 'restore', 'maximize', 'restore', 'minimize', 'destroy']


def test_window_api_without_host_does_not_claim_success():
    with patch.dict('sys.modules', {'webview': SimpleNamespace(windows=[])}):
        assert WindowApi().window_action('close') is False


@pytest.mark.parametrize('blocked_port', [6000, 6566, 6665, 6669, 6697, 10080, -1])
def test_desktop_startup_never_navigates_webview_to_restricted_port(tmp_path, blocked_port):
    """Replay OS bind(0) selecting the observed unsafe port through real startup."""
    allocated = []
    urls = []
    legacy_calls = []

    class Socket:
        def __init__(self, *_args):
            self.port = 6665 if blocked_port == -1 else blocked_port if not allocated else 12345
            self.closed = False
            self.listening = False
            allocated.append(self)

        def setsockopt(self, *_args): pass
        def bind(self, address): assert address == ('127.0.0.1', 0)
        def getsockname(self): return ('127.0.0.1', self.port)
        def listen(self, backlog):
            assert backlog == 128
            self.listening = True
        def close(self): self.closed = True

    class Thread:
        def __init__(self, target, kwargs=None, **_options):
            self.target = target
            self.kwargs = kwargs or {}
        def start(self): self.target(**self.kwargs)
        def is_alive(self): return False
        def join(self, **_options): pass

    class Shown:
        def __iadd__(self, _callback): return self

    class Server:
        def __init__(self, _config): self.started = False
        def run(self, *, sockets):
            assert sockets[-1].listening and not sockets[-1].closed
            self.started = True

    legacy = SimpleNamespace(server_port=12344,
        serve_forever=lambda: legacy_calls.append('start'),
        shutdown=lambda: legacy_calls.append('shutdown'),
        server_close=lambda: legacy_calls.append('close'))

    def create_window(_title, url, **_options):
        urls.append(url)
        return SimpleNamespace(events=SimpleNamespace(shown=Shown(), closing=Shown(), closed=Shown()))

    webview = SimpleNamespace(create_window=create_window, start=lambda **_options: None)
    uvicorn = SimpleNamespace(Server=Server, Config=lambda *_args, **_options: object())
    with patch.dict('sys.modules', {'webview': webview, 'uvicorn': uvicorn}), \
            patch.object(desktop, 'ConsoleServer', return_value=legacy), \
            patch.object(desktop.socket, 'socket', Socket), \
            patch.object(desktop.threading, 'Thread', Thread), \
            patch.object(api_module, 'create_app', return_value=object()):
        if blocked_port == -1:
            with pytest.raises(RuntimeError, match='browser-compatible loopback port'):
                desktop.launch_desktop(tmp_path)
        else:
            desktop.launch_desktop(tmp_path)
    if blocked_port == -1:
        assert not urls and len(allocated) == 64
        assert all(s.closed and not s.listening for s in allocated)
        assert legacy_calls == ['start', 'shutdown', 'close']
        return
    assert urls == ['http://127.0.0.1:12345/']
    assert len(allocated) == 2 and all(s.closed for s in allocated)
    assert not allocated[0].listening
    assert legacy_calls == ['start', 'shutdown', 'close']


def test_browser_socket_rejects_consecutive_restricted_ports_without_rebinding_accepted_socket():
    ports = iter([6665, 6666, 6667, 6668, 6669, 6697, 10080, 6000, 12345])
    allocated = []

    class Socket:
        def __init__(self, *_args):
            self.port = next(ports)
            self.closed = False
            self.binds = 0
            self.listening = False
            allocated.append(self)
        def setsockopt(self, *_args): pass
        def bind(self, address):
            assert address == ('127.0.0.1', 0)
            self.binds += 1
        def getsockname(self): return ('127.0.0.1', self.port)
        def listen(self, backlog):
            assert backlog == 128
            self.listening = True
        def close(self): self.closed = True

    with patch.object(desktop.socket, 'socket', Socket):
        selected = desktop._bind_browser_api_socket()
    try:
        assert selected is allocated[-1] and selected.port == 12345
        assert not selected.closed and selected.listening
        assert all(s.closed and not s.listening for s in allocated[:-1])
        assert all(s.binds == 1 for s in allocated)
    finally:
        selected.close()


def test_browser_socket_restricted_port_exhaustion_is_bounded_and_closes_every_socket():
    allocated = []

    def socket_factory(*_args):
        s = SimpleNamespace(closed=False)
        s.setsockopt = lambda *_args: None
        s.bind = lambda address: None
        s.getsockname = lambda: ('127.0.0.1', 6665)
        s.listen = lambda _backlog: pytest.fail('must not listen on unsafe port')
        s.close = lambda: setattr(s, 'closed', True)
        allocated.append(s)
        return s

    with patch.object(desktop.socket, 'socket', socket_factory), \
            pytest.raises(RuntimeError, match='browser-compatible loopback port'):
        desktop._bind_browser_api_socket()
    assert len(allocated) == 64 and all(s.closed for s in allocated)


@pytest.mark.parametrize('failure_stage', ['setsockopt', 'bind', 'listen'])
def test_browser_socket_failed_allocation_closes_candidate(failure_stage):
    failure = OSError('local allocation failed')
    s = SimpleNamespace(closed=False, setsockopt=lambda *_args: None,
        bind=lambda _address: None, getsockname=lambda: ('127.0.0.1', 12345), listen=lambda _backlog: None)
    s.close = lambda: setattr(s, 'closed', True)

    def fail(*_args): raise failure

    setattr(s, failure_stage, fail)
    with patch.object(desktop.socket, 'socket', return_value=s), pytest.raises(OSError) as raised:
        desktop._bind_browser_api_socket()
    assert raised.value is failure and s.closed


def test_browser_socket_is_real_bound_loopback_listener_and_closes_normally():
    selected = desktop._bind_browser_api_socket()
    try:
        host, port = selected.getsockname()
        assert host == '127.0.0.1' and port > 0
        assert port not in desktop._CHROMIUM_RESTRICTED_PORTS
        assert selected.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        assert selected.fileno() >= 0
    finally:
        selected.close()
    assert selected.fileno() == -1
