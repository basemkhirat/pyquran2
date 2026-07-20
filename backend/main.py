import asyncio
import hmac
import logging
import os
import secrets
import time
import uuid
from typing import Dict, Any

import numpy as np
import socketio
from socketio.exceptions import ConnectionRefusedError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import config
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


@app.on_event("startup")
async def startup():
    if config.enable_text_score:
        logger.info("Preloading Whisper/transcription model...")
        await asyncio.to_thread(transcriber.load_model)
        logger.info("Whisper model ready.")
    if config.enable_acoustic_score:
        logger.info("Preloading wav2vec2 model...")
        await asyncio.to_thread(acoustic_scorer.load_model)
        logger.info("Wav2vec2 model ready.")


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


@app.get("/api/sessions/{session_id}/recording")
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
        "mode": "word_by_word",  # set authoritatively in start_session
        "record": False,  # set authoritatively in start_session
        "total_samples": 0,  # session sample clock == frames written to recording.wav
        "timeline_cursor_sec": None,  # fallback per-word timing cursor (seconds into the WAV)
    }


@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    session = sessions.pop(sid, None)
    if session:
        if session.get("streaming_task"):
            session["streaming_task"].cancel()
        queue = session.get("store_queue")
        task = session.get("store_task")
        if queue is not None and task is not None:
            queue.put_nowait(("close",))
            await task


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
    session["streaming_start_idx"] = 0
    session["last_interim_index"] = None
    session["start_chapter"] = start_chapter
    session["start_verse"] = start_verse
    session["end_chapter"] = end_chapter
    session["end_verse"] = end_verse

    # Optional per-session pass/fail cutoff (0-1) sent by the client (e.g. mobile app).
    # When absent or invalid, fall back to the global SCORE_THRESHOLD config.
    score_threshold = config.score_threshold
    raw_threshold = data.get("score_threshold")
    if raw_threshold is not None:
        try:
            score_threshold = min(1.0, max(0.0, float(raw_threshold)))
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid score_threshold {raw_threshold!r} for [{sid}]; using default {config.score_threshold}"
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
    if session.get("store_task"):
        session.get("store_queue").put_nowait(_store_done)
        await session["store_task"]
        session["store_task"] = None
        session["store_queue"] = None
        session["store"] = None

    # Session id is always generated; the optional store persists data only when `record`
    session_id = str(uuid.uuid4())
    session["store"] = None
    session["store_queue"] = None
    session["store_task"] = None
    if record:
        store = SessionStore(
            session_id=session_id,
            mode=session["mode"],
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
        f"score_threshold={session['score_threshold']}, "
        f"record={record}, id={session_id})"
    )
    await sio.emit("session_started", {
        "id": session_id,
        "record": record,
    }, room=sid)


@sio.event
async def audio_chunk(sid, data):
    """Process incoming PCM16 audio chunk."""
    session = sessions.get(sid)
    if not session or not session["words"]:
        return

    # Append to session recording in background (non-blocking) and advance the session
    # sample clock in lockstep, so total_samples == frames written to recording.wav.
    queue = session.get("store_queue")
    if queue is not None:
        queue.put_nowait(("audio", data))
        session["total_samples"] = session.get("total_samples", 0) + len(data) // 2

    idx = session["current_index"]
    if idx >= len(session["words"]):
        await sio.emit("session_stopped", {}, room=sid)
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

    if session["current_index"] >= len(session["words"]):
        await sio.emit("session_stopped", {}, room=sid)


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

    # Signal store writer to close and wait for it (so WAV is finalized)
    queue = session.get("store_queue")
    task = session.get("store_task")
    if queue is not None and task is not None:
        queue.put_nowait(("close",))
        await task
        session["store_task"] = None
        session["store_queue"] = None
        session["store"] = None

    await sio.emit("session_stopped", {}, room=sid)


# ===================== Streaming Transcription Loop =====================

async def _streaming_transcription_loop(sid: str):
    """Periodically transcribe accumulated audio during speech.

    Runs every streaming_interval_ms. Emits interim word_result events.
    The last word in each cycle is marked is_interim=True (may self-correct).
    When speech ends (VAD detects silence), runs one final pass and stops.
    """
    interval = config.streaming_interval_ms / 1000.0

    try:
        while True:
            await asyncio.sleep(interval)

            session = sessions.get(sid)
            if not session or session["current_index"] >= len(session["words"]):
                return

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

            # Skip if already transcribing
            if session.get("transcribing"):
                if speech_ended:
                    # Wait a bit and retry
                    await asyncio.sleep(0.2)
                    if session.get("transcribing"):
                        return
                else:
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
                    session["vad"].reset()
                    session["timeline_cursor_sec"] = None
                    session["last_interim_index"] = None
                    session["streaming_task"] = None
                    session["streaming_start_idx"] = session["current_index"]
                    # End this loop; the next audio_chunk spawns a fresh streaming
                    # task (in detecting mode if unmatched, reciting mode if matched).
                    return
                continue

            await _process_speech(sid, audio, is_final=speech_ended, captured_total=captured_total)

            if speech_ended:
                # Reset VAD for next utterance and restart loop
                session["vad"].reset()
                session["timeline_cursor_sec"] = None
                session["last_interim_index"] = None
                session["streaming_task"] = None
                session["streaming_start_idx"] = session["current_index"]
                return

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(f"Streaming transcription error for [{sid}]")


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
    score_threshold = session.get("score_threshold", config.score_threshold)
    if idx >= len(words):
        await sio.emit("session_stopped", {}, room=sid)
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

    # Run transcription based on enabled scoring methods
    logger.info(f"Processing {audio_duration:.2f}s of audio for [{sid}] ({'final' if is_final else 'interim'})...")
    logger.info(f"  Expected word #{idx}: '%s'", display_arabic(current_word["uthmani_text"]))
    t0 = time.time()
    
    text = ""
    acoustic_scores_full: list[float] = []
    acoustic_decoded_full: list[str] = []
    acoustic_offsets_full: list = []
    n_decoded_words = 0

    if config.enable_text_score and config.enable_acoustic_score and expected_chunk_max:
        whisper_task = asyncio.to_thread(transcriber.transcribe, audio)
        wav2vec_task = asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores, audio, previous_expected_chunk, expected_chunk_max
        )
        text, ac_res = await asyncio.gather(whisper_task, wav2vec_task)
        acoustic_scores_full = ac_res.scores
        acoustic_decoded_full = ac_res.best_words
        acoustic_offsets_full = ac_res.offsets
        n_decoded_words = ac_res.n_decoded
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Whisper + wav2vec (parallel) took %.2fs", time.time() - t0)
    elif config.enable_text_score:
        text = await asyncio.to_thread(transcriber.transcribe, audio)
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Transcription took %.2fs", time.time() - t0)
    elif config.enable_acoustic_score and expected_chunk_max:
        ac_res = await asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores, audio, previous_expected_chunk, expected_chunk_max
        )
        acoustic_scores_full = ac_res.scores
        acoustic_decoded_full = ac_res.best_words
        acoustic_offsets_full = ac_res.offsets
        n_decoded_words = ac_res.n_decoded
        logger.info("  Wav2vec (acoustic only, %d decoded words) took %.2fs", n_decoded_words, time.time() - t0)

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
    if config.enable_acoustic_score and acoustic_scores_full:
        # Acoustic scores align with EXPECTED chunk, not transcribed chunk.
        # Length of expected_chunk_max was min(remaining, 20).
        n_words_chunk = min(remaining, 20)
        acoustic_scores = acoustic_scores_full[:n_words_chunk]
        acoustic_decoded = acoustic_decoded_full[:n_words_chunk]
        acoustic_offsets = acoustic_offsets_full[:n_words_chunk]

    streaming = not is_final
    n_transcribed = len(transcribed_words)

    # Score each transcribed word against expected sequence
    corrected_parts: list[str] = []
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

        # No wav2vec2 token matched this expected word. Two cases:
        if config.enable_acoustic_score and not config.enable_text_score and not ac_decoded:
            # (a) continuous mode AND a later word was recited confidently (passed) -> the reciter
            # moved past this word without a matching decode. Mark it incorrect (a flagged 0% miss)
            # and advance so scoring keeps up with what they actually recited. A merely-weak later
            # match on an interim decode is treated as the decode still catching up (see helper).
            if scorer.should_skip_forward(
                session.get("mode", "word_by_word"),
                acoustic_scores[words_processed + 1:],
                score_threshold,
                is_final,
            ):
                logger.info(
                    "  No wav2vec2 match for '%s' — reciter moved on; marking incorrect and advancing",
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
                # Not written to the timeline: the reciter moved on, so this word has no
                # spoken audio (timeline.json holds only actually-spoken words).
                idx += 1
                words_processed += 1
                continue
            # (b) word_by_word mode, or nothing ahead matched (a genuine pause/silence, or the
            # decode still catching up). Stay on the word, but still emit a word_result so the
            # client always gets an event. It is marked interim (is_interim=True) unconditionally,
            # so it never advances the index or gets persisted, and is overwritten once the word is
            # actually decoded — the client renders it as a neutral "listening" chip, not a miss.
            logger.info(
                "  No wav2vec2 match for '%s' (noise/silence) — emitting interim word_result, staying on word",
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
        if streaming:
            payload["is_interim"] = word_is_interim

        # Emit result
        if word_is_interim:
            # Interim: emit but don't advance index yet
            # If we had a previous interim at a different word, the previous one
            # was already confirmed (it's no longer the last word)
            session["last_interim_index"] = idx
            await sio.emit("word_result", payload, room=sid)
        else:
            # Confirmed word: advance index
            await sio.emit("word_result", payload, room=sid)

            # Persist confirmed spoken word (correct/incorrect) with its time span in
            # recording.wav. Primary timing from wav2vec2 CTC offsets; proportional
            # fallback (split the segment span by text length) when no offset is available.
            queue = session.get("store_queue")
            if queue is not None:
                ac_off = (
                    acoustic_offsets[words_processed]
                    if config.enable_acoustic_score and words_processed < len(acoustic_offsets)
                    else None
                )
                if ac_off is not None:
                    w_start = seg_start_sec + ac_off[0]
                    w_end = seg_start_sec + ac_off[1]
                else:
                    remaining_chars = sum(len(w) for w in transcribed_words[i:]) or 1
                    frac = (len(t_word) or 1) / remaining_chars
                    w_start = cursor_sec
                    w_end = cursor_sec + (seg_end_sec - cursor_sec) * frac
                # Keep entries monotonic and within the segment.
                w_start = min(max(w_start, cursor_sec), seg_end_sec)
                w_end = min(max(w_end, w_start), seg_end_sec)
                cursor_sec = w_end
                queue.put_nowait(("word", {
                    "chapter_number": word["surah"],
                    "verse_number": word["ayah"],
                    "word_number": word["word_index"],
                    "word_text": word["uthmani_text"],
                    # What the recognizer actually heard, so playback can show it back.
                    "detected_text": payload["detected_text"],
                    "status": status,
                    "score": scores["total_score"],
                    "start_time": w_start,
                    "end_time": w_end,
                }))

            if scorer.should_advance(status, session.get("mode", "word_by_word")):
                idx += 1
                words_processed += 1
            else:
                break

    session["current_index"] = idx
    session["timeline_cursor_sec"] = cursor_sec

    if idx >= len(words):
        # Cancel streaming task if running
        if session.get("streaming_task"):
            session["streaming_task"].cancel()
            session["streaming_task"] = None
        await sio.emit("session_stopped", {}, room=sid)
