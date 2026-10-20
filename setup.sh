#!/usr/bin/env bash
# ==============================================================================
# Setup & Health Check Script
# AI-Powered RAW Photo Archival System v2.0
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================================="
echo "  AI-Powered RAW Photo Archival System (v2.0)"
echo "  Setting up environment & verifying dependencies..."
echo "=========================================================="

# 1. Check for ExifTool
if ! command -v exiftool &> /dev/null; then
    echo "⚠️  ExifTool is not detected on your PATH."
    if command -v brew &> /dev/null; then
        echo "Installing ExifTool via Homebrew..."
        brew install exiftool || echo "Could not install via brew automatically."
    else
        echo "💡 Install ExifTool via Homebrew ('brew install exiftool') or download the macOS package from https://exiftool.org/"
    fi
else
    echo "✓ ExifTool detected: $(exiftool -ver)"
fi

# 2. Check for Ollama
if ! command -v ollama &> /dev/null; then
    echo "⚠️  Ollama is not detected on your PATH."
    echo "💡 Download & install Ollama for macOS from https://ollama.com/"
else
    echo "✓ Ollama detected."
    echo "Checking if qwen2.5-vl:7b is pulled..."
    if ollama list 2>/dev/null | grep -q "qwen2.5-vl:7b"; then
        echo "✓ Model 'qwen2.5-vl:7b' is ready."
    else
        echo "Pulling qwen2.5-vl:7b model (this may take a few minutes)..."
        ollama pull qwen2.5-vl:7b || echo "Please run 'ollama pull qwen2.5-vl:7b' once Ollama is running."
    fi
fi

# 3. Setup Python Virtual Environment
VENV_DIR="$PROJECT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment in .venv..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 4. Install Python Dependencies
echo "Installing Python dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Initialize directories
mkdir -p "$PROJECT_DIR/cache" "$PROJECT_DIR/data" "$PROJECT_DIR/logs" "$PROJECT_DIR/reference_faces"

# 6. Run quick verification test
echo "Running system verification test (dry_run.py)..."
python3 "$PROJECT_DIR/dry_run.py"
python3 "$PROJECT_DIR/router.py" --dry-run

echo "=========================================================="
echo "  ✓ Setup Complete! All systems operational."
echo "  To launch the live dashboard:"
echo "    streamlit run app.py"
echo "  To run the ingestion pipeline on your archive:"
echo "    python3 pipeline.py"
echo "=========================================================="

