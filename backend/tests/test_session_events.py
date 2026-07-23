"""Tests for end-of-session behaviour: finalization, session_stopped and session_ended.

Covers:
- the recording is finalized (WAV closed) before session_ended advertises its URL,
- session_stopped/session_ended each fire exactly once,
- session_ended is emitted for every session — with the audio URL when recorded, and with
  url=null (but the same info + words) when record=false,
- the origin/URL helpers behind the absolute recording URL.
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


def _session(recorded=True, **overrides):
    """A session dict shaped like a live session (recorded or not).

    Carries the in-memory fields session_ended is built from: id, mode, score_threshold, the
    verse range, the sample clock (total_samples), and the confirmed-word list. Must be called
    inside a running loop when recorded=True (it starts the fake writer task).
    """
    session = {
        "id": "sess-1",
        "mode": "word_by_word",
        "score_threshold": 0.5,
        "start_chapter": 1, "start_verse": 1, "end_chapter": 1, "end_verse": 7,
        "total_samples": config.audio_sample_rate,  # 1s of audio -> duration 1000 ms
        "result_words": [],
        "streaming_task": None,
        "ended": False,
        "origin": "https://recite.example",
        "store": None, "store_queue": None, "store_task": None,
    }
    if recorded:
        queue = asyncio.Queue()
        session["store"] = FakeStore("sess-1")
        session["store_queue"] = queue
        session["store_task"] = asyncio.create_task(_fake_writer(queue))
    session.update(overrides)
    return session


@pytest.fixture
def emitted(monkeypatch):
    """Capture sio.emit calls as (event, payload) tuples."""
    calls = []

    async def fake_emit(event, payload=None, room=None):
        calls.append((event, payload))

    monkeypatch.setattr(main.sio, "emit", fake_emit)
    return calls


class TestFinalizeStore:
    """One helper replaces the three duplicated close-and-await blocks."""

    def test_closes_waits_and_clears(self):
        async def scenario():
            store = FakeStore("sess-x")
            session = _session(store=store)
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
            session = _session()
            await main._finalize_store(session)
            assert await main._finalize_store(session) is None

        asyncio.run(scenario())

    def test_unrecorded_session_returns_none(self):
        async def scenario():
            session = {"store": None, "store_queue": None, "store_task": None}
            assert await main._finalize_store(session) is None

        asyncio.run(scenario())


# A confirmed-word entry in the info.json shape session_ended reports.
_WORD = {
    "chapter_number": 1, "verse_number": 1, "word_number": 0,
    "expected_text": "و", "detected_text": "و", "status": "correct",
    "total_score": 0.9, "start_time": 100, "end_time": 400,
}


class TestEndSession:
    def test_emits_stopped_then_ended_once(self, emitted):
        """Idempotent: calling twice must not duplicate either event."""
        async def scenario():
            session = _session(recorded=True)
            await main._end_session("sid", session)
            await main._end_session("sid", session)

        asyncio.run(scenario())
        assert [event for event, _ in emitted] == ["session_stopped", "session_ended"]

    def test_recorded_payload_is_info_plus_url(self, emitted):
        async def scenario():
            session = _session(recorded=True, result_words=[_WORD])
            await main._end_session("sid", session)

        asyncio.run(scenario())
        payload = dict(emitted)["session_ended"]
        # info.json props are flattened onto the event, with the WAV URL alongside.
        assert payload["id"] == "sess-1"
        assert payload["type"] == "word_by_word"
        assert payload["narration_id"] == 1
        assert payload["score_threshold"] == 0.5
        assert payload["duration"] == 1000
        assert payload["start_chapter_number"] == 1
        assert payload["end_verse_number"] == 7
        assert payload["words"] == [_WORD]
        assert payload["url"].endswith("/api/sessions/sess-1/recording.wav")
        assert "info" not in payload and "recording_url" not in payload

    def test_unrecorded_still_emits_ended_with_null_url(self, emitted):
        """record=false still gets session_ended (info + words); only `url` is null."""
        async def scenario():
            session = _session(recorded=False, result_words=[_WORD])
            await main._end_session("sid", session)

        asyncio.run(scenario())
        assert [event for event, _ in emitted] == ["session_stopped", "session_ended"]
        payload = dict(emitted)["session_ended"]
        assert payload["url"] is None
        assert payload["id"] == "sess-1"
        assert payload["type"] == "word_by_word"
        assert payload["duration"] == 1000
        assert payload["words"] == [_WORD]

    def test_finalizes_before_advertising_the_recording(self, monkeypatch):
        """The WAV must be closed before its URL goes out, or the client reads stale RIFF
        lengths (Infinity duration, broken seeking)."""
        state = {}

        async def scenario():
            session = _session(recorded=True)
            task = session["store_task"]

            async def fake_emit(event, payload=None, room=None):
                if event == "session_ended":
                    state["writer_done_at_emit"] = task.done()

            monkeypatch.setattr(main.sio, "emit", fake_emit)
            await main._end_session("sid", session)

        asyncio.run(scenario())
        assert state["writer_done_at_emit"] is True

    def test_does_not_cancel_its_own_streaming_task(self, emitted):
        """_do_process_speech calls _end_session from inside the streaming task itself;
        cancelling it there would raise CancelledError at the next await and abort
        finalization half-way through."""
        async def scenario():
            session = _session(recorded=True)

            async def runner():
                session["streaming_task"] = asyncio.current_task()
                await main._end_session("sid", session)
                return "completed"

            return await asyncio.create_task(runner())

        assert asyncio.run(scenario()) == "completed"
        assert [event for event, _ in emitted] == ["session_stopped", "session_ended"]

    def test_cancels_a_different_streaming_task(self, emitted):
        async def scenario():
            session = _session(recorded=True)
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
