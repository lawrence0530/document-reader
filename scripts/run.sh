#!/usr/bin/env bash
# ======================================================================
#  Document Reader Skill - single entry point (run.sh, macOS / Linux)
#  Usage:
#    ./scripts/run.sh <file-path> [--file-type-hint pdf] [--password xxx]
#                      [--pages 1-50] [--ocr] [--mineru-mode flash|precision]
#                      [--no-prefer-mineru-sdk] [--enable-ocr-plugin]
#                      [--output-format json|text|markdown] [--chars 2000]
#
#  This script does three things:
#    1) Sanity-check the environment (uv present, Python 3.12, deps installed)
#    2) Bootstrap if anything is missing (download uv + Python 3.12 + uv sync)
#    3) Forward every argument to src/main.py
#  The SKILL root is one level above this scripts/ directory.
# ======================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SKILL_DIR"

LOCAL_UV_DIR="$SKILL_DIR/.local/bin"
mkdir -p "$LOCAL_UV_DIR"

UV_EXE=""
if command -v uv >/dev/null 2>&1; then
    UV_EXE="$(command -v uv)"
elif [ -x "$LOCAL_UV_DIR/uv" ]; then
    UV_EXE="$LOCAL_UV_DIR/uv"
fi

# -------- 2. Bootstrap uv if not present --------
if [ -z "$UV_EXE" ]; then
    echo "[run] uv not found - bootstrapping uv (single-file binary)..."

    # 2a. Try Astral's official install.sh first
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$LOCAL_UV_DIR/../" sh >/dev/null 2>&1 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$LOCAL_UV_DIR/../" sh >/dev/null 2>&1 || true
    fi
    [ -x "$LOCAL_UV_DIR/../bin/uv" ] && cp -f "$LOCAL_UV_DIR/../bin/uv" "$LOCAL_UV_DIR/uv" 2>/dev/null || true
    [ -x "$HOME/.local/bin/uv" ] && cp -f "$HOME/.local/bin/uv" "$LOCAL_UV_DIR/uv" 2>/dev/null || true

    if [ -x "$LOCAL_UV_DIR/uv" ]; then
        UV_EXE="$LOCAL_UV_DIR/uv"
    else
        # 2b. If official script failed, grab single-file tarball directly from GitHub
        echo "[run] Official install failed - downloading uv binary directly from GitHub..."
        OS_KERNEL="$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')"
        ARCH="$(uname -m 2>/dev/null)"
        case "$ARCH" in
            x86_64|amd64)    TARGET_ARCH="x86_64" ;;
            aarch64|arm64)   TARGET_ARCH="aarch64" ;;
            *) echo "[run] FAILED: unsupported arch: $ARCH"; exit 10 ;;
        esac
        case "$OS_KERNEL" in
            darwin*)  UV_ARTIFACT="uv-$TARGET_ARCH-apple-darwin.tar.gz" ;;
            linux*)   UV_ARTIFACT="uv-$TARGET_ARCH-unknown-linux-gnu.tar.gz" ;;
            *)        echo "[run] FAILED: unsupported OS: $OS_KERNEL"; exit 10 ;;
        esac
        UV_URL="https://github.com/astral-sh/uv/releases/latest/download/$UV_ARTIFACT"
        UV_TGZ="$(mktemp)"
        if command -v curl >/dev/null 2>&1; then
            curl -L --fail -sSf "$UV_URL" -o "$UV_TGZ" || { echo "[run] FAILED: curl download failed"; rm -f "$UV_TGZ"; exit 10; }
        elif command -v wget >/dev/null 2>&1; then
            wget -q "$UV_URL" -O "$UV_TGZ" || { echo "[run] FAILED: wget download failed"; rm -f "$UV_TGZ"; exit 10; }
        else
            echo "[run] FAILED: neither curl nor wget available"; exit 10
        fi
        tar -xzf "$UV_TGZ" -C "$LOCAL_UV_DIR" --strip-components=1 2>/dev/null \
            || tar -xzf "$UV_TGZ" -C "$LOCAL_UV_DIR" 2>/dev/null
        rm -f "$UV_TGZ"
        if [ -f "$LOCAL_UV_DIR/uv" ]; then
            chmod +x "$LOCAL_UV_DIR/uv"
            UV_EXE="$LOCAL_UV_DIR/uv"
        fi
    fi

    if [ -z "$UV_EXE" ]; then
        echo "[run] FAILED to auto-download uv. Please install manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 10
    fi
    export PATH="$LOCAL_UV_DIR:$PATH"
fi

# -------- 3. Ensure Python 3.12 + deps installed --------
#     Put the .venv outside the (potentially read-only / sandboxed) skill install directory.
#     Fallback order (first writable wins):
#       1. ${DOCUMENT_READER_VENV_ROOT}  (user override, if set)
#       2. ${XDG_CACHE_HOME}/document-reader
#       3. ${HOME}/.cache/document-reader
#       4. ${TMPDIR:-/tmp}/document-reader
#       5. parent of SKILL_DIR: <skills_parent>/.document-reader-cache
if [ -n "${DOCUMENT_READER_VENV_ROOT:-}" ]; then
    CACHE_ROOT="${DOCUMENT_READER_VENV_ROOT}"
elif [ -n "${XDG_CACHE_HOME:-}" ] && [ -d "$XDG_CACHE_HOME" ]; then
    CACHE_ROOT="${XDG_CACHE_HOME}/document-reader"
elif [ -n "${HOME:-}" ]; then
    CACHE_ROOT="${HOME}/.cache/document-reader"
elif [ -n "${TMPDIR:-}" ] && [ -d "$TMPDIR" ]; then
    CACHE_ROOT="${TMPDIR}/document-reader"
else
    PARENT="$(cd "$SKILL_DIR/.." && pwd)"
    CACHE_ROOT="${PARENT}/.document-reader-cache"
fi
export UV_PROJECT_ENVIRONMENT="${CACHE_ROOT}/venv"
mkdir -p "${CACHE_ROOT}" 2>/dev/null || true

echo "[run] Ensuring Python 3.12 + dependencies installed..."
echo "[run] Using virtualenv: ${UV_PROJECT_ENVIRONMENT}"
"$UV_EXE" sync --all-extras

# -------- 4. Real work: forward all arguments to main (module mode, avoids script-directory sys.path trap) --------
"$UV_EXE" run python -m document_reader.main "$@"
