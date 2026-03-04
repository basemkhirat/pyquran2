.PHONY: backend frontend

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
