import asyncio
import os
import time
import wave
import logging
from typing import Dict, Any

import numpy as np
import socketio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.config import config
from backend import quran_data, transcriber, scorer
from backend.vad import VADProcessor
if config.enable_acoustic_score:
    from backend import acoustic_scorer

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


# ===================== REST Endpoints =====================

@app.get("/api/chapters")
def api_chapters():
    return quran_data.get_chapters()


@app.get("/api/words")
def api_words(
    surah: int = Query(...),
    start_ayah: int = Query(...),
    end_ayah: int = Query(...),
):
    return quran_data.get_words(surah, start_ayah, end_ayah)


@app.get("/api/verse-count")
def api_verse_count(surah: int = Query(...)):
    return {"count": quran_data.get_chapter_verse_count(surah)}


# ===================== Socket.IO Events =====================

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")
    sessions[sid] = {
        "words": [],
        "current_index": 0,
        "vad": VADProcessor(),
        "timeout_task": None,
        "transcribing": False,
    }


@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    session = sessions.pop(sid, None)
    if session and session.get("timeout_task"):
        session["timeout_task"].cancel()


@sio.event
async def start_session(sid, data):
    """Initialize a recitation session with selected verse range."""
    surah = data["chapter_number"]
    start_ayah = data["start_verse_number"]
    end_ayah = data["end_verse_number"]

    words = quran_data.get_words(surah, start_ayah, end_ayah)
    session = sessions.get(sid)
    if not session:
        return

    session["words"] = words
    session["current_index"] = 0
    session["vad"].reset()

    # Cancel any existing timeout task
    if session.get("timeout_task"):
        session["timeout_task"].cancel()

    logger.info(f"Session started for {sid}: Surah {surah}, Ayah {start_ayah}-{end_ayah}, {len(words)} words")
    await sio.emit("session_started", {}, room=sid)

    # Start silence timeout watcher
    session["timeout_task"] = asyncio.create_task(_silence_watcher(sid))


@sio.event
async def audio_chunk(sid, data):
    """Process incoming PCM16 audio chunk."""
    session = sessions.get(sid)
    if not session or not session["words"]:
        return

    idx = session["current_index"]
    if idx >= len(session["words"]):
        await sio.emit("session_stopped", {}, room=sid)
        return

    vad = session["vad"]
    speech_segment = vad.process_chunk(data)

    if speech_segment is not None:
        await _process_speech(sid, speech_segment)


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
        payload["total_score"] = 0.0
    await sio.emit("word_result", payload, room=sid)

    session["current_index"] += 1
    session["vad"].reset()

    if session["current_index"] >= len(session["words"]):
        await sio.emit("session_stopped", {}, room=sid)


@sio.event
async def stop_session(sid, _data=None):
    """Stop session and flush any remaining audio."""
    session = sessions.get(sid)
    if not session:
        return

    segment = session["vad"].flush()
    if segment is not None and len(segment) > config.audio_sample_rate * 0.3:
        await _process_speech(sid, segment)


# ===================== Internal Helpers =====================

async def _process_speech(sid: str, audio: np.ndarray):
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
        await _do_process_speech(sid, session, audio)
    finally:
        session["transcribing"] = False


async def _do_process_speech(sid: str, session: dict, audio: np.ndarray):
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

    # Reset VAD immediately so new audio starts fresh
    session["vad"].reset()

    # Save audio chunk to disk for testing/debugging
    chunks_dir = os.path.join(os.path.dirname(__file__), "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    wav_path = os.path.join(chunks_dir, f"{ts}_{sid[:8]}_w{idx}.wav")
    pcm16 = (audio * 32768).astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.audio_sample_rate)
        wf.writeframes(pcm16.tobytes())
    logger.info(f"Saved audio chunk: {wav_path} ({audio_duration:.2f}s)")

    # Build context prompt: previous words for continuity + optional next words (capped)
    current_word = words[idx]
    prev_words = [w["emlaey_text"] for w in words[max(0, idx - 3):idx]]
    remaining_words = [
        w["emlaey_text"] for w in words[idx:]
        if w["ayah"] == current_word["ayah"]
    ][: config.max_prompt_next_words]
    initial_prompt = " ".join(prev_words + remaining_words)

    # Transcribe
    logger.info(f"Transcribing {audio_duration:.2f}s of audio for [{sid}]...")
    logger.info(f"  Initial prompt: '{initial_prompt}'")
    logger.info(f"  Expected word #{idx}: '{current_word['emlaey_text']}'")
    t0 = time.time()
    text = await asyncio.to_thread(transcriber.transcribe, audio, initial_prompt)
    text = text.strip()
    t_transcribe = time.time() - t0
    logger.info(f"  Result in {t_transcribe:.2f}s: '{text}'")

    if not text:
        return

    transcribed_words = text.split()

    # --- Backtrack detection: skip repeated already-correct words ---
    lookback = min(idx, len(transcribed_words))  # how far back we can check
    skip_count = 0

    if lookback > 0:
        # Try matching the longest possible prefix of transcribed_words
        # against the already-correct words ending at idx
        for prefix_len in range(lookback, 0, -1):
            prev_slice = words[idx - prefix_len : idx]
            candidate = transcribed_words[:prefix_len]

            all_match = all(
                scorer.score_word(
                    prev_slice[j]["emlaey_text"],
                    scorer.correct_word(
                        prev_slice[j]["emlaey_text"], candidate[j], config.max_edits_for_correction
                    ),
                )["total_score"]
                >= config.score_threshold
                for j in range(prefix_len)
            )
            if all_match:
                skip_count = prefix_len
                break

    if skip_count > 0:
        logger.info(f"  Backtrack detected: skipping {skip_count} repeated word(s)")
        transcribed_words = transcribed_words[skip_count:]

    # Acoustic scores (one per expected word for this chunk) when enabled
    acoustic_scores: list[float] = []
    if config.enable_acoustic_score:
        n_words_chunk = min(len(transcribed_words), len(words) - idx)
        expected_chunk = [words[idx + i]["emlaey_text"] for i in range(n_words_chunk)]
        if expected_chunk:
            acoustic_scores = await asyncio.to_thread(
                acoustic_scorer.get_acoustic_scores, audio, expected_chunk
            )

    # Score each transcribed word against expected sequence; collect corrected text for subtitle
    corrected_parts: list[str] = []
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
        ac = (acoustic_scores[i] if i < len(acoustic_scores) else None) if config.enable_acoustic_score else None
        if ac is not None:
            scores["acoustic_score"] = round(ac, 3)
        scores["total_score"] = round(
            scorer.compute_total_score(scores["char_score"], ds, ac), 3
        )
        status = "correct" if scores["total_score"] >= config.score_threshold else "incorrect"

        corrected_parts.append(t_corrected)
        payload: Dict[str, Any] = {
            "chapter_number": word["surah"],
            "verse_number": word["ayah"],
            "word_number": word["word_index"],
            "status": status,
        }
        if config.send_word_result_details:
            payload["transcribed"] = t_corrected
            payload["expected"] = word["emlaey_text"]
            payload.update(scores)
        await sio.emit("word_result", payload, room=sid)

        # Only advance to next word if correct; incorrect words must be retried
        if status == "correct":
            idx += 1
        else:
            break  # Stop processing further transcribed words on incorrect

    # Emit corrected transcription for live subtitle (so frontend shows post-correction text)
    if corrected_parts:
        await sio.emit("transcription", {"text": " ".join(corrected_parts)}, room=sid)

    session["current_index"] = idx

    if idx >= len(words):
        await sio.emit("session_stopped", {}, room=sid)


async def _silence_watcher(sid: str):
    """Background task to detect prolonged silence and emit timeout."""
    try:
        while True:
            await asyncio.sleep(1.0)
            session = sessions.get(sid)
            if not session or session["current_index"] >= len(session["words"]):
                return

            vad = session["vad"]
            if vad.last_speech_time > 0 and not vad.is_speaking:
                elapsed = time.time() - vad.last_speech_time
                if elapsed > (config.silence_timeout_ms / 1000.0):
                    # Flush any remaining audio
                    segment = vad.flush()
                    if segment is not None and len(segment) > config.audio_sample_rate * 0.3:
                        await _process_speech(sid, segment)
                    else:
                        await sio.emit("timeout", {
                            "word_index": session["current_index"],
                        }, room=sid)
    except asyncio.CancelledError:
        pass
