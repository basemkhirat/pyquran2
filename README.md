# PyQuran2

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

## Environment (backend)

Optional; defaults work for local development. Create a `.env` in the project root if needed:

| Variable               | Default                         | Description                    |
|------------------------|---------------------------------|--------------------------------|
| `TRANSCRIPTION_BACKEND`| `whisper_cpp`                   | `whisper_cpp`, `mlx`, or `hf`  |
| `WHISPER_MODEL_PATH`   | `./whisper_cpp/epoch-best/ggml-model.bin` | Whisper-CPP model path |
| `MLX_MODEL_PATH`       | `./mlx_models/epoch-best`       | MLX Whisper model path        |
| `HF_MODEL_PATH`        | `./guff/whisper-quran-v1`       | Hugging Face model path       |
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
