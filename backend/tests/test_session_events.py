"""Tests for end-of-session behaviour: finalization, session_stopped and session_ended.

These cover the three defects the session_ended work fixed:
- the recording was never finalized on the recitation-completed paths,
- session_stopped re-fired on every audio chunk after the last word,
- the close-and-await sequence was duplicated in three places.
"""
import asyncio
import sys
import types

import pytest

# backend.main imports backend.vad, which pulls in torch + silero_vad at module level.
# None of that is exercised here, so stand in a stub before importing main.
if "backend.vad" not in sys.modules:
    _vad_stub = types.ModuleType("backend.vad")

    class _StubVAD:
        def __init__(self, *args, **kwargs):
            pass

    _vad_stub.VADProcessor = _StubVAD
    sys.modules["backend.vad"] = _vad_stub

from backend import main  # noqa: E402
from backend.config import config  # noqa: E402


class FakeStore:
    """Stands in for SessionStore — _end_session only reads .session_id."""

    def __init__(self, session_id="sess-1"):
        self.session_id = session_id


async def _fake_writer(queue):
    """Mimics _store_writer_loop's termination contract: return on ("close",)."""
    while True:
        item = await queue.get()
        if isinstance(item, tuple) and item[0] == "close":
            return


def _recorded_session(store=None):
    """A session dict shaped like a live recorded session."""
    queue = asyncio.Queue()
    task = asyncio.create_task(_fake_writer(queue))
    return {
        "store": store or FakeStore(),
        "store_queue": queue,
        "store_task": task,
        "streaming_task": None,
        "ended": False,
        "origin": "https://recite.example",
    }


@pytest.fixture
def emitted(monkeypatch):
    """Capture sio.emit calls as (event, payload) tuples."""
    calls = []

    async def fake_emit(event, payload=None, room=None):
        calls.append((event, payload))

    monkeypatch.setattr(main.sio, "emit", fake_emit)
    return calls


@pytest.fixture
def stub_reader(monkeypatch):
    """Keep session_ended off the filesystem; shaped like a real info.json."""
    monkeypatch.setattr(main.session_reader, "load_info", lambda sid: {
        "id": sid,
        "type": "word_by_word",
        "narration_id": 1,
        "score_threshold": 0.5,
        "duration": 4321,
        "start_chapter_number": 1,
        "start_verse_number": 1,
        "end_chapter_number": 1,
        "end_verse_number": 7,
        "words": [],
    })


class TestFinalizeStore:
    """Bug 3: one helper replaces the three duplicated close-and-await blocks."""

    def test_closes_waits_and_clears(self):
        async def scenario():
            store = FakeStore("sess-x")
            session = _recorded_session(store)
            task = session["store_task"]

            returned = await main._finalize_store(session)

            assert returned is store
            assert task.done(), "must await the writer so the WAV header is finalized"
            assert session["store"] is None
            assert session["store_queue"] is None
            assert session["store_task"] is None

        asyncio.run(scenario())

    def test_second_call_is_a_no_op(self):
        async def scenario():
            session = _recorded_session()
            await main._finalize_store(session)
            assert await main._finalize_store(session) is None

        asyncio.run(scenario())

    def test_unrecorded_session_returns_none(self):
        async def scenario():
            session = {"store": None, "store_queue": None, "store_task": None}
            assert await main._finalize_store(session) is None

        asyncio.run(scenario())


class TestEndSession:
    def test_emits_stopped_then_ended_once(self, emitted, stub_reader):
        """Bug 2: calling twice must not duplicate either event."""
        async def scenario():
            session = _recorded_session()
            await main._end_session("sid", session)
            await main._end_session("sid", session)

        asyncio.run(scenario())

        events = [event for event, _ in emitted]
        assert events == ["session_stopped", "session_ended"]

    def test_finalizes_before_advertising_the_recording(self, monkeypatch, stub_reader):
        """Bug 1: the WAV must be closed before its URL goes out, or the client reads
        stale RIFF lengths (Infinity duration, broken seeking)."""
        state = {}

        async def scenario():
            session = _recorded_session()
            task = session["store_task"]

            async def fake_emit(event, payload=None, room=None):
                if event == "session_ended":
                    state["writer_done_at_emit"] = task.done()
                    state["payload"] = payload

            monkeypatch.setattr(main.sio, "emit", fake_emit)
            await main._end_session("sid", session)

        asyncio.run(scenario())

        assert state["writer_done_at_emit"] is True
        payload = state["payload"]
        # info.json is flattened onto the payload, with the audio URL alongside it.
        assert payload["id"] == "sess-1"
        assert payload["type"] == "word_by_word"
        assert payload["narration_id"] == 1
        assert payload["score_threshold"] == 0.5
        assert payload["duration"] == 4321
        assert payload["start_chapter_number"] == 1
        assert payload["words"] == []
        assert payload["url"].endswith("/api/sessions/sess-1/recording.wav")
        assert "info" not in payload and "recording_url" not in payload

    def test_unrecorded_session_emits_only_stopped(self, emitted, stub_reader):
        async def scenario():
            session = {
                "store": None, "store_queue": None, "store_task": None,
                "streaming_task": None, "ended": False,
            }
            await main._end_session("sid", session)

        asyncio.run(scenario())

        assert [event for event, _ in emitted] == ["session_stopped"]

    def test_does_not_cancel_its_own_streaming_task(self, emitted, stub_reader):
        """_do_process_speech calls _end_session from inside the streaming task itself;
        cancelling it there would raise CancelledError at the next await and abort
        finalization half-way through."""
        async def scenario():
            session = _recorded_session()

            async def runner():
                session["streaming_task"] = asyncio.current_task()
                await main._end_session("sid", session)
                return "completed"

            return await asyncio.create_task(runner())

        assert asyncio.run(scenario()) == "completed"
        assert [event for event, _ in emitted] == ["session_stopped", "session_ended"]

    def test_cancels_a_different_streaming_task(self, emitted, stub_reader):
        async def scenario():
            session = _recorded_session()
            started = asyncio.Event()

            async def never_ends():
                started.set()
                await asyncio.sleep(3600)

            streaming = asyncio.create_task(never_ends())
            await started.wait()
            session["streaming_task"] = streaming

            await main._end_session("sid", session)
            await asyncio.sleep(0)  # let the cancellation land
            return streaming

        streaming = asyncio.run(scenario())
        assert streaming.cancelled() or streaming.done()


class TestOriginFromEnviron:
    def test_host_header(self):
        environ = {"asgi.scope": {"headers": [(b"host", b"example.com")], "scheme": "http"}}
        assert main._origin_from_environ(environ) == "http://example.com"

    def test_uses_scope_scheme(self):
        environ = {"asgi.scope": {"headers": [(b"host", b"example.com")], "scheme": "https"}}
        assert main._origin_from_environ(environ) == "https://example.com"

    def test_forwarded_headers_win(self):
        environ = {"asgi.scope": {
            "headers": [
                (b"host", b"internal:8000"),
                (b"x-forwarded-host", b"api.example.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "scheme": "http",
        }}
        assert main._origin_from_environ(environ) == "https://api.example.com"

    def test_takes_first_hop_of_a_proxy_chain(self):
        environ = {"asgi.scope": {
            "headers": [
                (b"x-forwarded-host", b"api.example.com, inner.local"),
                (b"x-forwarded-proto", b"https, http"),
            ],
        }}
        assert main._origin_from_environ(environ) == "https://api.example.com"

    def test_websocket_scheme_is_normalized_to_http(self):
        # A websocket handshake scope carries scheme ws/wss; the recording URL must still be
        # a plain http(s) URL a client can GET.
        ws = {"asgi.scope": {"headers": [(b"host", b"example.com")], "scheme": "ws"}}
        wss = {"asgi.scope": {"headers": [(b"host", b"example.com")], "scheme": "wss"}}
        assert main._origin_from_environ(ws) == "http://example.com"
        assert main._origin_from_environ(wss) == "https://example.com"

    def test_forwarded_proto_beats_websocket_scheme(self):
        environ = {"asgi.scope": {
            "headers": [(b"host", b"example.com"), (b"x-forwarded-proto", b"https")],
            "scheme": "ws",
        }}
        assert main._origin_from_environ(environ) == "https://example.com"

    def test_no_headers_returns_empty(self):
        assert main._origin_from_environ({}) == ""
        assert main._origin_from_environ({"asgi.scope": {"headers": []}}) == ""


class TestAbsoluteUrl:
    def test_prefers_configured_base_url(self, monkeypatch):
        monkeypatch.setattr(config, "public_base_url", "https://cfg.example/")
        session = {"origin": "https://handshake.example"}
        assert main._absolute_url(session, "/api/x") == "https://cfg.example/api/x"

    def test_falls_back_to_handshake_origin(self, monkeypatch):
        monkeypatch.setattr(config, "public_base_url", "")
        session = {"origin": "https://handshake.example"}
        assert main._absolute_url(session, "/api/x") == "https://handshake.example/api/x"

    def test_degrades_to_relative_path(self, monkeypatch):
        monkeypatch.setattr(config, "public_base_url", "")
        assert main._absolute_url({}, "/api/x") == "/api/x"
