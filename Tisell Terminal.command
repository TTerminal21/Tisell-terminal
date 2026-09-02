#!/bin/bash
# Double-clickable launcher. Finder runs this from an arbitrary working
# directory, so everything is resolved relative to the script itself.
cd "$(dirname "$0")" || exit 1

UI="http://127.0.0.1:8501"
API="http://127.0.0.1:8000"

up() { curl -s -m 2 "$1" >/dev/null 2>&1; }

echo "Tisell Terminal"
echo "==============="

# Only skip startup when BOTH halves answer. A half-up state (a dying process
# still holding a port, or the API down while the UI lingers) must start fresh,
# otherwise the browser opens onto nothing.
if up "$UI/_stcore/health" && up "$API/health"; then
  echo "Already running. Opening $UI"
  open "$UI"
  echo
  echo "You can close this window."
  exit 0
fi

# Clear anything half-alive that would hold the ports.
if up "$UI/_stcore/health" || up "$API/health"; then
  echo "Clearing a stale session…"
  pkill -f "streamlit run dashboard/app.py" 2>/dev/null
  pkill -f "uvicorn --app-dir data-layer" 2>/dev/null
  for _ in $(seq 1 20); do
    up "$UI/_stcore/health" || up "$API/health" || break
    sleep 0.5
  done
fi

if [ ! -x ".venv/bin/python" ]; then
  echo
  echo "ERROR: the virtual environment is missing."
  echo "Rebuild it by running these two lines in Terminal, from this folder:"
  echo '  python3 -m venv .venv'
  echo '  ./.venv/bin/pip install -r requirements.txt'
  echo
  read -r -p "Press Return to close."
  exit 1
fi

echo "Starting… your browser opens on its own in a few seconds."
echo "KEEP THIS WINDOW OPEN — closing it stops the terminal."
echo
./.venv/bin/python run.py

echo
echo "Stopped."
read -r -p "Press Return to close."
