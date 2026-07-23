# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quran Voice Recognition Web Application — real-time Arabic recitation scoring with word-by-word pass/fail feedback. Audio flows from the browser via Socket.IO, processed by VAD + Whisper + wav2vec2, and scored against Quran reference text.

## Commands

### Backend

```bash
make setup-backend        # Create .venv and install requirements.txt
make backend              # Run FastAPI server on port 8000
# Or manually:
source .venv/bin/activate
uvicorn backend.main:socket_app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
yarn install
yarn dev                  # Dev server at http://localhost:5173 (proxies /api and /socket.io to :8000)
yarn build                # Production build
yarn lint                 # ESLint
```

### Tests

```bash
pytest backend/tests -v             # All backend tests
pytest backend/tests/test_scorer.py # Single test file

cd frontend && yarn check:playback  # Regression check for the playback audio hook
```

There is no frontend test runner (yarn 1 cannot link vitest against vite here).
`yarn check:playback` runs `frontend/scripts/check-audio-playback.mts` on bare node + jsdom
to guard one specific trap: the `<audio>` element mounts later than `useAudioPlayback`,
because the page renders a spinner until the session loads. With a plain `RefObject` the
listener effect runs once against a null ref and never re-runs, so audio plays while the
words and seek bar sit frozen — hence the callback ref.

### Guide (VitePress docs)

```bash
cd guide
yarn dev                  # Preview docs locally
yarn build                # Build static site
```

### Deployment

```bash
make modal-serve          # Deploy to Modal.com (ephemeral dev URL)
make modal-deploy         # Deploy to Modal.com production (persistent URL)
```

## Architecture

### Backend (`backend/`)

FastAPI + python-socketio ASGI app. The Socket.IO server wraps FastAPI and is exposed as `socket_app` in `main.py`. Both HTTP REST endpoints and WebSocket events are handled in the same process.

**Audio Processing Pipeline** (per session, per word):
1. Browser sends PCM16 mono 16kHz chunks via `audio_chunk` socket event
2. `vad.py` (Silero VAD) accumulates audio and detects speech boundaries
3. Optional verse detection (`verse_detection.py`) identifies which verse the user is reciting from the first utterance
4. `transcriber.py` (Whisper `whisper-quran-v1`) transcribes accumulated speech → Arabic text
5. `acoustic_scorer.py` (wav2vec2 greedy CTC decoding) decodes the audio and scores the decoded text as `WEIGHT_CHAR` * char accuracy + `WEIGHT_DIACRITIC` * diacritic accuracy (reusing `scorer.py`)
6. `scorer.py` computes character accuracy + diacritic accuracy for the Whisper text, then blends text and acoustic scores
7. `word_result` event is emitted with pass/fail status and scores

**Key modules**:
- `main.py` — Socket.IO event handlers (`start_session`, `audio_chunk`, `skip_word`, `stop_session`) + REST endpoints
- `config.py` — All configuration via env vars with defaults; used everywhere as `from backend.config import config`
- `quran_data.py` — Loads `assets/narrations/hafs.json`; provides chapter/verse/word lookups
- `session_reader.py` — Reads recorded sessions back for playback (`GET /api/sessions/{id}`). Merges the stored timeline with `quran_data.get_words_range` so each attempt carries a `display_index` into the word list — resolving the `surah/ayah/word_index` vs `chapter_number/verse_number/word_number` naming split server-side. Also infers the verse range for sessions recorded before those fields existed, and computes WAV duration from the file rather than trusting a possibly-unfinalized RIFF header
- `session_store.py` — Persists per-session info to `data/sessions/{uuid}/info.json` (session metadata — id, type/mode, narration_id, score_threshold, the recorded `duration` in ms, and the recited range as `start_chapter_number`/`start_verse_number`/`end_chapter_number`/`end_verse_number` — plus a `words` array, each word with the reference text (`expected_text`), what the recognizer heard (`detected_text`), and `start_time`/`end_time` in ms relative to `recording.wav`) plus the full-session audio `recording.wav`; driven by a background writer task in `main.py`

### Frontend (`frontend/src/`)

React 19 + TypeScript + Zustand + Socket.IO client, with `react-router-dom` routing two pages: `/` (live recitation) and `/sessions/:sessionId` (playback of a recorded session). Routes live in `main.tsx` inside `AuthGate`, so both inherit the password gate. `App` is mounted only on `/` — it renders `SessionSetup`, which rewrites the URL via `history.replaceState` and would clobber the playback route.

- `App.tsx` — Mounts Socket.IO listeners; the live recitation view
- `stores/session.ts` — Zustand store; holds session state, word list, scores
- `hooks/useAudioRecorder.ts` — Captures microphone, encodes to PCM16, sends chunks via socket
- `hooks/useAudioPlayback.ts` — Drives the playback `<audio>` element; publishes position from a single rAF loop to subscribers rather than React state, so the verse list re-renders per word instead of per frame
- `lib/socket.ts` — Socket.IO client singleton (connects to `VITE_BACKEND_URL`)
- `lib/playbackTimeline.ts` — Builds a binary-searchable index over a recorded timeline and answers "how should this word look at time T". The verdict flips at an attempt's **end**, not its start, so a word retried in `word_by_word` mode renders gold → red → gold → green as playback crosses each attempt
- `components/SessionSetup.tsx` — Chapter/verse range picker, start button
- `components/WordChip.tsx` — Single colour-coded word, shared by the live view and playback
- `components/VerseDisplay.tsx` — Live word display with color-coded pass/fail
- `components/playback/` — Playback header/verses/audio bar/progress/transport
- `pages/SessionPlaybackPage.tsx` — Fetches a session and composes the playback UI
- `components/SessionSummary.tsx` — Post-session results (currently unreferenced)

### Data

No SQL database. All persistence is file-based:
- **Quran text**: `assets/narrations/hafs.json` — reference text with emlaei/uthmani variants
- **Whisper model**: `models/whisper-quran-v1/` — Hugging Face checkpoint (local)
- **Sessions**: `data/sessions/{uuid}/` — `info.json` (session metadata including the recording `duration`, plus a `words` array; each confirmed spoken word with its reference text, the recognizer's `detected_text`, and WAV-relative start/end times — all times in ms) + `recording.wav` (one WAV per session). Location is `SESSIONS_DIR`; on Modal it is a mounted Volume, since the container filesystem is ephemeral. Note the `words` array is **sparse** (skipped words are never written) and **not unique** (a word retried in `word_by_word` mode is recorded once per attempt)

### REST Endpoints

`GET /api/chapters`, `GET /api/words`, `GET /api/verse-count`, `GET /api/auth-config`, `POST /api/login`, plus session playback:

- `GET /api/sessions/{id}` — merged playback payload: metadata, the verse range as display words, and the timeline with each attempt's `display_index`, status, score and ms offsets
- `GET /api/sessions/{id}/recording` (also `…/recording.wav` — same file; the `.wav` form is what `session_ended` advertises) — the session WAV via `FileResponse`, which supports Range/206 so `<audio>` can seek

Both return 404 for unknown, malformed or unreadable ids alike, so the id space cannot be probed. Like every other REST endpoint, they are unauthenticated.

### Socket.IO Protocol

Client → Server: `start_session`, `audio_chunk` (binary PCM16), `skip_word`, `stop_session`

`start_session` accepts three optional per-session fields: `score_threshold` (0-1 pass/fail cutoff), `mode`, and `record`. `mode` is `word_by_word` (default — stays on a word until it passes) or `continuous` (always scores and advances so a wrong word never blocks). The advance decision lives in `scorer.should_advance`. `record` (boolean) decides whether the session is persisted to `data/sessions/{uuid}/`; when omitted it falls back to the `SAVE_SESSION_DATA` config, which defaults to `false` — so sessions are not recorded unless asked for. The mobile clients and web frontend set these fields.

`session_started` echoes back `{id, record}` so clients can confirm the resolved recording decision. The session `id` is always generated, even when nothing is persisted.

Server → Client: `session_started`, `word_result`, `session_stopped`, `session_ended`, `session_error`, `timeout`, `verse_detected`, `verse_detection_failed`

Ending a session goes through `_end_session` in `main.py`, which is idempotent — `session_stopped` is emitted **exactly once** per session whichever path ends it (words exhausted, last word skipped, explicit `stop_session`). It goes out *before* the recording is flushed, so the client's UI signal is never delayed by disk I/O.

`session_ended` follows only for a recorded session (`record=true`), and only after the store is closed and awaited. The WAV's RIFF length fields are only finalized by `SessionStore.close_audio()`, so advertising the URL any earlier hands the client a file whose duration reads as `Infinity` and whose seeking is broken. Payload: the raw `info.json` contents flattened onto the event (`id`, `type`, `narration_id`, `score_threshold`, `duration`, the verse range, and `words`) plus `url` — the absolute recording URL (`PUBLIC_BASE_URL` when set, otherwise derived from the client's handshake headers, preferring `X-Forwarded-*`). `duration` is written by `SessionStore.close_audio()`, which re-flushes `info.json` so the value covers audio recorded after the last word.

Authentication: optional `SOCKET_AUTH_API_KEY` env var; frontend sends it as `auth.api_key` on handshake.

## Environment Variables

Copy `.env.example` to `.env` in the project root. Key vars:

| Variable | Purpose |
|----------|---------|
| `HF_MODEL_PATH` | Path to Whisper model (default: `./models/whisper-quran-v1`) |
| `HAFS_JSON_PATH` | Path to Quran JSON (default: `./assets/narrations/hafs.json`) |
| `ENABLE_TEXT_SCORE` | Enable Whisper transcription scoring |
| `ENABLE_ACOUSTIC_SCORE` | Enable wav2vec2 acoustic scoring |
| `WAV2VEC2_QURAN_ASR_MODEL` | HuggingFace model ID for wav2vec2 |
| `SCORE_THRESHOLD` | Pass/fail cutoff (default: `0.5`) |
| `SCORE_FATHA` / `SCORE_DAMMA` / `SCORE_KASRA` / `SCORE_SHADDA` / `SCORE_SUKOON` | Per-diacritic scoring toggles (all default `true`); a disabled mark is ignored everywhere. Defined by `SCORABLE_DIACRITICS` in `config.py` |
| `SOCKET_AUTH_API_KEY` | Optional socket auth key |
| `APP_PASSWORD` | Optional password gate for the frontend, validated server-side via `POST /api/login` (empty = disabled) |
| `SAVE_SESSION_DATA` | Fallback for `start_session`'s `record` field when the client omits it; persists session JSON/WAV to disk (default: `false`) |
| `SESSIONS_DIR` | Where recorded sessions are stored (default: `./data/sessions`; `/data/sessions` on Modal) |
| `PUBLIC_BASE_URL` | Public origin for absolute URLs in socket payloads (e.g. `session_ended`'s `url`). Empty = derive it from the client's handshake headers |

Frontend: `frontend/.env` with `VITE_BACKEND_URL` and `VITE_SOCKET_API_KEY`.

## Scoring Logic

Total score = weighted blend of:
- **Text score**: character accuracy (`WEIGHT_CHAR`) + diacritic accuracy (`WEIGHT_DIACRITIC`), computed after Arabic normalization on the Whisper transcription
- **Acoustic score**: wav2vec2 greedy CTC decodes the audio, then the decoded text is scored the same way — character accuracy (`WEIGHT_CHAR`) + diacritic accuracy (`WEIGHT_DIACRITIC`) — reusing the text scorer. The blended result is weighted by `WEIGHT_ACOUSTIC`. `WEIGHT_CHAR` + `WEIGHT_DIACRITIC` should sum to 1.0 to keep each sub-score in [0, 1].

Blend ratio controlled by `WEIGHT_TEXT` vs `WEIGHT_ACOUSTIC`. Either scorer can be disabled independently.
