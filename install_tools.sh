#!/usr/bin/env bash
# ==============================================================================
# Installer for ExifTool & Ollama (Self-Contained in Project Environment)
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$PROJECT_DIR/bin"
VENV_BIN="$PROJECT_DIR/.venv/bin"
TMP_DIR="$PROJECT_DIR/cache/installer_tmp"

mkdir -p "$BIN_DIR" "$TMP_DIR"

echo "=========================================================="
echo "  Installing ExifTool & Ollama into project environment..."
echo "=========================================================="

# ------------------------------------------------------------------------------
# 1. Install ExifTool (Perl-based, zero sudo required)
# ------------------------------------------------------------------------------
if [ -f "$BIN_DIR/exiftool" ]; then
    echo "✓ ExifTool is already installed in $BIN_DIR/exiftool"
else
    echo "Downloading ExifTool..."
    cd "$TMP_DIR"
    curl -fsSL https://github.com/exiftool/exiftool/archive/refs/tags/13.15.tar.gz -o exiftool.tar.gz || \
    curl -fsSL https://exiftool.org/Image-ExifTool-13.15.tar.gz -o exiftool.tar.gz

    tar -xzf exiftool.tar.gz
    EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*exiftool*" | head -n 1)

    cp -R "$EXTRACTED_DIR/lib" "$BIN_DIR/"
    cp "$EXTRACTED_DIR/exiftool" "$BIN_DIR/exiftool"
    chmod +x "$BIN_DIR/exiftool"

    echo "✓ ExifTool successfully installed to $BIN_DIR/exiftool"
fi

# Symlink into .venv/bin so python's exiftool helper finds it immediately
if [ -d "$VENV_BIN" ]; then
    ln -sf "$BIN_DIR/exiftool" "$VENV_BIN/exiftool"
    ln -sf "$BIN_DIR/lib" "$VENV_BIN/lib"
fi

# ------------------------------------------------------------------------------
# 2. Install Ollama for macOS (Native Apple Silicon Binary)
# ------------------------------------------------------------------------------
if [ -f "$BIN_DIR/ollama" ]; then
    echo "✓ Ollama binary already installed in $BIN_DIR/ollama"
else
    echo "Downloading Ollama for macOS..."
    cd "$TMP_DIR"
    curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o Ollama-darwin.zip
    unzip -q -o Ollama-darwin.zip

    if [ -d "Ollama.app" ]; then
        # Copy CLI binary to project bin
        cp "Ollama.app/Contents/Resources/ollama" "$BIN_DIR/ollama"
        chmod +x "$BIN_DIR/ollama"

        # Also copy Ollama.app to ~/Applications or /Applications if permissible
        if [ -w "/Applications" ]; then
            cp -R "Ollama.app" "/Applications/"
            echo "✓ Copied Ollama.app to /Applications/"
        else
            mkdir -p "$HOME/Applications"
            cp -R "Ollama.app" "$HOME/Applications/"
            echo "✓ Copied Ollama.app to ~/Applications/"
        fi
        echo "✓ Ollama CLI installed to $BIN_DIR/ollama"
    fi
fi

# Symlink into .venv/bin
if [ -d "$VENV_BIN" ]; then
    ln -sf "$BIN_DIR/ollama" "$VENV_BIN/ollama"
fi

# Clean up temporary installer files
rm -rf "$TMP_DIR"

echo "=========================================================="
echo "  ✓ Verifying Installed Binaries:"
echo "  - ExifTool: $($BIN_DIR/exiftool -ver)"
echo "  - Ollama:   $($BIN_DIR/ollama --version)"
echo "=========================================================="

