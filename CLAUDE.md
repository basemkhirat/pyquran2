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
```

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
5. `acoustic_scorer.py` (wav2vec2 + KenLM beam search) produces an acoustic score independently
6. `scorer.py` computes character accuracy + diacritic accuracy, then blends with acoustic score
7. `word_result` event is emitted with pass/fail status and scores

**Key modules**:
- `main.py` — Socket.IO event handlers (`start_session`, `audio_chunk`, `skip_word`, `stop_session`) + REST endpoints
- `config.py` — All configuration via env vars with defaults; used everywhere as `from backend.config import config`
- `quran_data.py` — Loads `assets/narrations/hafs.json`; provides chapter/verse/word lookups
- `session_store.py` — Background async writer; persists word results to `data/sessions/{uuid}/data.json` and audio to `recording.wav`

### Frontend (`frontend/src/`)

React 19 + TypeScript + Zustand + Socket.IO client. Single page app.

- `App.tsx` — Mounts Socket.IO listeners and routes between Setup/Recording/Summary views
- `stores/session.ts` — Zustand store; holds session state, word list, scores
- `hooks/useAudioRecorder.ts` — Captures microphone, encodes to PCM16, sends chunks via socket
- `lib/socket.ts` — Socket.IO client singleton (connects to `VITE_BACKEND_URL`)
- `components/SessionSetup.tsx` — Chapter/verse range picker, start button
- `components/VerseDisplay.tsx` — Live word display with color-coded pass/fail
- `components/SessionSummary.tsx` — Post-session results

### Data

No SQL database. All persistence is file-based:
- **Quran text**: `assets/narrations/hafs.json` — reference text with emlaei/uthmani variants
- **Language model**: `assets/quran_lm.arpa` — KenLM ARPA file for wav2vec2 beam search
- **Whisper model**: `models/whisper-quran-v1/` — Hugging Face checkpoint (local)
- **Sessions**: `data/sessions/{uuid}/` — `data.json` (word results) + `recording.wav`

### Socket.IO Protocol

Client → Server: `start_session`, `audio_chunk` (binary PCM16), `skip_word`, `stop_session`

Server → Client: `session_started`, `word_result`, `session_stopped`, `session_error`, `timeout`, `verse_detected`, `verse_detection_failed`

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
| `WAV2VEC2_LM_PATH` | Path to KenLM ARPA file |
| `SCORE_THRESHOLD` | Pass/fail cutoff (default: `0.5`) |
| `SOCKET_AUTH_API_KEY` | Optional socket auth key |
| `SAVE_SESSION_DATA` | Persist session JSON/WAV to disk |

Frontend: `frontend/.env` with `VITE_BACKEND_URL` and `VITE_SOCKET_API_KEY`.

## Scoring Logic

Total score = weighted blend of:
- **Text score**: character accuracy (`WEIGHT_CHAR`) + diacritic accuracy (`WEIGHT_DIACRITIC`), computed after Arabic normalization
- **Acoustic score**: wav2vec2 CTC beam search with KenLM, weighted by `WEIGHT_ACOUSTIC`

Blend ratio controlled by `WEIGHT_TEXT` vs `WEIGHT_ACOUSTIC`. Either scorer can be disabled independently.
