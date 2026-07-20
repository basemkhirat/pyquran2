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

# Dependencies for Modal (Hugging Face Whisper on Linux GPU).
# add_local_* must be last; use copy=True on the requirements file so we can run pip before it.
IMAGE = (
    modal.Image.debian_slim()
    .add_local_file("requirements-modal.txt", "/requirements-modal.txt", copy=True)
    .run_commands("pip install -r /requirements-modal.txt")
    .env({"PYTHONPATH": "/root/pyquran2", "SESSIONS_DIR": SESSIONS_PATH})
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
    volumes={"/data": SESSIONS_VOLUME},
    # min_containers=1,  # always keep 1 container warm — no scale-to-zero, no cold starts
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def serve():
    from backend.main import socket_app
    from backend import session_store

    # Volume writes are only durable once committed, and reads only see other containers'
    # writes after a reload. backend/ never imports modal, so it calls these through hooks.
    session_store.set_commit_hook(SESSIONS_VOLUME.commit)
    session_store.set_reload_hook(SESSIONS_VOLUME.reload)
    return socket_app
