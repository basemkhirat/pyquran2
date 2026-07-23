.PHONY: backend frontend setup-backend modal-serve modal-deploy

# Start the FastAPI + Socket.IO backend. Run 'make setup-backend' once if venv doesn't exist.
backend:
	venv/bin/uvicorn backend.main:socket_app --reload --host 0.0.0.0 --port 8001

# Create venv and install backend deps. Run once before 'make backend'.
# Needs Python 3.10+: torch >= 2.9 publishes no wheels for 3.9, and pip's failure
# there is a wall of resolver output that never names the version as the cause.
# Override the interpreter if 'python3' is older: make setup-backend PYTHON=python3.12
PYTHON ?= python3

setup-backend:
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { \
		echo "Error: $(PYTHON) is $$($(PYTHON) -V 2>&1); this project needs Python 3.10+."; \
		echo "  brew install python@3.12 && make setup-backend PYTHON=python3.12"; \
		exit 1; }
	$(PYTHON) -m venv venv
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
