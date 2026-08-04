"""Regressions for _streaming_transcription_loop's utterance-boundary handling.

Two bugs, both observed in recorded muaalem sessions:

- Audio spoken while the end-of-utterance decode was running got destroyed. `streaming_flush()`
  already resets the VAD; the loop reset it a *second* time after awaiting _process_speech, and
  everything `audio_chunk` had accumulated in between died with it. In session
  894b53d9 the reciter resumed 12ms after the flush, so 500ms of وَإِيَّاكَ never reached the
  model — it decoded 'اكَ' against 'وَءِييَااكَ' and the word failed at 48%.

- The loop could exit without clearing session["streaming_task"]. `audio_chunk` only spawns a
  new loop when that is None, so scoring stopped for the rest of the session.
"""
import asyncio
import sys
import types

import numpy as np

# backend.main imports backend.vad, which pulls in torch + silero_vad at module level.
# The real VADProcessor is never used here (FakeVAD stands in), so stub it before importing.
if "backend.vad" not in sys.modules:
    _vad_stub = types.ModuleType("backend.vad")

    class _StubVAD:
        def __init__(self, *args, **kwargs):
            pass

    _vad_stub.VADProcessor = _StubVAD
    sys.modules["backend.vad"] = _vad_stub

from backend import main  # noqa: E402
from backend.config import config  # noqa: E402

SAMPLES_PER_CHUNK = 1600  # 100ms at 16kHz


class FakeVAD:
    """Minimal stand-in: holds a buffer, reports speech end on demand, records resets."""

    def __init__(self, buffered_sec=3.0, speech_ended=True, flush_returns_audio=True):
        self.buffer = [np.zeros(int(buffered_sec * config.audio_sample_rate), dtype=np.float32)]
        self._speech_ended = speech_ended
        self._flush_returns_audio = flush_returns_audio
        self.resets = 0
        self.flushes = 0

    def detect_speech_end(self):
        return self._speech_ended

    def get_accumulated_audio(self):
        return np.concatenate(self.buffer) if self.buffer else None

    def streaming_flush(self):
        self.flushes += 1
        audio = self.get_accumulated_audio() if self._flush_returns_audio else None
        self.reset()
        return audio

    def reset(self):
        self.resets += 1
        self.buffer = []

    def accumulate_chunk(self, pcm16_bytes):
        """What audio_chunk does — including while the final decode is in flight."""
        self.buffer.append(
            np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )


def _session(vad, **overrides):
    session = {
        "vad": vad,
        "words": [{"surah": 1, "ayah": 5, "word_index": i + 1} for i in range(10)],
        "current_index": 2,
        "streaming_start_idx": 0,
        "total_samples": 16000,
        "streaming_task": "sentinel-task",
        "timeline_cursor_sec": 1.5,
        "last_interim_index": 2,
        "last_interim_acoustic": {"score": 0.4},
        "last_interim_n_decoded": 3,
        "phase": "reciting",
    }
    session.update(overrides)
    return session


def _run_loop(monkeypatch, session, process_speech, interval_ms=10):
    """Drive one pass of the loop with a fast tick, then return."""
    monkeypatch.setattr(config, "streaming_interval_ms", interval_ms)
    monkeypatch.setattr(main, "sessions", {"sid": session})
    monkeypatch.setattr(main, "_process_speech", process_speech)

    async def scenario():
        task = asyncio.create_task(main._streaming_transcription_loop("sid"))
        session["streaming_task"] = task
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


def test_audio_spoken_during_the_final_decode_survives_the_boundary(monkeypatch):
    """The وَإِيَّاكَ regression: chunks arriving mid-decode belong to the next utterance."""
    vad = FakeVAD()
    session = _session(vad)
    spoken_during_decode = 5  # 500ms — what was lost in session 894b53d9

    async def slow_decode(sid, audio, is_final=False, captured_total=0):
        # The reciter starts the next word while the previous utterance is still decoding.
        for _ in range(spoken_during_decode):
            await asyncio.sleep(0)
            vad.accumulate_chunk(b"\x01\x00" * SAMPLES_PER_CHUNK)

    _run_loop(monkeypatch, session, slow_decode)

    retained = vad.get_accumulated_audio()
    assert retained is not None, "the next utterance's opening audio was destroyed"
    assert len(retained) == spoken_during_decode * SAMPLES_PER_CHUNK
    # streaming_flush()'s own reset is the only one that may run.
    assert vad.resets == 1


def test_utterance_end_still_clears_per_utterance_state(monkeypatch):
    """Dropping the extra vad.reset() must not drop the rest of the teardown."""
    vad = FakeVAD()
    session = _session(vad)

    async def noop(sid, audio, is_final=False, captured_total=0):
        session["current_index"] = 4

    _run_loop(monkeypatch, session, noop)

    assert session["streaming_task"] is None
    assert session["timeline_cursor_sec"] is None
    assert session["last_interim_index"] is None
    assert session["last_interim_acoustic"] is None
    assert session["last_interim_n_decoded"] is None
    assert session["streaming_start_idx"] == 4


def test_short_final_utterance_still_clears_the_streaming_task(monkeypatch):
    """streaming_flush() returns None under 0.5s — the loop exits with nothing to score."""
    vad = FakeVAD(flush_returns_audio=False)
    session = _session(vad)

    async def never_called(sid, audio, is_final=False, captured_total=0):
        raise AssertionError("no audio to process")

    _run_loop(monkeypatch, session, never_called)

    assert session["streaming_task"] is None, "audio_chunk can never restart the loop"


def test_final_utterance_below_min_duration_still_clears_the_streaming_task(monkeypatch):
    """Flushed audio shorter than streaming_min_audio_sec takes the other early return."""
    vad = FakeVAD(buffered_sec=0.1)
    session = _session(vad)

    async def never_called(sid, audio, is_final=False, captured_total=0):
        raise AssertionError("audio is below the minimum duration")

    _run_loop(monkeypatch, session, never_called)

    assert session["streaming_task"] is None


def test_loop_exception_clears_the_streaming_task(monkeypatch):
    """An unexpected error must not leave the session unscorable for good."""
    vad = FakeVAD()
    session = _session(vad)

    async def boom(sid, audio, is_final=False, captured_total=0):
        raise RuntimeError("decoder blew up")

    _run_loop(monkeypatch, session, boom)

    assert session["streaming_task"] is None


def test_busy_transcription_does_not_flush_the_utterance(monkeypatch):
    """A tick that cannot process must not take the only copy of the buffered audio."""
    vad = FakeVAD()
    session = _session(vad, transcribing=True)

    async def never_called(sid, audio, is_final=False, captured_total=0):
        raise AssertionError("a decode is already in flight")

    monkeypatch.setattr(config, "streaming_interval_ms", 10)
    monkeypatch.setattr(main, "sessions", {"sid": session})
    monkeypatch.setattr(main, "_process_speech", never_called)

    async def scenario():
        task = asyncio.create_task(main._streaming_transcription_loop("sid"))
        session["streaming_task"] = task
        await asyncio.sleep(0.1)  # several ticks, all of them busy
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert vad.flushes == 0, "a busy tick discarded the utterance"
    assert vad.get_accumulated_audio() is not None


def test_cancellation_leaves_teardown_to_the_canceller(monkeypatch):
    """stop_session cancels the loop, then reads streaming_start_idx for its own flush."""
    vad = FakeVAD(speech_ended=False)
    session = _session(vad)

    async def slow_interim(sid, audio, is_final=False, captured_total=0):
        await asyncio.sleep(10)  # still decoding when stop_session cancels us

    monkeypatch.setattr(config, "streaming_interval_ms", 10)
    monkeypatch.setattr(main, "sessions", {"sid": session})
    monkeypatch.setattr(main, "_process_speech", slow_interim)

    async def scenario():
        task = asyncio.create_task(main._streaming_transcription_loop("sid"))
        session["streaming_task"] = task
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert session["streaming_start_idx"] == 0, "cancelled loop clobbered the canceller's state"
