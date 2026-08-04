import asyncio
import hmac
import logging
import os
import secrets
import time
import uuid
from dataclasses import asdict
from typing import Dict, Any, Optional, Tuple

import numpy as np
import socketio
from socketio.exceptions import ConnectionRefusedError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import ACOUSTIC_BACKENDS, config
from backend import quran_data, scorer, session_reader, session_store
from backend.session_store import SessionStore
from backend.terminal_arabic import display_arabic
from backend.vad import VADProcessor
if config.enable_text_score:
    from backend import transcriber
if config.enable_acoustic_score:
    from backend import acoustic_scorer
    from backend import verse_detection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Worst-first, so a word carrying several errors is labelled by its most serious one.
_ERROR_TYPE_SEVERITY = ("tajweed", "tashkeel", "normal")


def _dominant_error_type(errors) -> str:
    """The most serious error_type among a word's errors, for a single UI label."""
    kinds = {e.error_type for e in errors}
    for kind in _ERROR_TYPE_SEVERITY:
        if kind in kinds:
            return kind
    return "normal"


def _default_score_threshold(model: Optional[str] = None) -> float:
    """Pass/fail cutoff for a session's acoustic backend.

    Muaalem derives scores from discrete pronunciation errors rather than a smooth CER blend,
    so its distribution is more bimodal and it carries its own cutoff.
    """
    if config.enable_acoustic_score:
        return acoustic_scorer.get_backend(model).score_threshold
    return config.score_threshold


def _uses_muaalem(session: dict) -> bool:
    """True when this session scores acoustically with muaalem (and nothing else)."""
    return (
        config.enable_acoustic_score
        and not config.enable_text_score
        and session.get("model") == "muaalem"
    )


def _restore_cached_acoustic_interim(
    session: dict,
    idx: int,
    scores: list,
    decoded: list,
    tajweed: list,
    errors: list,
    recited: list,
) -> bool:
    """Restore a current-word match that only Muaalem's final re-alignment dropped.

    Streaming and final passes decode the same accumulated utterance independently. A short
    final pass can occasionally align only the already-confirmed prefix, turning the current
    word from a valid interim match into an unmatched trailing word. Keep the newer final
    result whenever it has a match; otherwise restore the prior acoustic fields so the normal
    scoring loop can emit a confirmed replacement for the pulsing interim UI result.
    """
    cached = session.get("last_interim_acoustic")
    if session.get("last_interim_index") != idx or not cached:
        return False
    if decoded and decoded[0]:
        return False

    def replace_first(values: list, value) -> None:
        if values:
            values[0] = value
        else:
            values.append(value)

    replace_first(scores, cached["score"])
    replace_first(decoded, cached["decoded"])
    replace_first(tajweed, cached["tajweed"])
    replace_first(errors, cached["errors"])
    replace_first(recited, cached["recited"])
    return True


# --- FastAPI App ---
app = FastAPI(title="Quran Voice Recognition API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Socket.IO ---
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
_inner_socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

# ASGI CORS: reflect Origin when present (required when credentials are sent; * is invalid then).
def _cors_headers_for_scope(scope):
    origin = None
    for k, v in scope.get("headers", []):
        if k.lower() == b"origin":
            origin = v.decode("latin-1").strip()
            break
    if origin:
        return [
            (b"access-control-allow-origin", origin.encode("latin-1")),
            (b"access-control-allow-credentials", b"true"),
            (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE, OPTIONS"),
            (b"access-control-allow-headers", b"*"),
        ]
    return [
        (b"access-control-allow-origin", b"*"),
        (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE, OPTIONS"),
        (b"access-control-allow-headers", b"*"),
    ]


async def _cors_middleware(scope, receive, send):
    if scope["type"] != "http":
        await _inner_socket_app(scope, receive, send)
        return
    cors_h = _cors_headers_for_scope(scope)
    if scope["method"] == "OPTIONS":
        await send({
            "type": "http.response.start",
            "status": 204,
            "headers": cors_h + [(b"access-control-max-age", b"86400")],
        })
        await send({"type": "http.response.body", "body": b""})
        return

    async def send_with_cors(message):
        if message["type"] == "http.response.start" and "headers" in message:
            existing_keys = {h[0].lower() for h in message["headers"]}
            extra = [h for h in cors_h if h[0].lower() not in existing_keys]
            if extra:
                message["headers"] = list(message["headers"]) + extra
        await send(message)

    await _inner_socket_app(scope, receive, send_with_cors)


socket_app = _cors_middleware

# Per-session state
sessions: Dict[str, Dict[str, Any]] = {}

# Queue item: ("audio", bytes) | ("word", timeline_kwargs) | ("close",)
_store_done = object()


async def _store_writer_loop(store: SessionStore, queue: asyncio.Queue):
    """Run session store writes in a single background task so I/O never blocks the session."""
    try:
        while True:
            item = await queue.get()
            if item is _store_done or (isinstance(item, tuple) and item[0] == "close"):
                store.close_audio()
                return
            if item[0] == "audio":
                await asyncio.to_thread(store.append_audio, item[1])
            elif item[0] == "word":
                await asyncio.to_thread(store.add_timeline_word, **item[1])
    except Exception:
        logger.exception("Session store writer error")
    finally:
        store.close_audio()
        # Runs on both the clean-close and the error path. Once per session — never per
        # word, since a Volume commit is far too expensive for _flush()'s cadence.
        await asyncio.to_thread(session_store.commit)


def _origin_from_environ(environ) -> str:
    """Public origin of the handshake request, used to build absolute URLs.

    Prefers the proxy headers so a deployment behind Modal/nginx advertises the URL the
    client can actually reach, not the internal one. Returns "" when the headers give us
    nothing usable, in which case callers fall back to a relative path.
    """
    scope = environ.get("asgi.scope") or {}
    headers: Dict[str, str] = {}
    for key, value in scope.get("headers", []):
        headers[key.decode("latin-1").lower()] = value.decode("latin-1")

    host = headers.get("x-forwarded-host") or headers.get("host")
    if not host:
        return ""
    # X-Forwarded-* may carry a comma-separated proxy chain; the first hop is the original.
    host = host.split(",")[0].strip()
    if not host:
        return ""
    proto = headers.get("x-forwarded-proto") or scope.get("scheme") or "http"
    proto = proto.split(",")[0].strip() or "http"
    # The handshake scope's scheme is ws/wss for a websocket transport; map it to http/https
    # so the recording URL is a plain HTTP(S) URL a client can GET the WAV from.
    proto = {"ws": "http", "wss": "https"}.get(proto, proto)
    return f"{proto}://{host}"


def _absolute_url(session: Dict[str, Any], path: str) -> str:
    """Absolute URL for an API path: PUBLIC_BASE_URL when set, else the handshake origin."""
    base = (config.public_base_url or session.get("origin") or "").rstrip("/")
    return f"{base}{path}" if base else path


async def _finalize_store(session: Dict[str, Any]) -> Optional[SessionStore]:
    """Close the session store and await the writer, so the WAV header is finalized.

    Returns the store that was closed, or None when the session was not being recorded or
    was already finalized — safe to call more than once.
    """
    queue = session.get("store_queue")
    task = session.get("store_task")
    store = session.get("store")
    session["store"] = None
    session["store_queue"] = None
    session["store_task"] = None
    if queue is None or task is None:
        return None
    queue.put_nowait(("close",))
    await task
    return store


def _session_info(session: Dict[str, Any]) -> Dict[str, Any]:
    """The session's info.json payload, built from in-memory state.

    Works whether or not the session was recorded — a non-recorded session has no info.json
    on disk, but the same fields (metadata, duration, the spoken words) are tracked in the
    session dict. duration is derived from the sample clock, so it matches the WAV length
    when recorded. Mirrors what SessionStore writes, minus the recording itself.
    """
    rate = config.audio_sample_rate or 1
    return {
        "id": session.get("id"),
        "type": session.get("mode", "word_by_word"),
        "narration_id": 1,
        "model": session.get("model", config.acoustic_backend),
        "score_threshold": session.get("score_threshold"),
        "duration": round(session.get("total_samples", 0) / rate * 1000),
        "start_chapter_number": session.get("start_chapter"),
        "start_verse_number": session.get("start_verse"),
        "end_chapter_number": session.get("end_chapter"),
        "end_verse_number": session.get("end_verse"),
        "words": session.get("result_words", []),
    }


async def _end_session(sid: str, session: Dict[str, Any]) -> None:
    """End a session exactly once: emit session_stopped, finalize, then session_ended.

    session_stopped goes out first so the client's UI signal is never delayed by the disk
    flush. session_ended always follows (built from in-memory session state), but only after
    the store is closed — the WAV header must be finalized before its URL is advertised, or
    the client reads stale RIFF lengths (Infinity duration, broken seeking). For a session
    that was not recorded, the payload is the same shape with `url` set to null.
    """
    if session.get("ended"):
        return
    session["ended"] = True

    # _do_process_speech calls this from inside the streaming task itself; cancelling that
    # task here would raise CancelledError at the next await and abort finalization. The
    # loop exits on its own next tick via its current_index guard.
    task = session.get("streaming_task")
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    session["streaming_task"] = None

    await sio.emit("session_stopped", {}, room=sid)

    # Recorded or not, hand the client the whole session (info.json props + words) in one
    # event, so no follow-up request is needed. `url` points at the WAV only when recorded.
    store = await _finalize_store(session)
    url = None
    if store is not None and session.get("id"):
        url = _absolute_url(session, f"/api/sessions/{session['id']}/recording.wav")
    await sio.emit("session_ended", {**_session_info(session), "url": url}, room=sid)


@app.on_event("startup")
async def startup():
    if config.enable_text_score:
        logger.info("Preloading Whisper/transcription model...")
        await asyncio.to_thread(transcriber.load_model)
        logger.info("Whisper model ready.")
    if config.enable_acoustic_score:
        # Every backend, not just the default: a session that picks the other model then
        # starts scoring immediately instead of downloading weights mid-recitation.
        logger.info("Preloading acoustic models: %s...", ", ".join(sorted(ACOUSTIC_BACKENDS)))
        await asyncio.to_thread(acoustic_scorer.load_model)
        logger.info("Acoustic models ready.")


# ===================== REST Endpoints =====================

PROJECT_NAME = "Quran Voice Recognition API"


@app.get("/")
def root():
    """Health check; confirms app is up and CORS works (e.g. for Modal)."""
    return {"name": PROJECT_NAME, "status": "ok", "socket_io_path": "/socket.io"}


@app.get("/api/chapters")
def api_chapters():
    return quran_data.get_chapters()


@app.get("/api/words")
def api_words(
    start_chapter: int = Query(...),
    start_verse: int = Query(...),
    end_chapter: int = Query(...),
    end_verse: int = Query(...),
):
    return quran_data.get_words_range(start_chapter, start_verse, end_chapter, end_verse)


@app.get("/api/verse-count")
def api_verse_count(surah: int = Query(...)):
    return {"count": quran_data.get_chapter_verse_count(surah)}


# --- Recorded session playback ---------------------------------------------------------
# Reads back sessions written by SessionStore when start_session was sent with record=true.
# An unknown, malformed or unreadable id all return the same 404 so the id space cannot be
# probed for which sessions exist.

@app.get("/api/sessions/{session_id}")
def api_session(session_id: str):
    """Session metadata + display words + the recorded timeline, merged for playback."""
    payload = session_reader.build_playback(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return payload


# Both paths serve the same file. The `.wav` form is what session_ended advertises (some
# players key off the extension); the bare path is kept so existing callers keep working.
@app.get("/api/sessions/{session_id}/recording")
@app.get("/api/sessions/{session_id}/recording.wav")
def api_session_recording(session_id: str):
    """The session's WAV. FileResponse handles Range/206, which <audio> needs to seek."""
    path = session_reader.recording_path(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail="recording_not_found")
    # No filename= — that sets a Content-Disposition: attachment, and this is meant to be
    # played inline by an <audio> element, not downloaded.
    return FileResponse(path, media_type="audio/wav")


# --- Password gate --------------------------------------------------------------------
# The app password is validated here (server-side) so it never ships in the frontend
# bundle. Enabled by setting APP_PASSWORD; when unset the gate is disabled.

class LoginRequest(BaseModel):
    password: str


@app.get("/api/auth-config")
def api_auth_config():
    """Tell the frontend whether a password gate is enabled (without revealing the password)."""
    return {"password_required": bool(config.app_password)}


@app.post("/api/login")
def api_login(body: LoginRequest):
    """Validate the app password server-side; return a session token on success."""
    if config.app_password and not hmac.compare_digest(body.password, config.app_password):
        raise HTTPException(status_code=401, detail="invalid_password")
    return {"token": secrets.token_urlsafe(32)}


# ===================== Socket.IO Events =====================

@sio.event
async def connect(sid, environ, auth):
    if config.socket_auth_api_key:
        if not isinstance(auth, dict):
            raise ConnectionRefusedError("authentication_failed")
        key = auth.get("api_key") or auth.get("apiKey")
        if key != config.socket_auth_api_key:
            raise ConnectionRefusedError("authentication_failed")
    logger.info(f"Client connected: {sid}")
    sessions[sid] = {
        "words": [],
        "current_index": 0,
        "vad": VADProcessor(),
        "transcribing": False,
        "streaming_task": None,
        "last_interim_index": None,  # word index of the last interim result
        "last_interim_acoustic": None,  # last matched acoustic fields for final-pass fallback
        "mode": "word_by_word",  # set authoritatively in start_session
        "model": config.acoustic_backend,  # acoustic backend; set authoritatively in start_session
        "record": False,  # set authoritatively in start_session
        "total_samples": 0,  # session sample clock == frames written to recording.wav
        "timeline_cursor_sec": None,  # fallback per-word timing cursor (seconds into the WAV)
        "ended": False,  # guards session_stopped/session_ended to one emit per session
        "origin": _origin_from_environ(environ),  # for absolute URLs in session_ended
        "id": None,  # generated in start_session; kept here so session_ended has it
        "result_words": [],  # confirmed spoken words (info.json shape), for session_ended
    }


@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    session = sessions.pop(sid, None)
    if session:
        if session.get("streaming_task"):
            session["streaming_task"].cancel()
        # The client is gone, so nothing can be emitted — just finalize the WAV header.
        session["ended"] = True
        await _finalize_store(session)


@sio.event
async def start_session(sid, data):
    """Initialize a recitation session with selected verse range (may span chapters)."""
    start_chapter = data["start_chapter_number"]
    start_verse = data["start_verse_number"]
    end_chapter = data["end_chapter_number"]
    end_verse = data["end_verse_number"]

    words = quran_data.get_words_range(start_chapter, start_verse, end_chapter, end_verse)
    session = sessions.get(sid)
    if not session:
        await sio.emit("session_error", {"reason": "not_connected"}, room=sid)
        return
    if not words:
        await sio.emit("session_error", {"reason": "invalid_range"}, room=sid)
        return

    session["words"] = words
    session["current_index"] = 0
    session["vad"].reset()
    session["total_samples"] = 0
    session["timeline_cursor_sec"] = None
    session["result_words"] = []
    session["streaming_start_idx"] = 0
    session["last_interim_index"] = None
    session["last_interim_acoustic"] = None
    session["start_chapter"] = start_chapter
    session["start_verse"] = start_verse
    session["end_chapter"] = end_chapter
    session["end_verse"] = end_verse

    # Which acoustic model scores this session: "wav2vec2" (default) or "muaalem". The wire
    # value only selects one of a fixed set of backends -- each sources its own checkpoint
    # from config -- so an unknown name falls back to ACOUSTIC_BACKEND rather than erroring.
    raw_model = data.get("model")
    if config.enable_acoustic_score:
        model = acoustic_scorer.resolve_backend_name(raw_model)
    else:
        # acoustic_scorer isn't even imported when acoustic scoring is off (see the top of
        # this module), so record the configured name without consulting the registry.
        model = config.acoustic_backend
    if raw_model is not None and model != raw_model:
        logger.warning(f"Invalid model {raw_model!r} for [{sid}]; using {model!r}")
    session["model"] = model

    # Optional per-session pass/fail cutoff (0-1) sent by the client (e.g. mobile app).
    # When absent or invalid, fall back to the chosen backend's default cutoff.
    default_threshold = _default_score_threshold(model)
    score_threshold = default_threshold
    raw_threshold = data.get("score_threshold")
    if raw_threshold is not None:
        try:
            score_threshold = min(1.0, max(0.0, float(raw_threshold)))
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid score_threshold {raw_threshold!r} for [{sid}]; using default {default_threshold}"
            )
    session["score_threshold"] = score_threshold

    # Session mode: "word_by_word" (default) blocks on a wrong word until it passes;
    # "continuous" always scores and advances so the reciter is never held up.
    mode = data.get("mode")
    if mode not in ("word_by_word", "continuous"):
        if mode is not None:
            logger.warning(f"Invalid mode {mode!r} for [{sid}]; using 'word_by_word'")
        mode = "word_by_word"
    session["mode"] = mode

    # Whether to persist this session (info.json + recording.wav) to disk. Opt-in per
    # session; when the client omits `record`, fall back to the global SAVE_SESSION_DATA.
    raw_record = data.get("record")
    if raw_record is None:
        record = config.save_session_data
    elif isinstance(raw_record, str):
        record = raw_record.strip().lower() in ("1", "true", "yes", "on")
    else:
        record = bool(raw_record)
    session["record"] = record

    # When acoustic scoring is enabled, start in detection phase so the user
    # can begin reciting from any verse in the range.
    if config.enable_acoustic_score:
        session["phase"] = "detecting"
    else:
        session["phase"] = "reciting"

    # Cancel any existing tasks
    if session.get("streaming_task"):
        session["streaming_task"].cancel()
        session["streaming_task"] = None
    # Finalize a previous recording, in case start_session is sent twice on one connection.
    await _finalize_store(session)
    session["ended"] = False

    # Session id is always generated; the optional store persists data only when `record`
    session_id = str(uuid.uuid4())
    session["id"] = session_id
    session["store"] = None
    session["store_queue"] = None
    session["store_task"] = None
    if record:
        store = SessionStore(
            session_id=session_id,
            mode=session["mode"],
            model=session["model"],
            score_threshold=session["score_threshold"],
            start_chapter_number=start_chapter,
            start_verse_number=start_verse,
            end_chapter_number=end_chapter,
            end_verse_number=end_verse,
        )
        session["store"] = store
        queue: asyncio.Queue = asyncio.Queue()
        session["store_queue"] = queue
        session["store_task"] = asyncio.create_task(_store_writer_loop(store, queue))

    logger.info(
        f"Session started for {sid}: {start_chapter}:{start_verse} - {end_chapter}:{end_verse}, "
        f"{len(words)} words (phase={session['phase']}, mode={session['mode']}, "
        f"model={session['model']}, score_threshold={session['score_threshold']}, "
        f"record={record}, id={session_id})"
    )
    await sio.emit("session_started", {
        "id": session_id,
        "record": record,
        # Echoed so the client can confirm which backend actually scored the session, the
        # same way `record` confirms the resolved recording decision.
        "model": session["model"],
    }, room=sid)


@sio.event
async def audio_chunk(sid, data):
    """Process incoming PCM16 audio chunk."""
    session = sessions.get(sid)
    if not session or not session["words"]:
        return

    # Advance the session sample clock for every chunk — it drives per-word timing and the
    # session duration, both of which session_ended reports whether or not we record. When
    # recording it stays in lockstep with the WAV (fed the same chunks), so
    # total_samples == frames written to recording.wav.
    session["total_samples"] = session.get("total_samples", 0) + len(data) // 2
    queue = session.get("store_queue")
    if queue is not None:
        queue.put_nowait(("audio", data))

    idx = session["current_index"]
    if idx >= len(session["words"]):
        await _end_session(sid, session)
        return

    vad = session["vad"]

    vad.accumulate_chunk(data)

    if vad.speech_started and session.get("streaming_task") is None:
        logger.info(f"Streaming: starting periodic transcription for [{sid}]")
        session["streaming_task"] = asyncio.create_task(
            _streaming_transcription_loop(sid)
        )


@sio.event
async def skip_word(sid, _data=None):
    """Skip the current word."""
    session = sessions.get(sid)
    if not session or not session["words"]:
        return

    idx = session["current_index"]
    if idx >= len(session["words"]):
        return

    word = session["words"][idx]
    payload: Dict[str, Any] = {
        "chapter_number": word["surah"],
        "verse_number": word["ayah"],
        "word_number": word["word_index"],
        "status": "skipped",
        # A skipped word is never scored, so total_score is 0 and detected_text is empty.
        "total_score": 0.0,
        "expected_text": word["uthmani_text"],
        "detected_text": "",
    }
    await sio.emit("word_result", payload, room=sid)

    # A skipped word has no spoken audio, so it is not written to the timeline
    # (timeline.json holds only actually-spoken words).

    session["current_index"] += 1
    session["vad"].reset()
    session["timeline_cursor_sec"] = None
    session["streaming_start_idx"] = session["current_index"]
    session["last_interim_index"] = None
    session["last_interim_acoustic"] = None

    if session["current_index"] >= len(session["words"]):
        await _end_session(sid, session)


@sio.event
async def stop_session(sid, _data=None):
    """Stop session and flush any remaining audio."""
    session = sessions.get(sid)
    if not session:
        return

    # Cancel streaming task if running
    if session.get("streaming_task"):
        session["streaming_task"].cancel()
        session["streaming_task"] = None

    captured_total = session.get("total_samples", 0)
    segment = session["vad"].streaming_flush()
    if segment is not None and len(segment) > config.audio_sample_rate * 0.3:
        await _process_speech(sid, segment, is_final=True, captured_total=captured_total)

    await _end_session(sid, session)


# ===================== Streaming Transcription Loop =====================

def _finish_utterance(sid: str, session: Dict[str, Any]) -> None:
    """Clear per-utterance state so the next audio_chunk can start a fresh loop.

    Must run on EVERY exit path of _streaming_transcription_loop: `audio_chunk` spawns a new
    loop only when streaming_task is None, so an exit that leaves it set (a short utterance,
    a decode still in flight, an unexpected exception) stops the session being scored for the
    rest of the recording.

    Deliberately does NOT touch the VAD. `streaming_flush()` already reset it, and whatever it
    has accumulated since is the beginning of the *next* utterance — audio that arrived while
    the final decode was running. Resetting here destroyed it, so a word begun within a decode
    of the previous utterance's end reached the model with its opening syllables missing.
    """
    if sessions.get(sid) is not session or session.get("ended"):
        return
    session["timeline_cursor_sec"] = None
    session["last_interim_index"] = None
    session["last_interim_acoustic"] = None
    session["streaming_task"] = None
    session["streaming_start_idx"] = session["current_index"]


async def _streaming_transcription_loop(sid: str):
    """Periodically transcribe accumulated audio during speech.

    Runs every streaming_interval_ms. Emits interim word_result events.
    The last word in each cycle is marked is_interim=True (may self-correct).
    When speech ends (VAD detects silence), runs one final pass and stops.
    """
    interval = config.streaming_interval_ms / 1000.0
    session: Optional[Dict[str, Any]] = None
    cancelled = False

    try:
        while True:
            await asyncio.sleep(interval)

            session = sessions.get(sid)
            if not session or session["current_index"] >= len(session["words"]):
                return

            # Checked before the flush, not after: flushing hands us the only copy of the
            # utterance, so bailing out afterwards would drop it entirely. Retrying next tick
            # is lossless — the silence streak persists, so speech_ended still fires; and if
            # the reciter resumed meanwhile the streak resets and it stays one utterance.
            if session.get("transcribing"):
                continue

            vad = session["vad"]
            speech_ended = vad.detect_speech_end()

            # Get accumulated audio + snapshot the session sample clock in the same
            # synchronous step (no await between) so seg_start = captured_total - len(audio).
            if speech_ended:
                audio = vad.streaming_flush()
            else:
                audio = vad.get_accumulated_audio()
            captured_total = session.get("total_samples", 0)

            if audio is None:
                if speech_ended:
                    return
                continue

            duration = len(audio) / config.audio_sample_rate
            if duration < config.streaming_min_audio_sec:
                if speech_ended:
                    return
                continue

            # --- Detection phase: match verse start instead of scoring words ---
            if session.get("phase") == "detecting":
                await _detect_verse(sid, audio, is_final=speech_ended)
                if speech_ended:
                    # If detection just committed on this final utterance, score the
                    # SAME audio against the detected start so the words the user
                    # recited to trigger detection are graded too. Otherwise that
                    # audio is dropped and the user has to repeat the verse.
                    if session.get("phase") == "reciting":
                        await _process_speech(sid, audio, is_final=True, captured_total=captured_total)
                    # End this loop; the next audio_chunk spawns a fresh streaming
                    # task (in detecting mode if unmatched, reciting mode if matched).
                    # Teardown happens in the finally, for every exit path alike.
                    return
                continue

            await _process_speech(sid, audio, is_final=speech_ended, captured_total=captured_total)

            if speech_ended:
                # Utterance over. Stop here and let the next audio_chunk start a fresh loop;
                # the finally clears the per-utterance state.
                return

    except asyncio.CancelledError:
        # stop_session / _end_session cancelled us and owns the teardown from here — in
        # particular stop_session still reads streaming_start_idx for its own final flush.
        cancelled = True
    except Exception:
        logger.exception(f"Streaming transcription error for [{sid}]")
    finally:
        if session is not None and not cancelled:
            _finish_utterance(sid, session)


# ===================== Verse Detection =====================

async def _detect_verse(sid: str, audio: np.ndarray, is_final: bool = False):
    """Run verse detection on the audio and transition to reciting phase if matched.

    In streaming mode, emit verse_detection_failed only when is_final (speech ended)
    and no match was found, so the client is not spammed every interval.
    """
    session = sessions.get(sid)
    if not session:
        return

    if session.get("transcribing"):
        return
    session["transcribing"] = True

    try:
        result = await asyncio.to_thread(
            verse_detection.detect_start_verse,
            audio,
            session["words"],
            start_chapter=session.get("start_chapter"),
            start_verse=session.get("start_verse"),
            is_final=is_final,
            model=session.get("model"),
        )

        if result.status == "commit":
            word_index = result.word_index
            session["current_index"] = word_index
            session["streaming_start_idx"] = word_index
            session["phase"] = "reciting"
            word_number = session["words"][word_index]["word_index"]
            logger.info(
                f"Verse detected for [{sid}]: {result.chapter}:{result.ayah}, "
                f"word_number {word_number}, score {result.score:.3f}"
            )
            await sio.emit("verse_detected", {
                "chapter_number": result.chapter,
                "verse_number": result.ayah,
                "word_number": word_number,
            }, room=sid)
        elif result.status == "ambiguous":
            # Identical verses tie — do NOT guess. Stay in the detecting phase and
            # keep listening; the next (distinct) verse extends the match window and
            # resolves which occurrence the user is reciting.
            logger.info(
                f"Verse detection ambiguous for [{sid}]: {len(result.candidates)} identical "
                f"candidate(s) — waiting for the next distinct verse"
            )
        else:  # "none"
            if is_final:
                logger.info(f"Verse detection failed for [{sid}], waiting for next utterance")
                await sio.emit("verse_detection_failed", {}, room=sid)
    finally:
        session["transcribing"] = False


# ===================== Internal Helpers =====================

async def _process_speech(sid: str, audio: np.ndarray, is_final: bool = False, captured_total: int = 0):
    """Transcribe speech segment and score against expected word(s).

    captured_total is the session sample clock snapshotted when `audio` was grabbed; it
    anchors the segment (and each confirmed word's timing) to a position in recording.wav.
    """
    session = sessions.get(sid)
    if not session:
        return

    # Prevent concurrent transcriptions for the same session
    if session.get("transcribing"):
        logger.info(f"Skipping segment for [{sid}] — transcription already in progress")
        return
    session["transcribing"] = True

    try:
        await _do_process_speech(sid, session, audio, is_final, captured_total)
    finally:
        session["transcribing"] = False


async def _do_process_speech(sid: str, session: dict, audio: np.ndarray, is_final: bool = False, captured_total: int = 0):
    """Inner transcription logic (called under the transcribing guard)."""
    idx = session["current_index"]
    words = session["words"]
    model = session.get("model", config.acoustic_backend)
    score_threshold = session.get("score_threshold", _default_score_threshold(model))
    uses_muaalem = _uses_muaalem(session)
    if idx >= len(words):
        await _end_session(sid, session)
        return

    # Check minimum audio duration (0.5 seconds)
    audio_duration = len(audio) / config.audio_sample_rate
    if audio_duration < 0.5:
        logger.info(f"Audio too short ({audio_duration:.2f}s), skipping transcription")
        return

    # Timing anchor: where this segment sits inside recording.wav. Every chunk feeds both
    # the WAV and the VAD, and the VAD buffer is a contiguous suffix of the received stream,
    # so seg_start = captured_total - len(audio). cursor_sec advances as words are attributed
    # (drives the proportional timing fallback and keeps timeline entries monotonic).
    sr = config.audio_sample_rate
    seg_start_sec = max(0.0, (captured_total - len(audio)) / sr)
    seg_end_sec = seg_start_sec + audio_duration
    cursor_sec = session.get("timeline_cursor_sec")
    if cursor_sec is None:
        cursor_sec = seg_start_sec

    current_word = words[idx]
    start_idx = session.get("streaming_start_idx", idx)
    if start_idx > idx:
        start_idx = idx

    previous_expected_chunk = [
        (words[i]["emlaey_text"], words[i]["uthmani_text"])
        for i in range(start_idx, idx)
    ]

    # Build max expected chunk for parallel wav2vec when acoustic scoring is enabled
    remaining = len(words) - idx
    expected_chunk_max = (
        [
            (words[idx + i]["emlaey_text"], words[idx + i]["uthmani_text"])
            for i in range(min(remaining, 20))
        ]
        if config.enable_acoustic_score and remaining > 0
        else []
    )
    # The word dicts behind previous_expected_chunk + expected_chunk_max, in that order.
    # The muaalem backend needs surah/ayah/word_index to look up its reference text;
    # wav2vec2 ignores this.
    word_meta = (
        words[start_idx : idx + len(expected_chunk_max)]
        if expected_chunk_max
        else []
    )

    # Run transcription based on enabled scoring methods
    logger.info(f"Processing {audio_duration:.2f}s of audio for [{sid}] ({'final' if is_final else 'interim'})...")
    logger.info(f"  Expected word #{idx}: '%s'", display_arabic(current_word["uthmani_text"]))
    t0 = time.time()
    
    text = ""
    acoustic_scores_full: list[float] = []
    acoustic_decoded_full: list[str] = []
    acoustic_offsets_full: list = []
    # muaalem only; stay empty under wav2vec2 so its payload is unchanged.
    acoustic_tajweed_full: list[float] = []
    acoustic_errors_full: list[list] = []
    acoustic_recited_full: list = []
    n_decoded_words = 0

    def _take(res) -> None:
        """Spread one AcousticResult across the parallel arrays above."""
        nonlocal acoustic_scores_full, acoustic_decoded_full, acoustic_offsets_full
        nonlocal acoustic_tajweed_full, acoustic_errors_full, acoustic_recited_full
        nonlocal n_decoded_words
        acoustic_scores_full = res.scores
        acoustic_decoded_full = res.best_words
        acoustic_offsets_full = res.offsets
        acoustic_tajweed_full = res.tajweed_scores
        acoustic_errors_full = res.errors
        acoustic_recited_full = res.recited
        n_decoded_words = res.n_decoded

    if config.enable_text_score and config.enable_acoustic_score and expected_chunk_max:
        whisper_task = asyncio.to_thread(transcriber.transcribe, audio)
        wav2vec_task = asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores,
            audio, previous_expected_chunk, expected_chunk_max, word_meta, model,
        )
        text, ac_res = await asyncio.gather(whisper_task, wav2vec_task)
        _take(ac_res)
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Whisper + acoustic (parallel) took %.2fs", time.time() - t0)
    elif config.enable_text_score:
        text = await asyncio.to_thread(transcriber.transcribe, audio)
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Transcription took %.2fs", time.time() - t0)
    elif config.enable_acoustic_score and expected_chunk_max:
        ac_res = await asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores,
            audio, previous_expected_chunk, expected_chunk_max, word_meta, model,
        )
        _take(ac_res)
        logger.info(
            "  %s (acoustic only, %d decoded words) took %.2fs",
            model, n_decoded_words, time.time() - t0,
        )

    # The final Muaalem alignment can occasionally retain only the already-confirmed prefix
    # of the utterance. If it drops the exact current word that had a real interim match, reuse
    # that match and let the ordinary final scoring path confirm it. A real final current-word
    # match always wins, and the cache is scoped to this word/utterance by its session index.
    if (
        is_final
        and uses_muaalem
        and _restore_cached_acoustic_interim(
            session,
            idx,
            acoustic_scores_full,
            acoustic_decoded_full,
            acoustic_tajweed_full,
            acoustic_errors_full,
            acoustic_recited_full,
        )
    ):
        logger.info(
            "  Muaalem final pass lost current word '%s'; restoring its interim match",
            display_arabic(current_word["uthmani_text"]),
        )

    # When text scoring is disabled, use expected words as transcribed words for acoustic scoring.
    # Bound the span by the alignment itself: process expected words up to and including the last
    # one that got a decoded-token match (acoustic_decoded_full is parallel to the current chunk,
    # idx onwards). Interior unmatched words are kept (the no-match branch handles them); trailing
    # unmatched words — not recited yet — are excluded. This is robust to a previous word that got
    # no token (skipped/dropped): the old `n_decoded - len(previous)` count assumed every previous
    # word consumed a token, so a skipped one over-subtracted and dropped a genuinely-decoded
    # trailing word (e.g. بِرَبِّكَ decoded right after a skipped نُوحٍ never got an event).
    if not config.enable_text_score and config.enable_acoustic_score:
        last_matched = max(
            (i + 1 for i, w in enumerate(acoustic_decoded_full) if w),
            default=0,
        )
        transcribed_words = [
            words[idx + i]["uthmani_text"] for i in range(min(remaining, last_matched))
        ]
    elif not text:
        return
    else:
        transcribed_words = text.split()

    # Either backend aligns against a 20-word reference window, so one word farther ahead that
    # resembles what was said makes every reference word before it look attempted. In continuous
    # mode that advances the cursor through the whole false span, marking words the reciter never
    # reached as 0% misses. Only trust a bounded run of low-confidence words without a nearby
    # passing score; on interim ticks, leave the trailing weak word pending so the same
    # accumulated audio cannot advance it again on the next tick.
    #
    # Not muaalem-only: wav2vec2 hits this harder, because `should_skip_forward` accepts *any*
    # nonzero later score on a final pass and its best-match scoring rarely returns a clean zero.
    if (
        config.enable_acoustic_score
        and not config.enable_text_score
        and session.get("mode", "word_by_word") == "continuous"
        and transcribed_words
    ):
        safe_span = scorer.bounded_continuous_span(
            acoustic_scores_full[: len(transcribed_words)],
            score_threshold,
            config.continuous_max_unanchored_words,
            is_final,
        )
        if safe_span < len(transcribed_words):
            logger.info(
                "  Continuous resync guard: limiting %d aligned words to %d",
                len(transcribed_words),
                safe_span,
            )
            transcribed_words = transcribed_words[:safe_span]
            # Keep every parallel acoustic array behind the same frontier. In particular,
            # the unmatched-word branch below searches later scores for a resync anchor; if
            # it could still see the rejected distant match, repeated interim ticks would
            # advance one false word at a time and recreate the cascade more slowly.
            acoustic_scores_full = acoustic_scores_full[:safe_span]
            acoustic_decoded_full = acoustic_decoded_full[:safe_span]
            acoustic_offsets_full = acoustic_offsets_full[:safe_span]
            acoustic_tajweed_full = acoustic_tajweed_full[:safe_span]
            acoustic_errors_full = acoustic_errors_full[:safe_span]
            acoustic_recited_full = acoustic_recited_full[:safe_span]

    # --- Backtrack detection: skip repeated already-correct words (only when text scoring enabled) ---
    lookback = min(idx, len(transcribed_words)) if config.enable_text_score else 0
    skip_count = 0

    if lookback > 0:
        # Try matching the longest possible prefix of transcribed_words
        # against the already-correct words ending at idx
        # Use text_score for backtrack detection (acoustic scores not available for past words)
        score_key = "text_score" if config.pass_on_any_score else "total_score"
        for prefix_len in range(lookback, 0, -1):
            prev_slice = words[idx - prefix_len : idx]
            candidate = transcribed_words[:prefix_len]

            all_match = all(
                scorer.score_word_best(
                    prev_slice[j]["emlaey_text"],
                    prev_slice[j]["uthmani_text"],
                    candidate[j],
                    config.max_edits_for_correction,
                )[score_key]
                >= score_threshold
                for j in range(prefix_len)
            )
            if all_match:
                skip_count = prefix_len
                break

    if skip_count > 0:
        logger.info(f"  Backtrack detected: skipping {skip_count} repeated word(s)")
        transcribed_words = transcribed_words[skip_count:]

    # Slice parallel wav2vec scores to actual chunk size (after backtrack)
    acoustic_scores: list[float] = []
    acoustic_decoded: list[str] = []
    acoustic_offsets: list = []
    acoustic_tajweed: list[float] = []
    acoustic_errors: list[list] = []
    acoustic_recited: list = []
    if config.enable_acoustic_score and acoustic_scores_full:
        # Acoustic scores align with EXPECTED chunk, not transcribed chunk.
        # Length of expected_chunk_max was min(remaining, 20).
        n_words_chunk = min(remaining, 20)
        acoustic_scores = acoustic_scores_full[:n_words_chunk]
        acoustic_decoded = acoustic_decoded_full[:n_words_chunk]
        acoustic_offsets = acoustic_offsets_full[:n_words_chunk]
        # muaalem only; stay empty for wav2vec2 so its payload is unchanged.
        acoustic_tajweed = acoustic_tajweed_full[:n_words_chunk]
        acoustic_errors = acoustic_errors_full[:n_words_chunk]
        acoustic_recited = acoustic_recited_full[:n_words_chunk]

    streaming = not is_final
    n_transcribed = len(transcribed_words)

    # Score each transcribed word against expected sequence
    corrected_parts: list[str] = []

    def _proportional_span(text_len: int, from_word: int, cursor: float) -> Tuple[float, float]:
        """Char-proportional share of what's left of the segment, for a word with no offsets."""
        remaining_chars = sum(len(w) for w in transcribed_words[from_word:]) or 1
        frac = (text_len or 1) / remaining_chars
        # Monotonic and inside the segment: the cursor only ever moves forward.
        start = min(cursor, seg_end_sec)
        end = min(max(cursor + (seg_end_sec - cursor) * frac, start), seg_end_sec)
        return start, end

    def _commit_word(record: Dict[str, Any]) -> None:
        """Add a word to the session timeline, and to disk when the session is being recorded.

        `record` holds seconds; the in-memory copy is converted to the info.json shape (ms +
        rounded score) so `session_ended` and the stored file agree.
        """
        session["result_words"].append({
            **record,
            "total_score": round(record["total_score"], 3),
            "start_time": round(record["start_time"] * 1000),
            "end_time": round(record["end_time"] * 1000),
        })
        queue = session.get("store_queue")
        if queue is not None:
            queue.put_nowait(("word", record))

    words_processed = 0
    for i, t_word in enumerate(transcribed_words):
        if idx >= len(words):
            break

        word = words[idx]
        scores = scorer.score_word_best(
            word["emlaey_text"],
            word["uthmani_text"],
            t_word,
            config.max_edits_for_correction,
        )
        t_corrected = scores.pop("t_corrected")
        ds = scores["diacritic_score"]
        ts = scores["text_score"]
        
        # In expected_chunk_max, `word` is at index 0 initially.
        # Since `idx` advances by 1 for each correct word, the offset into the original
        # `acoustic_scores` array (from the start of the current chunk) is `words_processed`.
        ac = (acoustic_scores[words_processed] if words_processed < len(acoustic_scores) else None) if config.enable_acoustic_score else None
        ac_decoded = (acoustic_decoded[words_processed] if words_processed < len(acoustic_decoded) else None) if config.enable_acoustic_score else None
        # muaalem only; None/[] under wav2vec2, which leaves them out of the payload entirely.
        ac_tajweed = acoustic_tajweed[words_processed] if words_processed < len(acoustic_tajweed) else None
        ac_word_errors = acoustic_errors[words_processed] if words_processed < len(acoustic_errors) else []
        ac_recited = acoustic_recited[words_processed] if words_processed < len(acoustic_recited) else None

        # No acoustic token matched this expected word. Two cases:
        if config.enable_acoustic_score and not config.enable_text_score and not ac_decoded:
            # (a) continuous mode AND a later word was recited confidently (passed) -> the reciter
            # moved past this word without a matching decode. Mark it incorrect (a flagged 0% miss)
            # and advance so scoring keeps up with what they actually recited. A merely-weak later
            # match on an interim decode is treated as the decode still catching up (see helper).
            #
            # A later match is the ONLY evidence accepted here. A final pass that aligned more
            # reference words than its last interim used to count as "evidence of a spoken
            # substitution", but that was unsound: this branch only runs for a word that did not
            # align, so it contributed nothing to that count — every extra alignment came from
            # some *other* word and says nothing about this one. It fired on an ordinary pause:
            # سَاهُونَ merely finished decoding between the last interim and the final (4 -> 6
            # aligned words), which burned the following ٱلَّذِينَ before the reciter had spoken
            # it and shifted every word after it by one (session 185e6594).
            has_later_anchor = scorer.should_skip_forward(
                session.get("mode", "word_by_word"),
                acoustic_scores[words_processed + 1:],
                score_threshold,
                is_final,
            )
            if has_later_anchor:
                logger.info(
                    "  No acoustic match for '%s' — reciter moved on; marking incorrect and advancing",
                    display_arabic(word["uthmani_text"]),
                )
                missed_payload = {
                    "chapter_number": word["surah"],
                    "verse_number": word["ayah"],
                    "word_number": word["word_index"],
                    "status": "incorrect",
                    "total_score": 0.0,
                    "expected_text": word["uthmani_text"],
                    "detected_text": "",
                }
                if streaming:
                    missed_payload["is_interim"] = False
                await sio.emit("word_result", missed_payload, room=sid)
                # Recorded like any other confirmed word. It usually *was* recited — the aligner
                # just could not attribute a decode to it, and its phonemes surface as inserts on
                # the next word — so leaving it out made the session claim it was never read, and
                # handed its share of the segment to the following word, shifting every highlight
                # after it by one. It has no offsets, so it takes the same char-proportional slice
                # the fallback gives any unaligned word.
                w_start, w_end = _proportional_span(len(word["uthmani_text"]), i, cursor_sec)
                cursor_sec = w_end
                missed_record = {
                    "chapter_number": word["surah"],
                    "verse_number": word["ayah"],
                    "word_number": word["word_index"],
                    "expected_text": word["uthmani_text"],
                    "detected_text": "",
                    "status": "incorrect",
                    "total_score": 0.0,
                    "start_time": w_start,
                    "end_time": w_end,
                }
                # No decode reached this word, so there is no per-phoneme detail to store. The
                # key is still written for a muaalem session, where every entry carries it.
                if uses_muaalem:
                    missed_record["errors"] = []
                _commit_word(missed_record)
                idx += 1
                words_processed += 1
                continue
            # (b) word_by_word mode, or nothing ahead matched (a genuine pause/silence, or the
            # decode still catching up). Stay on the word, but still emit a word_result so the
            # client always gets an event. It is marked interim (is_interim=True) unconditionally,
            # so it never advances the index or gets persisted, and is overwritten once the word is
            # actually decoded — the client renders it as a neutral "listening" chip, not a miss.
            logger.info(
                "  No acoustic match for '%s' (noise/silence) — emitting interim word_result, staying on word",
                display_arabic(word["uthmani_text"]),
            )
            await sio.emit("word_result", {
                "chapter_number": word["surah"],
                "verse_number": word["ayah"],
                "word_number": word["word_index"],
                "status": "incorrect",
                "total_score": 0.0,
                "expected_text": word["uthmani_text"],
                "detected_text": "",
                "is_interim": True,
            }, room=sid)
            break

        scores["total_score"] = round(
            scorer.compute_total_score(scores["char_score"], ds, ac), 3
        )
        
        # Determine if word is correct based on scoring mode
        if config.pass_on_any_score:
            text_pass = config.enable_text_score and ts >= score_threshold
            acoustic_pass = ac is not None and ac >= score_threshold
            status = "correct" if (text_pass or acoustic_pass) else "incorrect"
        else:
            status = "correct" if scores["total_score"] >= score_threshold else "incorrect"

        # In streaming mode, mark the last word as interim (it may self-correct)
        word_is_interim = streaming and (i == n_transcribed - 1)
        logger.info(
            "  Word score expected='%s' decoded='%s' total=%.3f status=%s interim=%s",
            display_arabic(word["uthmani_text"]),
            # Log the real wav2vec2 match, not the acoustic-only placeholder t_word (== expected).
            display_arabic(ac_decoded or t_word),
            scores["total_score"],
            status,
            word_is_interim,
        )

        corrected_parts.append(t_corrected)
        payload: Dict[str, Any] = {
            "chapter_number": word["surah"],
            "verse_number": word["ayah"],
            "word_number": word["word_index"],
            "status": status,
            "total_score": scores["total_score"],
            "expected_text": word["uthmani_text"],
            # Prefer the wav2vec2 decode; fall back to the Whisper transcription
            # when acoustic scoring is off (text-only mode). In acoustic-only mode
            # t_word is a placeholder (== expected), so it's excluded. "" only when
            # neither a decode nor a text transcription is available for this word.
            "detected_text": ac_decoded or (t_word if config.enable_text_score else ""),
        }
        # muaalem extras. Absent entirely under wav2vec2, which cannot measure any of them,
        # so its word_result payload is byte-identical to before this backend existed.
        if ac_word_errors:
            payload["errors"] = [asdict(e) for e in ac_word_errors]
            payload["error_type"] = _dominant_error_type(ac_word_errors)
        if ac_tajweed is not None:
            payload["tajweed_score"] = round(ac_tajweed, 3)
        if ac_recited is not None:
            payload["recited"] = asdict(ac_recited)
        if streaming:
            payload["is_interim"] = word_is_interim

        # Emit result
        if word_is_interim:
            # Interim: emit but don't advance index yet
            # If we had a previous interim at a different word, the previous one
            # was already confirmed (it's no longer the last word)
            session["last_interim_index"] = idx
            if ac_decoded:
                session["last_interim_acoustic"] = {
                    "score": ac,
                    "decoded": ac_decoded,
                    "tajweed": ac_tajweed,
                    "errors": ac_word_errors,
                    "recited": ac_recited,
                }
            await sio.emit("word_result", payload, room=sid)
        else:
            # Confirmed word: advance index
            await sio.emit("word_result", payload, room=sid)

            if session.get("last_interim_index") == idx:
                session["last_interim_index"] = None
                session["last_interim_acoustic"] = None

            # Time the confirmed word against the session audio: primary from wav2vec2 CTC
            # offsets, fallback a proportional split of the segment span. Done for every
            # session (recorded or not) so session_ended can carry the word timeline either way.
            ac_off = (
                acoustic_offsets[words_processed]
                if config.enable_acoustic_score and words_processed < len(acoustic_offsets)
                else None
            )
            if ac_off is not None:
                w_start = seg_start_sec + ac_off[0]
                w_end = seg_start_sec + ac_off[1]
                # Keep entries monotonic and within the segment.
                w_start = min(max(w_start, cursor_sec), seg_end_sec)
                w_end = min(max(w_end, w_start), seg_end_sec)
            else:
                w_start, w_end = _proportional_span(len(t_word), i, cursor_sec)
            cursor_sec = w_end

            word_record = {
                "chapter_number": word["surah"],
                "verse_number": word["ayah"],
                "word_number": word["word_index"],
                "expected_text": word["uthmani_text"],
                # What the recognizer actually heard, so playback can show it back.
                "detected_text": payload["detected_text"],
                "status": status,
                "total_score": scores["total_score"],
                "start_time": w_start,
                "end_time": w_end,
            }
            # muaalem extras, so a recorded session replays the same error detail the live
            # view showed. Stored flatter than the live payload: the recited unit is kept as
            # its two phoneme strings, and the tajweed score / dominant type are dropped since
            # both are derivable from `errors`. Unlike the live payload, `errors` is written
            # for every muaalem word — empty when the word was clean, so consumers can read
            # it unconditionally. wav2vec2 entries stay as they were: it measures no errors
            # at all, which is not the same claim as an empty list.
            if uses_muaalem:
                word_record["errors"] = payload.get("errors", [])
            if ac_recited is not None:
                word_record["detected_ph"] = ac_recited.ph
                word_record["expected_ph"] = ac_recited.expected_ph
            _commit_word(word_record)

            if scorer.should_advance(status, session.get("mode", "word_by_word")):
                idx += 1
                words_processed += 1
            else:
                break

    session["current_index"] = idx
    session["timeline_cursor_sec"] = cursor_sec

    if idx >= len(words):
        # _end_session cancels the streaming task safely — this runs inside that very task.
        await _end_session(sid, session)
