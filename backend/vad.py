import logging
from collections import deque

import numpy as np
import torch
import time
from typing import Optional, Callable
from silero_vad import load_silero_vad
from backend.config import config

logger = logging.getLogger(__name__)

# Silero VAD requires exactly 512 samples per call at 16kHz
VAD_CHUNK_SAMPLES = 512

# Minimum speech frames before we consider it real speech (not just noise)
# At 16kHz with 512-sample windows, each window = 32ms
# 10 frames = ~320ms of speech minimum
MIN_SPEECH_FRAMES = 6

# Minimum audio duration in seconds to send to Whisper
MIN_AUDIO_DURATION = 0.5

# How much pre-speech audio to keep (captures natural word onset)
PRE_SPEECH_BUFFER_SEC = 0.3

# Number of consecutive silent frames needed to confirm speech ended (streaming mode).
# At 32ms per frame, 15 frames ≈ 480ms of solid silence.
STREAMING_SILENCE_FRAMES = 15


class VADProcessor:
    """Processes streamed PCM16 audio chunks and detects speech boundaries.

    Continuously accumulates audio, exposes it for periodic transcription,
    and signals speech end via VAD frame counting.
    """

    def __init__(self, on_speech_end: Optional[Callable[[np.ndarray], None]] = None):
        self.model = load_silero_vad()
        self.sample_rate = config.audio_sample_rate

        self.audio_buffer: list[np.ndarray] = []  # only filled during speech
        self._pre_buffer: deque[np.ndarray] = deque()  # rolling buffer for speech onset
        self._pre_buffer_samples = 0
        self._max_pre_samples = int(self.sample_rate * PRE_SPEECH_BUFFER_SEC)
        self.is_speaking = False
        self.last_speech_time = 0.0
        self.speech_frame_count = 0
        self.on_speech_end = on_speech_end
        self._remainder = np.array([], dtype=np.float32)
        self._processing = False  # guard against concurrent flush

        # Streaming-specific state
        self._speech_started = False  # True once first speech detected
        self._silent_frame_streak = 0  # consecutive non-speech VAD frames

    def reset(self):
        self.audio_buffer = []
        self._pre_buffer.clear()
        self._pre_buffer_samples = 0
        self.is_speaking = False
        self.last_speech_time = 0.0
        self.speech_frame_count = 0
        self._remainder = np.array([], dtype=np.float32)
        self._processing = False
        self._speech_started = False
        self._silent_frame_streak = 0
        self.model.reset_states()

    def _get_buffer_duration(self) -> float:
        """Get total buffered audio duration in seconds."""
        total_samples = sum(len(chunk) for chunk in self.audio_buffer)
        return total_samples / self.sample_rate


    def accumulate_chunk(self, pcm16_bytes: bytes) -> None:
        """Accumulate audio for streaming transcription.

        Converts PCM16 → float32 and appends to the growing audio buffer.
        Runs VAD to track speech start/end but never triggers transcription
        on its own — that is the caller's responsibility via the periodic loop.
        """
        audio_int16 = np.frombuffer(pcm16_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0

        # Always accumulate once speech has started (or into pre-buffer before)
        if self._speech_started:
            self.audio_buffer.append(audio_float)
        else:
            self._pre_buffer.append(audio_float)
            self._pre_buffer_samples += len(audio_float)
            while self._pre_buffer_samples > self._max_pre_samples and self._pre_buffer:
                removed = self._pre_buffer.popleft()
                self._pre_buffer_samples -= len(removed)

        # Run VAD on 512-sample windows
        samples = np.concatenate([self._remainder, audio_float])
        speech_detected_in_chunk = False
        offset = 0
        while offset + VAD_CHUNK_SAMPLES <= len(samples):
            window = samples[offset : offset + VAD_CHUNK_SAMPLES]
            tensor = torch.from_numpy(window)
            prob = self.model(tensor, self.sample_rate).item()
            if prob > 0.5:
                speech_detected_in_chunk = True
                self.speech_frame_count += 1
                self._silent_frame_streak = 0
            else:
                self._silent_frame_streak += 1
            offset += VAD_CHUNK_SAMPLES
        self._remainder = samples[offset:]

        if speech_detected_in_chunk:
            self.last_speech_time = time.time()
            if not self._speech_started:
                logger.debug("Streaming: speech started")
                self._speech_started = True
                self.is_speaking = True
                # Promote pre-buffer (already contains current chunk)
                self.audio_buffer = list(self._pre_buffer)
                self._pre_buffer.clear()
                self._pre_buffer_samples = 0
            self.is_speaking = True

    @property
    def speech_started(self) -> bool:
        """Whether any real speech has been detected since last reset."""
        return self._speech_started and self.speech_frame_count >= MIN_SPEECH_FRAMES

    def get_accumulated_audio(self) -> Optional[np.ndarray]:
        """Return a copy of all accumulated audio for transcription."""
        if not self.audio_buffer:
            return None
        return np.concatenate(self.audio_buffer)

    def get_accumulated_duration(self) -> float:
        """Duration of accumulated audio in seconds."""
        if not self.audio_buffer:
            return 0.0
        total = sum(len(c) for c in self.audio_buffer)
        return total / self.sample_rate

    def detect_speech_end(self) -> bool:
        """Detect if speech has ended: enough silence after meaningful speech.

        Uses a frame-count approach (consecutive silent VAD frames) rather
        than wall-clock time so detection doesn't depend on chunk arrival rate.
        """
        if not self._speech_started:
            return False
        if self.speech_frame_count < MIN_SPEECH_FRAMES:
            return False
        return self._silent_frame_streak >= STREAMING_SILENCE_FRAMES

    def streaming_flush(self) -> Optional[np.ndarray]:
        """Final flush for streaming mode — extract and reset."""
        audio = self.get_accumulated_audio()
        self.reset()
        if audio is not None:
            duration = len(audio) / self.sample_rate
            if duration < MIN_AUDIO_DURATION:
                return None
            logger.info("Streaming flush: %.2fs", duration)
        return audio
