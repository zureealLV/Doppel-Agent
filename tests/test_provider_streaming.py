"""Offline streaming protocol/transport checks; no paid provider requests."""
import asyncio
import io
import json

import httpx
import pytest

from doppel_agent.provider import AsyncOpenAICompatibleProvider, Message, OpenAICompatibleProvider
from doppel_agent.provider_streaming import SSEModelStream, model_stream, stream_callback


def event(payload):
    return b'data: ' + json.dumps(payload, ensure_ascii=False).encode() + b'\n\n'


def delta(content=None, *, calls=None, finish=None):
    change = {}
    if content is not None:
        change['content'] = content
    if calls is not None:
        change['tool_calls'] = calls
    return {'choices': [{'index': 0, 'delta': change, 'finish_reason': finish}]}


def answer():
    return event(delta('你好')) + event(delta(' Lv')) + event(delta(finish='stop')) + event(
        {'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}
    ) + b'data: [DONE]\n\n'


def test_sse_byte_fragments_utf8_and_tool_call_indexes():
    parser = SSEModelStream()
    raw = event(delta('你好')) + event(delta(calls=[
        {'index': 1, 'id': 'two', 'function': {'name': 'write', 'arguments': '{"x":'}},
        {'index': 0, 'id': 'one', 'function': {'name': 'read', 'arguments': '{}'}},
    ])) + event(delta(calls=[{'index': 1, 'function': {'arguments': '2}'}}])) + event(
        delta(finish='tool_calls')) + b'data: [DONE]\n\n'
    text = []
    for byte in raw:
        text.extend(parser.feed(bytes([byte])))
    payload = parser.finish()
    assert text == ['你好']
    assert payload['choices'][0]['message']['tool_calls'][1]['function']['arguments'] == '{"x":2}'
    assert payload['choices'][0]['message']['tool_calls'][0]['id'] == 'one'


@pytest.mark.parametrize('raw', [event(delta('partial')), event(delta(finish='length')) + b'data: [DONE]\n\n',
    b'data: {broken}\n\n', b'data: {"error":{"message":"private"}}\n\n', b'data: \xff\n\n',
    event(delta('x')) + b'data: [DONE]\n\n'])
def test_invalid_truncated_or_unfinished_stream_rejected(raw):
    with pytest.raises(RuntimeError):
        parser = SSEModelStream()
        list(parser.feed(raw))
        parser.finish()


def test_stream_size_limit():
    parser = SSEModelStream(limit=30)
    with pytest.raises(RuntimeError, match='exceeds'):
        list(parser.feed(b':' * 31))


def test_context_scoped_and_reset():
    callback = lambda value: None
    assert stream_callback() is None
    with pytest.raises(ValueError):
        with model_stream(callback):
            assert stream_callback() is callback
            raise ValueError()
    assert stream_callback() is None


def test_sync_stream_is_incremental_and_closed(monkeypatch):
    seen, requests = [], []
    class Response(io.BytesIO):
        status = 200
        def read1(self, size):
            if self.tell() > 0:
                assert seen == ['你好'] or seen == ['你好', ' Lv']
            return super().read1(min(size, len(event(delta('你好')))))
    response = Response(answer())
    def open_request(request, timeout):
        requests.append(json.loads(request.data))
        return response
    monkeypatch.setattr('doppel_agent.provider.urlopen', open_request)
    with model_stream(seen.append):
        turn = OpenAICompatibleProvider('http://localhost/v1', 'fixture').next_turn([], [])
    assert turn.content == '你好 Lv' and turn.usage['prompt_tokens'] == 7
    assert seen == ['你好', ' Lv'] and response.closed
    assert requests[0]['stream'] is True
    assert requests[0]['stream_options'] == {'include_usage': True}


def test_async_stream_emits_before_next_chunk_and_awaits_sink():
    async def scenario():
        seen, requests = [], []
        class Body(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield event(delta('你好'))
                assert seen == ['你好']
                yield answer()[len(event(delta('你好'))):]
            async def aclose(self):
                self.closed = True
        body = Body()
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=body)
        async def emit(text):
            await asyncio.sleep(0)
            seen.append(text)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with model_stream(emit):
                turn = await provider.anext_turn([Message('user', 'test')], [])
        assert turn.content == '你好 Lv' and seen == ['你好', ' Lv']
        assert body.closed and requests[0]['stream'] is True
    asyncio.run(scenario())


def test_async_partial_network_failure_never_retries():
    async def scenario():
        seen, requests = [], []
        class Body(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield event(delta('partial'))
                raise httpx.ReadError('private network failure')
            async def aclose(self):
                self.closed = True
        body = Body()
        async def handler(request):
            requests.append(request)
            return httpx.Response(200, stream=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with model_stream(seen.append), pytest.raises(RuntimeError, match='stream'):
                await provider.anext_turn([], [])
        assert seen == ['partial'] and len(requests) == 1 and body.closed
    asyncio.run(scenario())


def test_async_cancel_joins_original_response_close():
    async def scenario():
        started = asyncio.Event()
        class Body(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield event(delta('partial'))
                started.set()
                await asyncio.Event().wait()
            async def aclose(self):
                await asyncio.sleep(0)
                self.closed = True
        body = Body()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body))) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with model_stream(lambda text: None):
                task = asyncio.create_task(provider.anext_turn([], []))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert body.closed
    asyncio.run(scenario())


def test_sync_json_fallback_for_provider_ignoring_stream(monkeypatch):
    seen = []
    class Response(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'application/json'}
    response = Response(json.dumps({'choices': [{'message': {'content': 'fallback'}}]}).encode())
    monkeypatch.setattr('doppel_agent.provider.urlopen', lambda request, timeout: response)
    with model_stream(seen.append):
        turn = OpenAICompatibleProvider('http://localhost/v1', 'fixture').next_turn([], [])
    assert turn.content == 'fallback' and seen == ['fallback'] and response.closed


def test_async_json_fallback_for_provider_ignoring_stream():
    async def scenario():
        seen = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': 'fallback'}}]}))) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with model_stream(seen.append):
                turn = await provider.anext_turn([], [])
        assert turn.content == 'fallback' and seen == ['fallback']
    asyncio.run(scenario())


def test_async_original_usage_event_observed_without_reconstructing_response():
    from doppel_agent.provider_observation import RequestAttempt, transport_scope
    async def scenario():
        attempts, seen = [], []
        class Observer:
            def native_entered(self, provider):
                pass
            async def begin_async(self, transport):
                value = RequestAttempt('test', 1, transport)
                attempts.append(value)
                return value
            async def finish_async(self, attempt):
                pass
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=answer(), headers={'content-type': 'text/event-stream'}))) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with transport_scope(Observer()), model_stream(seen.append):
                turn = await provider.anext_turn([], [])
        assert turn.usage == {'prompt_tokens': 7, 'completion_tokens': 3}
        assert attempts[0].transport == 'httpx_sse'
        assert attempts[0].usage == {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}
        assert attempts[0].response_state == 'bounded_body'
        assert attempts[0].outcome == 'returned' and attempts[0].failure is None
    asyncio.run(scenario())


def test_async_retry_only_before_first_partial_delta():
    async def scenario():
        requests, seen = [], []
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.ReadError('not exposed')
                yield b''
        def handler(request):
            requests.append(request)
            return httpx.Response(200, stream=Broken()) if len(requests) == 1 else httpx.Response(200, content=answer())
        async def sleep(delay):
            await asyncio.sleep(0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client, sleep=sleep)
            with model_stream(seen.append):
                turn = await provider.anext_turn([], [])
        assert turn.content == '你好 Lv' and len(requests) == 2 and seen == ['你好', ' Lv']
    asyncio.run(scenario())


def test_sync_local_http_first_token_before_response_completion():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread
    observed = Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(event(delta('你好')))
            self.wfile.flush()
            assert observed.wait(2), 'first delta must arrive before server sends the remainder'
            self.wfile.write(answer()[len(event(delta('你好'))):])
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        seen = []
        def emit(text):
            seen.append(text)
            observed.set()
        with model_stream(emit):
            turn = OpenAICompatibleProvider(f'http://127.0.0.1:{server.server_port}/v1', 'fixture').next_turn([], [])
        assert turn.content == '你好 Lv' and seen == ['你好', ' Lv']
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_sse_cr_only_line_endings():
    parser = SSEModelStream()
    assert list(parser.feed(answer().replace(b'\n', b'\r'))) == ['你好', ' Lv']
    assert parser.finish()['choices'][0]['message']['content'] == '你好 Lv'


def test_sse_reasoning_never_enters_visible_sink():
    parser = SSEModelStream()
    raw = event({'choices': [{'index': 0, 'delta': {'reasoning_content': 'private'}, 'finish_reason': None}]})
    assert list(parser.feed(raw + answer())) == ['你好', ' Lv']
    assert parser.finish()['choices'][0]['message']['content'] == '你好 Lv'


def test_sse_duplicate_json_keys_rejected():
    parser = SSEModelStream()
    with pytest.raises(RuntimeError, match='invalid provider stream'):
        list(parser.feed(b'data: {"choices": [], "choices": []}\n\n'))


def test_async_stream_limit_failure_has_accurate_observation_class():
    from doppel_agent.provider_observation import BODY_LIMIT, RequestAttempt, transport_scope
    async def scenario():
        attempts = []
        class Observer:
            def native_entered(self, provider):
                pass
            async def begin_async(self, transport):
                attempt = RequestAttempt('limit', 1, transport)
                attempts.append(attempt)
                return attempt
            async def finish_async(self, attempt):
                pass
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=b':' * (BODY_LIMIT + 1), headers={'content-type': 'text/event-stream'}))) as client:
            provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client)
            with transport_scope(Observer()), model_stream(lambda text: None), pytest.raises(RuntimeError, match='exceeds'):
                await provider.anext_turn([], [])
        assert attempts[0].failure == 'response_limit'
        assert attempts[0].usage is None
    asyncio.run(scenario())
