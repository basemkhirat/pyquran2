"""
Deploy pyquran2 (FastAPI + Socket.IO + Whisper) to Modal.com with GPU.

Setup:
  1. pip install modal && modal token new
  2. In Modal dashboard (modal.com): create a Secret with your env vars
     (HF_MODEL_PATH, HAFS_JSON_PATH, SOCKET_AUTH_API_KEY, etc.).
     Add HF_TOKEN (Hugging Face token) for higher rate limits and faster model downloads.
  3. From project root:
     modal serve modal_app.py   # dev: ephemeral URL, live reload
     modal deploy modal_app.py  # prod: persistent URL

Use the printed URL as VITE_SOCKET_URL in your frontend.
"""

import modal

# Recorded sessions (info.json + recording.wav) must outlive the container: Modal's
# filesystem is ephemeral and scales to zero. SESSIONS_DIR is set in the image env rather
# than the Secret — Secret values layer over image env and would silently win.
SESSIONS_VOLUME = modal.Volume.from_name("pyquran-sessions", create_if_missing=True)
SESSIONS_PATH = "/data/sessions"

# Persists the Hugging Face cache across containers. Without it every cold start re-downloads
# the model weights (~2.4GB for muaalem-model-v3_2), since `models` is excluded from the image.
HF_CACHE = modal.Volume.from_name("pyquran2-hf-cache", create_if_missing=True)
HF_CACHE_PATH = "/cache"

# Dependencies for Modal (Hugging Face Whisper on Linux GPU).
# add_local_* must be last; use copy=True on the requirements file so we can run pip before it.
IMAGE = (
    modal.Image.debian_slim()
    .add_local_file("requirements-modal.txt", "/requirements-modal.txt", copy=True)
    .run_commands("pip install -r /requirements-modal.txt")
    # HF_HOME must be set on the image, not at runtime: transformers reads it at import time,
    # and by then it is too late to redirect the cache onto the volume.
    .env({
        "PYTHONPATH": "/root/pyquran2",
        "SESSIONS_DIR": SESSIONS_PATH,
        "HF_HOME": f"{HF_CACHE_PATH}/huggingface",
    })
    .add_local_dir(
        ".",
        remote_path="/root/pyquran2",
        ignore=[
            ".venv",
            "venv",
            ".env",
            "node_modules",
            "__pycache__",
            ".git",
            "guide",
            "frontend",
            "dist",
            "*.pyc",
            ".cursor",
            "data",
            "models",
            ".pytest_cache",
        ],
    )
)

app = modal.App("memorize-quran", image=IMAGE)


@app.function(
    gpu="L4", # lower: T4
    secrets=[modal.Secret.from_name("custom-secret")],
    volumes={"/data": SESSIONS_VOLUME, HF_CACHE_PATH: HF_CACHE},
    # min_containers=1,  # always keep 1 container warm — no scale-to-zero, no cold starts
)
# Each active session runs a forward pass every STREAMING_INTERVAL_MS. 100 of those on one L4
# would queue rather than serve — with muaalem (0.6B + 11 CTC heads) far sooner than with
# wav2vec2, and a container serving both models holds both sets of weights. Raise this only
# alongside a measurement of per-tick latency under load.
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def serve():
    from backend.main import socket_app
    from backend import session_store

    # Volume writes are only durable once committed, and reads only see other containers'
    # writes after a reload. backend/ never imports modal, so it calls these through hooks.
    session_store.set_commit_hook(SESSIONS_VOLUME.commit)
    session_store.set_reload_hook(SESSIONS_VOLUME.reload)
    return socket_app
