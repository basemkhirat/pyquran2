.PHONY: backend frontend setup-backend modal-serve modal-deploy

# Start the FastAPI + Socket.IO backend. Run 'make setup-backend' once if venv doesn't exist.
backend:
	venv/bin/uvicorn backend.main:socket_app --reload --host 0.0.0.0 --port 8000

# Create venv and install backend deps (python3 required). Run once before 'make backend'.
setup-backend:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt

# Start the Vite dev server (run from project root; install deps with: cd frontend && npm install)
frontend:
	cd frontend && npm run dev

# Modal.com: dev server (ephemeral URL, live reload). Requires: pip install modal && modal token new
modal-serve:
	modal serve modal_app.py

# Modal.com: deploy to production (persistent URL). Requires: pip install modal && modal token new
modal-deploy:
	modal deploy modal_app.py
