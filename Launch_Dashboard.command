#!/bin/bash
# ==============================================================================
# Double-Click Launcher for AI RAW Photo Archival Dashboard
# ==============================================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Ensure Ollama daemon is running in background if not already active
if ! curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "Starting local Ollama vision engine..."
    /Applications/Ollama.app/Contents/Resources/ollama serve > /dev/null 2>&1 &
    sleep 2
fi

echo "=========================================================="
echo "  Launching RAW Photo Archival Control Center..."
echo "  URL: http://localhost:8501"
echo "=========================================================="

source "$DIR/.venv/bin/activate"
export PATH="$DIR/bin:$DIR/.venv/bin:$PATH"

# Open browser and run Streamlit dashboard
streamlit run app.py

