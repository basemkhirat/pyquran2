import asyncio
import logging
import os
import time
import uuid
from typing import Dict, Any

import numpy as np
import socketio
from socketio.exceptions import ConnectionRefusedError
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.config import config
from backend import quran_data, scorer
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
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

# Per-session state
sessions: Dict[str, Dict[str, Any]] = {}

# Queue item: ("audio", bytes) | ("word", kwargs) | ("close",)
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
                await asyncio.to_thread(store.add_word, **item[1])
    except Exception:
        logger.exception("Session store writer error")
    finally:
        store.close_audio()


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
    session["streaming_start_idx"] = 0
    session["last_interim_index"] = None
    session["start_chapter"] = start_chapter
    session["start_verse"] = start_verse
    session["end_chapter"] = end_chapter
    session["end_verse"] = end_verse

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

    # Session UUID is always generated; optional store persists data when config.save_session_data
    session_uuid = str(uuid.uuid4())
    session["store"] = None
    session["store_queue"] = None
    session["store_task"] = None
    if config.save_session_data:
        store = SessionStore(session_uuid=session_uuid)
        session["store"] = store
        queue: asyncio.Queue = asyncio.Queue()
        session["store_queue"] = queue
        session["store_task"] = asyncio.create_task(_store_writer_loop(store, queue))

    logger.info(
        f"Session started for {sid}: {start_chapter}:{start_verse} - {end_chapter}:{end_verse}, "
        f"{len(words)} words (phase={session['phase']}, save_session_data={config.save_session_data}, uuid={session_uuid})"
    )
    await sio.emit("session_started", {
        "session_uuid": session_uuid,
    }, room=sid)


@sio.event
async def audio_chunk(sid, data):
    """Process incoming PCM16 audio chunk."""
    session = sessions.get(sid)
    if not session or not session["words"]:
        return

    # Append to session recording in background (non-blocking)
    queue = session.get("store_queue")
    if queue is not None:
        queue.put_nowait(("audio", data))

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
    }
    if config.send_word_result_details:
        payload["transcribed"] = ""
        payload["expected"] = word["emlaey_text"]
        payload["char_score"] = 0.0
        payload["diacritic_score"] = 0.0
        payload["text_score"] = 0.0
        payload["total_score"] = 0.0
    await sio.emit("word_result", payload, room=sid)

    # Persist skipped word in background (non-blocking)
    queue = session.get("store_queue")
    if queue is not None:
        queue.put_nowait(("word", {
            "chapter_number": word["surah"],
            "verse_number": word["ayah"],
            "word_number": word["word_index"],
            "word_text": word["emlaey_text"],
            "score": 0.0,
            "status": "skipped",
        }))

    session["current_index"] += 1
    session["vad"].reset()
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

    segment = session["vad"].streaming_flush()
    if segment is not None and len(segment) > config.audio_sample_rate * 0.3:
        await _process_speech(sid, segment, is_final=True)

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

            # Get accumulated audio
            if speech_ended:
                audio = vad.streaming_flush()
            else:
                audio = vad.get_accumulated_audio()

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
                    session["vad"].reset()
                    session["streaming_task"] = None
                    # If still detecting, restart the loop by returning
                    # (the next audio_chunk will spawn a new streaming task)
                    if session.get("phase") == "detecting":
                        return
                    # If detection succeeded, fall through to continue the loop
                    # in reciting mode with fresh VAD state
                continue

            await _process_speech(sid, audio, is_final=speech_ended)

            if speech_ended:
                # Reset VAD for next utterance and restart loop
                session["vad"].reset()
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
        )

        if result is not None:
            chapter, ayah, word_index, score = result
            session["current_index"] = word_index
            session["streaming_start_idx"] = word_index
            session["phase"] = "reciting"
            logger.info(
                f"Verse detected for [{sid}]: {chapter}:{ayah}, word_index {word_index}, score {score:.3f}"
            )
            await sio.emit("verse_detected", {
                "chapter_number": chapter,
                "verse_number": ayah,
                "word_index": word_index,
                "score": round(score, 3),
            }, room=sid)
        else:
            if is_final:
                logger.info(f"Verse detection failed for [{sid}], waiting for next utterance")
                await sio.emit("verse_detection_failed", {}, room=sid)
    finally:
        session["transcribing"] = False


# ===================== Internal Helpers =====================

async def _process_speech(sid: str, audio: np.ndarray, is_final: bool = False):
    """Transcribe speech segment and score against expected word(s)."""
    session = sessions.get(sid)
    if not session:
        return

    # Prevent concurrent transcriptions for the same session
    if session.get("transcribing"):
        logger.info(f"Skipping segment for [{sid}] — transcription already in progress")
        return
    session["transcribing"] = True

    try:
        await _do_process_speech(sid, session, audio, is_final)
    finally:
        session["transcribing"] = False


async def _do_process_speech(sid: str, session: dict, audio: np.ndarray, is_final: bool = False):
    """Inner transcription logic (called under the transcribing guard)."""
    idx = session["current_index"]
    words = session["words"]
    if idx >= len(words):
        await sio.emit("session_stopped", {}, room=sid)
        return

    # Check minimum audio duration (0.5 seconds)
    audio_duration = len(audio) / config.audio_sample_rate
    if audio_duration < 0.5:
        logger.info(f"Audio too short ({audio_duration:.2f}s), skipping transcription")
        return

    current_word = words[idx]
    start_idx = session.get("streaming_start_idx", idx)
    if start_idx > idx:
        start_idx = idx

    previous_expected_chunk = [words[i]["emlaey_text"] for i in range(start_idx, idx)]

    # Build max expected chunk for parallel wav2vec when acoustic scoring is enabled
    remaining = len(words) - idx
    expected_chunk_max = (
        [words[idx + i]["emlaey_text"] for i in range(min(remaining, 20))]
        if config.enable_acoustic_score and remaining > 0
        else []
    )

    # Run transcription based on enabled scoring methods
    logger.info(f"Processing {audio_duration:.2f}s of audio for [{sid}] ({'final' if is_final else 'interim'})...")
    logger.info(f"  Expected word #{idx}: '%s'", display_arabic(current_word["emlaey_text"]))
    t0 = time.time()
    
    text = ""
    acoustic_scores_full: list[float] = []
    n_decoded_words = 0
    
    if config.enable_text_score and config.enable_acoustic_score and expected_chunk_max:
        whisper_task = asyncio.to_thread(transcriber.transcribe, audio)
        wav2vec_task = asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores, audio, previous_expected_chunk, expected_chunk_max
        )
        text, (acoustic_scores_full, n_decoded_words) = await asyncio.gather(whisper_task, wav2vec_task)
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Whisper + wav2vec (parallel) took %.2fs", time.time() - t0)
    elif config.enable_text_score:
        text = await asyncio.to_thread(transcriber.transcribe, audio)
        text = text.strip()
        logger.info("  Whisper transcription: '%s'", display_arabic(text))
        logger.info("  Transcription took %.2fs", time.time() - t0)
    elif config.enable_acoustic_score and expected_chunk_max:
        acoustic_scores_full, n_decoded_words = await asyncio.to_thread(
            acoustic_scorer.get_acoustic_scores, audio, previous_expected_chunk, expected_chunk_max
        )
        logger.info("  Wav2vec (acoustic only, %d decoded words) took %.2fs", n_decoded_words, time.time() - t0)

    # When text scoring is disabled, use expected words as transcribed words for acoustic scoring.
    # Limit to n_decoded_words (minus previous_words) so we only process words the user actually spoke.
    if not config.enable_text_score and config.enable_acoustic_score:
        n_new_decoded = max(0, n_decoded_words - len(previous_expected_chunk))
        transcribed_words = [words[idx + i]["emlaey_text"] for i in range(min(remaining, n_new_decoded))]
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
                scorer.score_word(
                    prev_slice[j]["emlaey_text"],
                    scorer.correct_word(
                        prev_slice[j]["emlaey_text"], candidate[j], config.max_edits_for_correction
                    ),
                )[score_key]
                >= config.score_threshold
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
    if config.enable_acoustic_score and acoustic_scores_full:
        # Acoustic scores align with EXPECTED chunk, not transcribed chunk.
        # Length of expected_chunk_max was min(remaining, 20).
        n_words_chunk = min(remaining, 20)
        acoustic_scores = acoustic_scores_full[:n_words_chunk]

    streaming = not is_final
    n_transcribed = len(transcribed_words)

    # Score each transcribed word against expected sequence
    corrected_parts: list[str] = []
    words_processed = 0
    for i, t_word in enumerate(transcribed_words):
        if idx >= len(words):
            break

        word = words[idx]
        t_corrected = scorer.correct_word(
            word["emlaey_text"], t_word, config.max_edits_for_correction
        )
        scores = scorer.score_word(word["emlaey_text"], t_corrected)
        # Diacritic score must use raw transcription: correct_word returns expected when
        # base letters match, which would otherwise give 100% diacritics for wrong tashkeel.
        ds = scorer.compute_diacritic_score(word["emlaey_text"], t_word)
        scores["diacritic_score"] = round(ds, 3)
        
        # Compute text_score from char_score and diacritic_score
        ts = scorer.compute_text_score(scores["char_score"], ds)
        scores["text_score"] = round(ts, 3)
        
        # In expected_chunk_max, `word` is at index 0 initially.
        # Since `idx` advances by 1 for each correct word, the offset into the original
        # `acoustic_scores` array (from the start of the current chunk) is `words_processed`.
        ac = (acoustic_scores[words_processed] if words_processed < len(acoustic_scores) else None) if config.enable_acoustic_score else None
        if ac is not None:
            scores["acoustic_score"] = round(ac, 3)
        scores["total_score"] = round(
            scorer.compute_total_score(scores["char_score"], ds, ac), 3
        )
        
        # Determine if word is correct based on scoring mode
        if config.pass_on_any_score:
            text_pass = config.enable_text_score and ts >= config.score_threshold
            acoustic_pass = ac is not None and ac >= config.score_threshold
            status = "correct" if (text_pass or acoustic_pass) else "incorrect"
        else:
            status = "correct" if scores["total_score"] >= config.score_threshold else "incorrect"

        # In streaming mode, mark the last word as interim (it may self-correct)
        word_is_interim = streaming and (i == n_transcribed - 1)

        corrected_parts.append(t_corrected)
        payload: Dict[str, Any] = {
            "chapter_number": word["surah"],
            "verse_number": word["ayah"],
            "word_number": word["word_index"],
            "status": status,
        }
        if streaming:
            payload["is_interim"] = word_is_interim
        if config.send_word_result_details:
            payload["transcribed"] = t_corrected
            payload["expected"] = word["emlaey_text"]
            payload.update(scores)

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

            # Persist confirmed word in background (non-blocking)
            queue = session.get("store_queue")
            if queue is not None:
                queue.put_nowait(("word", {
                    "chapter_number": word["surah"],
                    "verse_number": word["ayah"],
                    "word_number": word["word_index"],
                    "word_text": word["emlaey_text"],
                    "score": scores["total_score"],
                    "status": status,
                }))

            if status == "correct":
                idx += 1
                words_processed += 1
            else:
                break

    session["current_index"] = idx

    if idx >= len(words):
        # Cancel streaming task if running
        if session.get("streaming_task"):
            session["streaming_task"].cancel()
            session["streaming_task"] = None
        await sio.emit("session_stopped", {}, room=sid)
