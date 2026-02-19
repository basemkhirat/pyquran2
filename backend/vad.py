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


class VADProcessor:
    """Processes streamed PCM16 audio chunks and detects speech boundaries."""

    def __init__(self, on_speech_end: Optional[Callable[[np.ndarray], None]] = None):
        self.model = load_silero_vad()
        self.sample_rate = config.audio_sample_rate
        self.silence_timeout = config.silence_timeout_ms / 1000.0

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

    def reset(self):
        self.audio_buffer = []
        self._pre_buffer.clear()
        self._pre_buffer_samples = 0
        self.is_speaking = False
        self.last_speech_time = 0.0
        self.speech_frame_count = 0
        self._remainder = np.array([], dtype=np.float32)
        self._processing = False
        self.model.reset_states()

    def _get_buffer_duration(self) -> float:
        """Get total buffered audio duration in seconds."""
        total_samples = sum(len(chunk) for chunk in self.audio_buffer)
        return total_samples / self.sample_rate

    def process_chunk(self, pcm16_bytes: bytes) -> Optional[np.ndarray]:
        """Process an incoming PCM16 audio chunk.

        Returns complete speech segment when silence is detected after meaningful speech.
        """
        if self._processing:
            return None

        audio_int16 = np.frombuffer(pcm16_bytes, dtype=np.int16)
        audio_float = audio_int16.astype(np.float32) / 32768.0

        # Buffer strategy: only accumulate in audio_buffer during speech.
        # Before speech, keep a small rolling pre-buffer for onset capture.
        if self.is_speaking:
            self.audio_buffer.append(audio_float)
        else:
            self._pre_buffer.append(audio_float)
            self._pre_buffer_samples += len(audio_float)
            # Trim pre-buffer to max size
            while self._pre_buffer_samples > self._max_pre_samples and self._pre_buffer:
                removed = self._pre_buffer.popleft()
                self._pre_buffer_samples -= len(removed)

        # Prepend remainder from previous call
        samples = np.concatenate([self._remainder, audio_float])

        # Process in 512-sample windows
        speech_detected_in_chunk = False
        offset = 0
        while offset + VAD_CHUNK_SAMPLES <= len(samples):
            window = samples[offset : offset + VAD_CHUNK_SAMPLES]
            tensor = torch.from_numpy(window)
            prob = self.model(tensor, self.sample_rate).item()
            if prob > 0.5:
                speech_detected_in_chunk = True
                self.speech_frame_count += 1
            offset += VAD_CHUNK_SAMPLES

        self._remainder = samples[offset:]
        current_time = time.time()

        if speech_detected_in_chunk:
            if not self.is_speaking:
                logger.debug("Speech started")
                # Promote pre-buffer into audio_buffer to capture onset
                self.audio_buffer = list(self._pre_buffer)
                self.audio_buffer.append(audio_float)
                self._pre_buffer.clear()
                self._pre_buffer_samples = 0
            self.is_speaking = True
            self.last_speech_time = current_time
            return None

        # Check for silence after meaningful speech
        if (
            self.is_speaking
            and self.speech_frame_count >= MIN_SPEECH_FRAMES
            and (current_time - self.last_speech_time) > self.silence_timeout
        ):
            silence_dur = current_time - self.last_speech_time
            logger.info("Speech ended after %.0fms silence (frames=%d)",
                        silence_dur * 1000, self.speech_frame_count)
            return self._extract_segment()

        # If speaking but not enough speech frames yet and silence timeout passed,
        # discard the buffer (was just noise)
        if (
            self.is_speaking
            and self.speech_frame_count < MIN_SPEECH_FRAMES
            and (current_time - self.last_speech_time) > self.silence_timeout
        ):
            self.audio_buffer = []
            self.is_speaking = False
            self.speech_frame_count = 0
            self._remainder = np.array([], dtype=np.float32)

        return None

    def flush(self) -> Optional[np.ndarray]:
        """Force-flush any buffered audio if it contains meaningful speech."""
        if self.audio_buffer and self.speech_frame_count >= MIN_SPEECH_FRAMES:
            return self._extract_segment()
        # Discard if not enough speech
        self.audio_buffer = []
        self._pre_buffer.clear()
        self._pre_buffer_samples = 0
        self.is_speaking = False
        self.speech_frame_count = 0
        self._remainder = np.array([], dtype=np.float32)
        return None

    def _extract_segment(self) -> Optional[np.ndarray]:
        """Extract buffered audio as a single segment."""
        if not self.audio_buffer:
            return None

        self._processing = True
        segment = np.concatenate(self.audio_buffer)
        self.audio_buffer = []
        self._pre_buffer.clear()
        self._pre_buffer_samples = 0
        self.is_speaking = False
        self.speech_frame_count = 0
        self._remainder = np.array([], dtype=np.float32)
        self._processing = False

        # Check minimum duration
        duration = len(segment) / self.sample_rate
        if duration < MIN_AUDIO_DURATION:
            logger.debug("Segment too short (%.2fs), discarding", duration)
            return None

        logger.info("Extracted audio segment: %.2fs", duration)
        return segment

    def check_silence_timeout(self) -> bool:
        """Check if we've exceeded silence timeout since last speech."""
        if self.is_speaking and self.last_speech_time > 0:
            return (time.time() - self.last_speech_time) > self.silence_timeout
        return False
