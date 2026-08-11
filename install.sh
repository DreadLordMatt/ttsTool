#!/usr/bin/env bash
#
# install.sh — set up ttsTool: virtual environment, dependencies, espeak fix.
#
# Usage, from the repo folder:
#     ./install.sh              # install, then offer to add shell aliases
#     ./install.sh --no-alias   # install only, don't touch your shell config
#     ./install.sh --force      # rebuild the venv from scratch
#
# Safe to re-run: an existing venv is reused unless you pass --force, and the
# aliases are only appended once.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/tts-env"
ALIAS_MARKER="# ttsTool aliases"
DO_ALIAS=1
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --no-alias) DO_ALIAS=0 ;;
        --force)    FORCE=1 ;;
        -h|--help)  sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\n\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
bold "Checking prerequisites"
# ---------------------------------------------------------------------------

[ "$(uname -s)" = "Darwin" ] || die "this tool is macOS-only (the models run on Apple's MLX framework)."

if [ "$(uname -m)" != "arm64" ]; then
    die "Apple Silicon (M1 or later) is required — MLX has no Intel Mac support.
       Detected architecture: $(uname -m)"
fi
ok "Apple Silicon Mac"

# mlx-audio needs >=3.10; 3.13 is what this project is tested on.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
        case "$version" in
            3.13|3.12|3.11) PYTHON="$candidate"; break ;;
        esac
    fi
done
[ -n "$PYTHON" ] || die "need Python 3.11-3.13, none found.
       Install it with:  brew install python@3.13"
ok "$($PYTHON --version) at $(command -v "$PYTHON")"

# espeak-ng stores its data directory in a fixed 160-byte buffer (N_PATH_HOME).
# A longer path silently overflows and the library falls back to the absolute
# path baked in when its wheel was built — producing an error that names the
# wheel builder's CI directory and looks like a corrupted install. Catch it here
# instead, while the fix is still just "clone somewhere shorter".
PYVER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ESPEAK_DATA="$VENV/lib/python$PYVER/site-packages/espeakng_loader/espeak-ng-data"
if [ "${#ESPEAK_DATA}" -ge 160 ]; then
    die "this folder is too deeply nested for espeak-ng, which truncates paths at
       160 characters. Pronunciation would fail with a confusing error.

       Needs to be $(( ${#ESPEAK_DATA} - 159 )) character(s) shorter — move the repo somewhere
       closer to your home folder and re-run.
       (path that would be used, ${#ESPEAK_DATA} chars: $ESPEAK_DATA)"
fi
ok "path length OK for espeak (${#ESPEAK_DATA}/160)"

if command -v ffmpeg >/dev/null 2>&1; then
    ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
else
    warn "ffmpeg not found — output will be saved as WAV instead of MP3."
    warn "Install it any time with:  brew install ffmpeg"
fi

# ---------------------------------------------------------------------------
bold "Building the virtual environment"
# ---------------------------------------------------------------------------

if [ -d "$VENV" ] && [ "$FORCE" -eq 1 ]; then
    echo "  removing existing venv (--force)"
    rm -rf "$VENV"
fi

if [ -d "$VENV" ]; then
    ok "reusing existing tts-env (pass --force to rebuild)"
else
    "$PYTHON" -m venv "$VENV" || die "could not create the virtual environment at $VENV"
    ok "created tts-env"
fi

"$VENV/bin/pip" install --quiet --upgrade pip

echo "  installing packages (a few minutes on first run)…"
# Split deliberately: the second line pins spacy below 4.0 and forces wheels.
# Without --only-binary, pip tries to compile blis from source and fails on
# Python 3.13; without the <3.9 pin it resolves to a prerelease spacy whose
# thinc build breaks against the installed numpy.
"$VENV/bin/pip" install --quiet mlx-audio misaki \
    || die "failed installing mlx-audio/misaki — scroll up for pip's output."
"$VENV/bin/pip" install --quiet --only-binary :all: \
    "spacy>=3.8,<3.9" num2words espeakng-loader phonemizer-fork \
    || die "failed installing the text-processing dependencies."
"$VENV/bin/pip" install --quiet gradio pyyaml \
    || die "failed installing gradio/pyyaml."
ok "dependencies installed"

# ---------------------------------------------------------------------------
bold "Applying the espeak fix"
# ---------------------------------------------------------------------------

# misaki 0.7.4 only looks for libespeak-ng at a hardcoded Homebrew path, so its
# pronunciation fallback silently fails and unusual words crash generation with
# "TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'". This
# .pth runs at interpreter startup and points phonemizer at the pip-bundled
# copy, so no system-wide espeak-ng install is needed.
SITE="$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"

cat > "$SITE/_espeak_fix.py" << 'PYEOF'
try:
    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    if not EspeakWrapper._ESPEAK_LIBRARY:
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
except Exception:
    pass
PYEOF
echo "import _espeak_fix" > "$SITE/_espeak_fix.pth"
ok "espeak fix installed"

# ---------------------------------------------------------------------------
bold "Verifying"
# ---------------------------------------------------------------------------

# Import-level check only. A real generation would pull ~360 MB of model
# weights, which belongs in the user's first run, not in the installer.
"$VENV/bin/python" - << 'PYEOF' || die "the install did not verify — see the error above."
import sys

import mlx_audio.tts.utils            # noqa: F401
import misaki.en                      # noqa: F401
import gradio, yaml                   # noqa: F401
from phonemizer.backend.espeak.wrapper import EspeakWrapper

if not EspeakWrapper._ESPEAK_LIBRARY:
    sys.exit("espeak library did not load — the .pth fix is not taking effect")

from misaki import espeak
espeak.EspeakFallback(british=False)   # raises if espeak-ng is unreachable
PYEOF
ok "imports and pronunciation engine working"

"$VENV/bin/python" "$REPO/tests.py" >/dev/null 2>&1 \
    && ok "unit tests pass" \
    || warn "unit tests did not pass — run ./tts-env/bin/python tests.py to see why"

# ---------------------------------------------------------------------------
if [ "$DO_ALIAS" -eq 1 ]; then
bold "Shell aliases"
# ---------------------------------------------------------------------------

case "${SHELL##*/}" in
    zsh)  RC="$HOME/.zshrc" ;;
    bash) RC="$HOME/.bash_profile" ;;
    *)    RC="" ;;
esac

RC_SHORT="~/${RC#"$HOME"/}"

if [ -z "$RC" ]; then
    warn "unrecognised shell (${SHELL##*/}) — add these lines to your shell config yourself:"
    printf '      alias speak="%s/speak"\n      alias speak-gui="%s/speak-gui"\n' "$REPO" "$REPO"
elif [ -f "$RC" ] && grep -qF "$ALIAS_MARKER" "$RC"; then
    ok "aliases already present in $RC_SHORT"
else
    printf '\n%s\nalias speak="%s/speak"\nalias speak-gui="%s/speak-gui"\n' \
        "$ALIAS_MARKER" "$REPO" "$REPO" >> "$RC"
    ok "added speak and speak-gui to $RC_SHORT"
    warn "run 'source $RC_SHORT' or open a new terminal to use them"
fi
fi

# ---------------------------------------------------------------------------
printf '\n'
bold "Done."
cat << EOF

  Try it (first run downloads the Kokoro model, ~360 MB, one time):

      ./speak sample.txt

  Or open the browser interface:

      ./speak-gui

  Everything else — voices, emotions, podcast scripts, voice cloning — is in
  README.md, and also in the GUI's Docs tab.
EOF
