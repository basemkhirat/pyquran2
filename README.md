# Quran2

Quran voice recognition app: React frontend with real-time recording and a FastAPI + Socket.IO backend for transcription and scoring.

## Prerequisites

- **Python** 3.10+ (for backend)
- **Node.js** 18+ and npm (for frontend)
- Optional: [Whisper](https://github.com/openai/whisper) model files (see [Backend](#backend) for paths)

## Quick start

### 1. Backend

From the project root:

```bash
# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the API (default: http://localhost:8000)
uvicorn backend.main:socket_app --reload --host 0.0.0.0 --port 8000
```

### 2. Frontend

In a separate terminal, from the project root:

```bash
cd frontend
npm install
npm run dev
```

Then open the URL shown (usually http://localhost:5173). The Vite dev server proxies `/api` and `/socket.io` to the backend at port 8000.

## Scripts

| Where    | Command              | Description                    |
|----------|----------------------|--------------------------------|
| Root     | `uvicorn backend.main:socket_app --reload --port 8000` | Run backend (after `pip install -r requirements.txt`) |
| frontend | `npm run dev`        | Start Vite dev server          |
| frontend | `npm run build`      | Production build               |
| frontend | `npm run preview`    | Preview production build       |
| frontend | `npm run lint`       | Run ESLint                     |

## Deploy to Modal.com (GPU)

From the project root (after `pip install modal` and `modal token new`):

```bash
modal serve modal_app.py   # dev: ephemeral URL, live reload
modal deploy modal_app.py  # prod: persistent URL
```

See `modal_app.py` for setup (create a Secret in the Modal dashboard for env vars). Use the printed URL as `VITE_SOCKET_URL` in your frontend.

## Environment

Create a `.env` in the project root if needed. Backend and frontend (Vite) both read from the project root `.env`.

### Frontend (socket client)

| Variable               | Default                         | Description |
|------------------------|---------------------------------|-------------|
| `VITE_SOCKET_URL`      | dev: `http://localhost:8000`, prod: same origin | Socket server URL |
| `VITE_SOCKET_API_KEY` | —                               | Must match backend `SOCKET_AUTH_API_KEY` when auth is enabled |

### Backend

| Variable               | Default                         | Description                    |
|------------------------|---------------------------------|--------------------------------|
| `HF_MODEL_PATH`        | `./guff/whisper-quran-v1`       | Hugging Face Whisper model path |
| `HAFS_JSON_PATH`       | `./assets/narrations/hafs.json` | Hafs narration data           |
| `WEIGHT_CHAR`          | `0.6`                           | Character score weight        |
| `WEIGHT_DIACRITIC`     | `0.4`                           | Diacritic score weight        |
| `SCORE_THRESHOLD`      | `0.5`                           | Minimum pass score            |
| `SILENCE_TIMEOUT_MS`   | `3000`                          | Silence timeout (ms)          |
| `AUDIO_SAMPLE_RATE`    | `16000`                         | Audio sample rate             |

## Running tests (backend)

From the project root (with the same venv activated):

```bash
pytest backend/tests -v
```

## Project layout

- **backend/** — FastAPI app, Socket.IO, Quran data, VAD, transcriber, scorer
- **frontend/** — React + TypeScript + Vite UI (Tailwind, shadcn-style components)
