# ttsTool — text file in, MP3 out

Local text-to-speech that runs entirely on your Mac. No cloud, no API keys.
Uses [mlx-audio](https://github.com/Blaizzy/mlx-audio) with two models on
Apple Silicon: **Kokoro** (fast, 54 preset voices) and **Chatterbox** (real
emotion control, clones a voice from a short audio clip). The tool picks
whichever fits your script automatically — see [Models](#models) below.

Requires an Apple Silicon Mac (M1 or later), Python 3.13, and `ffmpeg`
(`brew install ffmpeg`) for MP3 output.

---

## Setup (once)

```bash
git clone https://github.com/DreadLordMatt/ttsTool.git && cd ttsTool && ./install.sh
```

That builds a virtual environment in `tts-env/` (nothing is installed
system-wide), applies a small fix that Kokoro needs, verifies the result, and
offers to add `speak` / `speak-gui` shell aliases. It takes a few minutes and
is safe to re-run — an existing environment is reused unless you pass
`--force`.

Options: `--no-alias` leaves your shell config alone, `--force` rebuilds the
environment from scratch, and `--with-models` pre-downloads the model weights
(see [Models](#models)) instead of fetching them on first use.

Then try it — the first run downloads the Kokoro model (~390 MB, one time):

```bash
./speak sample.txt
```

<details>
<summary>Doing it by hand instead</summary>

```bash
python3.13 -m venv tts-env && ./tts-env/bin/pip install mlx-audio misaki && ./tts-env/bin/pip install --only-binary :all: "spacy>=3.8,<3.9" num2words espeakng-loader phonemizer-fork && ./tts-env/bin/pip install gradio pyyaml
```

Then the espeak fix — misaki looks for espeak-ng at a hardcoded Homebrew path,
so this points it at the pip-bundled copy instead (see
[Quirks](#quirks-this-setup-papers-over)):

```bash
cat > tts-env/lib/python3.13/site-packages/_espeak_fix.py << 'EOF'
try:
    import espeakng_loader
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    if not EspeakWrapper._ESPEAK_LIBRARY:
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
except Exception:
    pass
EOF
echo "import _espeak_fix" > tts-env/lib/python3.13/site-packages/_espeak_fix.pth
```

The `--only-binary` flag and the `<3.9` spacy pin both matter: without them
pip tries to compile blis from source and fails on Python 3.13.

</details>

---

## The one command you actually want

`install.sh` offers to set these up for you. To add them yourself later, run
this **from the repo folder** — it expands to wherever you cloned it:

```bash
printf 'alias speak="%s/speak"\nalias speak-gui="%s/speak-gui"\n' "$PWD" "$PWD" >> ~/.zshrc && source ~/.zshrc
```

Either way, from any folder:

```bash
speak myfile.txt
```

That's it. It writes `myfile.mp3` next to `myfile.txt`.

More examples:

```bash
speak notes.txt --voice bm_george --speed 1.1
```

```bash
speak notes.txt --out episode.mp3
```

```bash
speak --text "Just say this one sentence."
```

```bash
speak --list-voices
```

```bash
speak --list-models
```

```bash
speak --list-emotions
```

**The GUI** (opens in your browser — upload a file, pick a model, drag the
speed slider; press Ctrl-C in the terminal to stop it):

```bash
speak-gui
```

> Why this works without activating anything: `speak` is a tiny script in the
> repo folder that calls the venv's own Python directly. The venv only needs
> "activating" if you want to type `python` yourself.

---

## Running things manually (if you don't use the alias)

Run these **from the repo folder**. Each one includes the venv step.

**Convert a text file to MP3:**

```bash
source tts-env/bin/activate && python speak.py sample.txt
```

**List the voices:**

```bash
source tts-env/bin/activate && python speak.py --list-voices
```

**Launch the browser GUI** (file upload, voice dropdown, speed slider —
it opens http://127.0.0.1:7860 in your browser; press Ctrl-C in the terminal to stop it):

```bash
source tts-env/bin/activate && python gui.py
```

---

## Voices (the useful ones)

Default is `am_michael`. Full list: `speak --list-voices`.

| Voice | Sounds like |
|---|---|
| `af_heart` | US female — warm, natural. Best overall quality. |
| `af_bella` | US female — expressive, energetic. |
| `af_nicole` | US female — soft, whispery (ASMR-ish). |
| `af_sarah` | US female — neutral, professional. |
| `am_michael` | US male — warm, even. Good narrator. **(default)** |
| `am_fenrir` | US male — deeper, more energetic. |
| `am_adam` | US male — deep. |
| `bf_emma` | UK female — natural. Best UK female. |
| `bm_george` | UK male — classic RP narrator. Best UK male. |
| `bm_fable` | UK male — storyteller. |

There are 54 presets total, including Japanese, Mandarin, Spanish, French,
Hindi, Italian, and Portuguese ones (Japanese/Mandarin need extra packages).

## Podcast scripts (multiple speakers)

Format the text like a screenplay — each turn is its own paragraph (blank line
between turns) starting with the speaker's name in CAPS and a colon:

```text
ALEX: Welcome to the show. Today we're talking about...

SAM: Great to be here, Alex.
```

Then just:

```bash
speak podcast_script.md
```

Any number of speakers works. They get distinct voices automatically, in
order of appearance: `am_michael`, `bm_george`, `af_heart`, `bf_emma`,
`am_fenrir`, `af_sarah`. The names themselves are never spoken.

**To cast voices yourself, put a header at the very top of the script** (ask
Claude to include it when it writes the script):

```text
---
speakers:
  ALEX: af_bella
  SAM: bm_george
---

ALEX: Welcome to the show...
```

The header is never read aloud, other keys in it (like `title:`) are ignored,
and speakers you don't list still get automatic voices. You can also override
from the command line — `--speakers` beats the header:

```bash
speak podcast_script.md --speakers "ALEX=af_heart"
```

This all works in the GUI too: pick a file or paste text and the detected
cast appears immediately, marked *(from script header)* or *(auto)*, before
you generate anything. A lone `WARNING:`-style paragraph won't trigger
speaker mode — it needs at least two labeled paragraphs.

## Speed

`--speed 1.0` is normal. `1.1`–`1.25` is good for long documents. `0.9` is
slightly slower and more deliberate. Range is roughly 0.5–2.0. Kokoro only —
Chatterbox doesn't have a speed control (see below).

---

## Models

**No model weights ship with this repo** — it's about 120 KB of Python. Each
model is downloaded from its own source repository the first time you use it
and cached in `~/.cache/huggingface`, shared with any other Hugging Face
tooling on your machine. Nothing is vendored, mirrored, or re-hosted here.

| Model | Source repository | Size | License |
|---|---|---|---|
| Kokoro | [mlx-community/Kokoro-82M-bf16](https://huggingface.co/mlx-community/Kokoro-82M-bf16) | ~390 MB | Apache-2.0, converted from [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) |
| Chatterbox | [mlx-community/chatterbox-fp16](https://huggingface.co/mlx-community/chatterbox-fp16) | ~2.6 GB | Apache-2.0, converted from [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) (MIT) |
| ↳ its tokenizer | [mlx-community/S3TokenizerV2](https://huggingface.co/mlx-community/S3TokenizerV2) | ~470 MB | pulled in automatically by Chatterbox |

`speak --list-models` prints these sources and whether each is cached yet. To
fetch them during install instead of on first run:

```bash
./install.sh --with-models        # Kokoro only
./install.sh --with-models=all    # Kokoro + Chatterbox
```

To point at different weights, change the repo ids in
[engines.py](engines.py) — `MODELS` for Kokoro, `CHATTERBOX_REPO` for
Chatterbox. Anything `mlx-audio` can load will work.

| | Kokoro (default) | Chatterbox |
|---|---|---|
| Voice | 54 named presets (`--voice`) | cloned from a reference clip, or one built-in default voice |
| Emotion | approximated (pacing only) | real — a genuine model parameter |
| Speed | `--speed` works | ignored |
| Speed of generation | ~15× faster than real time | close to real time |
| First-use download | ~390 MB | ~2.6 GB (+470 MB tokenizer) |

**You don't have to choose.** By default (`--model auto`, the GUI's "Auto")
the tool looks at your script and picks:

1. A `model:` line in the script header always wins.
2. `--model kokoro`/`chatterbox` (or the GUI dropdown) always wins next.
3. Reference audio provided → **Chatterbox**.
4. Kokoro-style pronunciation/emphasis markup in the text → **Kokoro**.
5. Named voice presets requested (`--voice`, `--speakers`, header `voice:`) → **Kokoro**.
6. Emotion tags on a single-voice script → **Chatterbox**.
7. Otherwise → **Kokoro**.

Every run prints which model it picked and why: `Model: chatterbox
(mlx-community/chatterbox-fp16) — reference audio provided`. The GUI shows
the same line live as you type. Check anytime with:

```bash
speak --list-models
```

Chatterbox's default voice comes from `mlx-community/chatterbox-fp16` — a
multilingual checkpoint (English works fine; other languages are along for
the ride). If you ever want the English-only original instead, the repo name
is one constant: `CHATTERBOX_REPO` near the top of [engines.py](engines.py).
That checkpoint has *no* default voice, so reference audio becomes mandatory.

## Voice cloning (reference audio)

Give Chatterbox 5–10 seconds of clean speech and it clones that voice. No
ref audio → its built-in default voice.

**Header** (per speaker, or `"*"` for everyone):

```yaml
---
speakers:
  SAM:
    ref_audio: voices/sam.wav   # relative to the script file
---
```

**CLI:**

```bash
speak script.txt --ref-audio me.wav
speak script.txt --ref-audio "ALEX=alex.wav,SAM=sam.wav"
```

**GUI:** pick a file or paste a script; a reference-audio box appears per
detected speaker (or one global box for single-voice text), each with
**upload or record-from-mic**. Providing any reference audio is itself a
routing signal — it switches the script to Chatterbox automatically.

Clip quality matters more than length: a clean 5–10 second recording clones
much better than a long noisy one. Providing audio for only some speakers in
a multi-speaker script is fine — the rest share the default voice, and the
tool warns you about it.

## Emotions

Works on both models, but differently: Chatterbox actually acts the emotion;
on Kokoro it's an approximation built from pacing (speed and pause changes)
since Kokoro has no emotion parameter at all.

```bash
speak --list-emotions
```

| tag | | tag |
|---|---|---|
| `neutral` | | `cheerful` |
| `calm` | | `excited` |
| `serious` | | `angry` |
| `sad` | | `whisper` |

Set per turn, or as a default for the whole script:

```text
ALEX (excited): We finally shipped it!

SAM (calm): Take a breath. There's still testing to do.
```

```bash
speak script.txt --emotion calm
```

The header can also set a speaker's default emotion (`speakers: SAM:
emotion: calm`). On Kokoro, `whisper` pairs well with `--voice af_nicole`.
For direct control over Chatterbox's raw intensity instead of a named tag,
use `--exaggeration 0.0`–`1.0` (0.5 is neutral).

## Pronunciation & fine control

**Pause anywhere**, on either model:

```text
Let me think about that. [pause 1.5] Okay, here's my answer.
```

Bare `[pause]` is 0.6 seconds; `[pause 2.5]` is 2.5 seconds (capped at 10).

**Pronunciation and emphasis** — native Kokoro/misaki syntax, so it's
documented straight from the model, not invented by this tool. **Kokoro
only** (Chatterbox strips these to plain text and warns):

```text
Say [Kokoro](/kˈOkəɹO/) the right way.   ← phoneme override
That was [really](2) surprising.         ← emphasis, -2 to 2
```

Phonemes use misaki's own notation, not strict IPA — when unsure, it's
easier to sound the word out with regular letters than hunt for phoneme
symbols (`[Kokoro](/koh-koh-roh/)` also works reasonably via `#flags#`-free
respelling in the header, see below).

**A pronunciation dictionary** for words you'll reuse across a whole script,
in the header:

```yaml
---
pronunciations:
  SQL: sequel                 # plain respelling — works on both models
  Kokoro: /kˈOkəɹO/           # phoneme form — Kokoro only
---
```

**Punctuation for pacing**: on Kokoro, a single `…` and `—` are proper pause
tokens the model understands — prefer them over `...` and `--`, which read
oddly. Chatterbox flattens that punctuation to commas/dashes regardless, so
use `[pause N]` there for a deliberate beat.

---

## What to expect

**Kokoro**, measured on an M3 MacBook Pro with the 512-word `sample.txt`:
**3.4 minutes of audio, generated in 13 seconds, 1.8 MB MP3** — roughly 15×
faster than real time, so an hour-long document takes about 4 minutes.

**Chatterbox** is much slower and heavier: a 95-word passage took **30
seconds** to generate 30 seconds of audio (close to real-time, not 15×), and
keeps ~2.4 GB of weights loaded. Reasonable for short emotional clips or a
handful of cloned lines; slow for a long document — Kokoro is the better fit
there even with the emotion tradeoff.

First-ever use of a model downloads it (Kokoro ~360 MB, Chatterbox ~2.4 GB,
one time each). Each new Kokoro voice you try downloads one small file (~0.5
MB, one time). Switching between models mid-session drops the other one from
memory, so only one is ever resident.

---

## If it errors out

**`command not found: speak`** — the alias isn't set up in this shell. Run the
`echo 'alias …' >> ~/.zshrc` line at the top of this file, or open a new
terminal window.

**Anything mentioning `ffmpeg`** — ffmpeg converts the audio to MP3. Install it:

```bash
brew install ffmpeg
```

(If ffmpeg is missing, `speak` still saves your audio as a `.wav` file and
tells you so — you don't lose the generation.)

**`No module named …` / import errors** — the venv is missing or broken. Wipe
it and redo [Setup](#setup-once), from the repo folder:

```bash
rm -rf tts-env
```

**`TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'` during
generation** — the espeak fix is missing. Re-run the second block in
[Setup](#setup-once).

**An error naming a path like `/Users/runner/work/espeakng-loader/...`** — that
is someone else's build machine, baked into the espeak-ng wheel. espeak-ng
stores its data directory in a fixed 160-character buffer; if this repo sits
somewhere deeply nested, the real path overflows and the library silently falls
back to that compiled-in one. Move the repo closer to your home folder.
`install.sh` checks for this up front.

**`No conditionals available` (Chatterbox)** — you switched `CHATTERBOX_REPO`
in [engines.py](engines.py) to a checkpoint without a built-in default voice
(see Models above). Either provide `--ref-audio`, or switch the constant back.

**Download/network errors on first run** — the model comes from Hugging Face.
Check your connection and just run the same command again; downloads resume.

**Something else** — run the same command again and read the last few lines of
the error; the real cause is almost always there. The model cache lives in
`~/.cache/huggingface` and is safe to delete if a download got corrupted
(it'll re-download).

---

## What's in this folder

| File | What it is |
|---|---|
| `install.sh` | One-shot setup: venv, dependencies, espeak fix, aliases |
| `speak` | The wrapper — makes `speak myfile.txt` work from anywhere |
| `speak-gui` | Same idea for the GUI — `speak-gui` launches it from anywhere |
| `speak.py` | The CLI: argument parsing, synthesis, audio output |
| `script_parser.py` | Script header/modifier parsing, chunking, the emotion table |
| `engines.py` | Model registry, auto-routing rules, Kokoro/Chatterbox adapters |
| `gui.py` | Browser GUI (Gradio) |
| `tests.py` | Unit tests for parsing/routing — no model load, runs in ~1s |
| `SCRIPT_PROMPT.md` | Paste-into-an-LLM spec so Claude/ChatGPT/Gemini write scripts in this format |
| `sample.txt` | 512-word test file — run `speak sample.txt` to produce `sample.mp3` |
| `tts-env/` | Python virtual environment (Python 3.13) — everything is installed here, nothing system-wide |

Generated audio (`*.mp3`, `*.wav`) and `tts-env/` are gitignored — both are
reproducible, and the venv is 855 MB of platform-specific wheels. On a fresh
clone, run [Setup](#setup-once) to build it.

## Quirks this setup papers over

1. **mlx-audio doesn't declare Kokoro's text dependencies.** Plain
   `pip install mlx-audio` can't run Kokoro. The extra installs (misaki, spacy,
   num2words, espeakng-loader, phonemizer-fork) fix that.
2. **misaki 0.7.4 looks for espeak-ng at a hardcoded Homebrew path.** Instead
   of installing espeak-ng system-wide, `_espeak_fix.py`/`.pth` inside the venv
   points it at the pip-bundled copy. Without it, unusual words (like "Kokoro")
   crash generation with the `NoneType` error above.
3. **Chatterbox's `chatterbox-fp16` checkpoint is multilingual v2**, chosen
   because it ships a default voice (no reference clip required). It's a
   noticeably bigger, slower model than Kokoro — see What to expect.

---

## License

MIT — see [LICENSE](LICENSE). The models themselves carry their own licenses
([Kokoro](https://huggingface.co/hexgrad/Kokoro-82M),
[Chatterbox](https://huggingface.co/ResembleAI/chatterbox)); this repo only
contains code that calls them.
