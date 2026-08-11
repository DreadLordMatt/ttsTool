#!/usr/bin/env python
"""speak.py — turn a text file into an MP3 using local TTS (mlx-audio).

Models: Kokoro (54 preset voices, fast) and Chatterbox (real emotion control,
voice cloning from reference audio). Auto-routes per script; override with
--model or a `model:` line in the script header.

Usage:
    python speak.py script.txt
    python speak.py script.txt --voice bm_george --out episode.mp3 --speed 1.1
    python speak.py script.txt --model chatterbox --ref-audio me.wav
    python speak.py --text "Quick test sentence."
    python speak.py --list-voices | --list-emotions | --list-models
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import engines
import script_parser
from script_parser import (  # re-exported for gui.py and back-compat
    DEFAULT_VOICE, EMOTIONS, OTHER_LANGUAGES, VOICES, SpeakerSpec,
    build_plan, check_emotion, is_valid_voice, parse_script,
)

# Back-compat: the Kokoro repo id, formerly the single model constant.
MODEL_ID = engines.MODELS["kokoro"].repo


def parse_frontmatter(text, base_dir=None):
    return script_parser.parse_frontmatter(text, base_dir=base_dir)


def build_chunks(text, default_voice, speaker_voices=None):
    """Back-compat wrapper: returns (chunks, {speaker: voice}) like the old API."""
    cli = {name: SpeakerSpec(voice=v, source="cli")
           for name, v in (speaker_voices or {}).items()}
    plan = build_plan(text, default_voice=default_voice, cli_speakers=cli)
    return plan.chunks, {name: s.voice for name, s in plan.cast.items()}


def load_tts_model():
    return engines.get_engine("kokoro").load()


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def print_voices():
    print(f"Kokoro preset voices ({MODEL_ID}):\n")
    for group, voices in VOICES:
        print(f"  {group}:")
        for name, desc in voices:
            print(f"    {name:<13} {desc}")
        print()
    print(f"  Blends work too: --voice \"af_heart,af_bella\" averages the voices.")
    print(f"  {OTHER_LANGUAGES}")


def print_emotions():
    print("Emotion tags — use as 'NAME (emotion): ...' in scripts, or --emotion:\n")
    print(f"  {'tag':<10} {'Kokoro (approx.)':<28} Chatterbox (real)")
    for tag, e in EMOTIONS.items():
        kokoro = f"speed ×{e.k_speed:.2f}, pauses ×{e.k_pause:.2f}"
        chatterbox = f"exaggeration {e.cb_exaggeration:.2f}"
        print(f"  {tag:<10} {kokoro:<28} {chatterbox}")
    print("\n  On Kokoro, emotions are approximated with pacing; Chatterbox acts them.")
    print("  Tip: on Kokoro, 'whisper' pairs well with --voice af_nicole.")


def print_models():
    print("Models:\n")
    for spec in engines.MODELS.values():
        state = "downloaded" if engines.is_downloaded(spec.key) else f"not downloaded ({spec.size_hint})"
        caps = []
        caps.append("54 preset voices" if spec.preset_voices else "voice via reference audio")
        caps.append("real emotion control" if spec.emotion_param else "emotion approximated")
        caps.append("speed control" if spec.speed_param else "no speed control")
        print(f"  {spec.key:<11} {spec.repo}")
        print(f"              {', '.join(caps)} — {state}")
    print("\nAuto-routing picks Kokoro unless reference audio is provided, or emotion")
    print("tags appear in a single-voice script. Override with --model or 'model:' in")
    print("the script header (header wins over --model).")


# ---------------------------------------------------------------------------
# Synthesis and output
# ---------------------------------------------------------------------------

def synthesize(chunks, model_key="kokoro", on_chunk=None):
    """Run TTS over Chunk objects, return (samples_int16, sample_rate).

    on_chunk(done, total) is called after each chunk for progress reporting.
    """
    import numpy as np

    engine = engines.get_engine(model_key)
    engine.load()
    sample_rate = engine.sample_rate

    pieces = []
    voiced = 0
    for i, chunk in enumerate(chunks):
        if chunk.text.strip():   # "" = pause-only; models must never see empty text
            audio = engine.generate_chunk(chunk)
            if audio.size:
                pieces.append(audio)
                voiced += 1
        if chunk.pause_after > 0:
            pieces.append(np.zeros(int(sample_rate * chunk.pause_after), dtype=np.float32))
        if on_chunk:
            on_chunk(i + 1, len(chunks))
    if voiced == 0:
        print("WARNING: nothing speakable found in the text — the output is silence.")

    audio = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767).astype(np.int16), sample_rate


def write_wav(path, samples, sample_rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())


def write_mp3(path, samples, sample_rate):
    """Write MP3 via ffmpeg (raises FileNotFoundError if ffmpeg is missing)."""
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError("ffmpeg not found — install it with: brew install ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        write_wav(tmp_wav, samples, sample_rate)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_wav),
             "-codec:a", "libmp3lame", "-q:a", "2", str(path)],
            check=True)
    finally:
        tmp_wav.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_kv(value, what, parser, check=None):
    """Parse 'NAME=x,NAME=y' (or a bare value meaning global '*')."""
    result = {}
    if "=" not in value:
        result["*"] = check(value.strip()) if check else value.strip()
        return result
    for pair in value.split(","):
        if not pair.strip():
            continue
        if "=" not in pair:
            parser.error(f"--{what} entries look like NAME=value (got '{pair.strip()}')")
        name, _, v = pair.strip().partition("=")
        result[name.strip().upper()] = check(v.strip()) if check else v.strip()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Turn a text file into an MP3 with local TTS (Kokoro/Chatterbox on Apple Silicon).")
    parser.add_argument("input", nargs="?", help="path to a .txt/.md file")
    parser.add_argument("--text", help="speak this string instead of a file")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"Kokoro voice preset or blend (default: {DEFAULT_VOICE}; see --list-voices)")
    parser.add_argument("--speakers", metavar="NAME=voice,NAME=voice",
                        help="voice per speaker for podcast scripts with 'NAME:' turns")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="speech speed multiplier (default: 1.0; Kokoro only)")
    parser.add_argument("--model", choices=["auto", "kokoro", "chatterbox"], default="auto",
                        help="model to use (default: auto-route per script)")
    parser.add_argument("--ref-audio", metavar="PATH | NAME=path,NAME=path",
                        help="reference clip(s) for Chatterbox voice cloning")
    parser.add_argument("--emotion", help="default emotion tag (see --list-emotions)")
    parser.add_argument("--exaggeration", type=float,
                        help="raw Chatterbox emotion intensity 0..1 (beats --emotion's value)")
    parser.add_argument("--out", help="output file (default: input name with .mp3)")
    parser.add_argument("--list-voices", action="store_true",
                        help="print available Kokoro voices and exit")
    parser.add_argument("--list-emotions", action="store_true",
                        help="print emotion tags and exit")
    parser.add_argument("--list-models", action="store_true",
                        help="print models, capabilities, and download state")
    args = parser.parse_args()

    if args.list_voices:
        print_voices()
        return
    if args.list_emotions:
        print_emotions()
        return
    if args.list_models:
        print_models()
        return

    if bool(args.input) == bool(args.text):
        parser.error("give exactly one input: a text file, or --text \"...\"")

    # Catch bad options BEFORE the slow model load / generation.
    def check_voice(v):
        if not is_valid_voice(v):
            parser.error(f"unknown voice '{v}' — run 'speak --list-voices' to see the options")
        return v

    check_voice(args.voice)
    if not 0.25 <= args.speed <= 3.0:
        parser.error(f"--speed {args.speed} is out of range (use 0.25 to 3.0; 1.0 is normal)")
    if args.emotion:
        try:
            args.emotion = check_emotion(args.emotion.strip().lower())
        except ValueError as e:
            parser.error(str(e))
    if args.exaggeration is not None and not 0.0 <= args.exaggeration <= 1.0:
        parser.error(f"--exaggeration {args.exaggeration} is out of range (0 to 1)")

    cli_speakers = {}
    if args.speakers:
        for name, v in _parse_kv(args.speakers, "speakers", parser, check_voice).items():
            if name == "*":
                parser.error("--speakers entries look like NAME=voice")
            cli_speakers[name] = SpeakerSpec(voice=v, source="cli")

    cli_refs = {}
    if args.ref_audio:
        for name, p in _parse_kv(args.ref_audio, "ref-audio", parser).items():
            path = Path(p).expanduser()
            if not path.is_file():
                parser.error(f"reference audio not found: {path}")
            cli_refs[name] = str(path)

    if args.input:
        source = Path(args.input)
        if not source.is_file():
            sys.exit(f"error: file not found: {source}")
        # utf-8-sig strips a leading BOM if present (plain UTF-8 also reads fine)
        text = source.read_text(encoding="utf-8-sig", errors="replace")
        default_out = source.with_suffix(".mp3")
        base_dir = source.parent
    else:
        text = args.text
        default_out = Path("speech.mp3")
        base_dir = Path.cwd()

    if not text.strip():
        sys.exit("error: input text is empty")

    out = Path(args.out) if args.out else default_out
    if out.suffix.lower() not in (".mp3", ".wav"):
        parser.error(f"--out must end in .mp3 or .wav (got '{out.name}')")
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        plan = build_plan(
            text, default_voice=args.voice, speed=args.speed,
            cli_speakers=cli_speakers, cli_refs=cli_refs,
            cli_emotion=args.emotion, cli_exaggeration=args.exaggeration,
            model_override=None if args.model == "auto" else args.model,
            base_dir=base_dir)
    except ValueError as e:
        sys.exit(f"error: {e}")

    words = len(text.split())
    spec = engines.MODELS[plan.model_key]
    print(f"Model: {plan.model_key} ({spec.repo}) — {plan.reason}")
    if plan.cast:
        cast = ", ".join(
            f"{n} → {s.ref_audio.rsplit('/', 1)[-1] if plan.model_key == 'chatterbox' and s.ref_audio else s.voice}"
            + (f" ({s.emotion})" if s.emotion else "")
            for n, s in plan.cast.items())
        print(f"{words} words in {len(plan.chunks)} chunk(s); speakers: {cast}")
    elif plan.model_key == "chatterbox":
        ref = cli_refs.get("*")
        v = f"cloned from {Path(ref).name}" if ref else "Chatterbox default"
        print(f"{words} words in {len(plan.chunks)} chunk(s); voice={v}")
    else:
        print(f"{words} words in {len(plan.chunks)} chunk(s); voice={args.voice} speed={args.speed}")
    for w in plan.warnings:
        print(f"note: {w}")

    started = time.time()
    from tqdm import tqdm
    with tqdm(total=len(plan.chunks), desc="Generating", unit="chunk") as bar:
        samples, sample_rate = synthesize(plan.chunks, plan.model_key,
                                          on_chunk=lambda done, total: bar.update(1))
    duration = len(samples) / sample_rate

    if out.suffix.lower() == ".wav":
        write_wav(out, samples, sample_rate)
    elif shutil.which("ffmpeg") is None:
        out = out.with_suffix(".wav")
        write_wav(out, samples, sample_rate)
        print("\nWARNING: ffmpeg not found, saved WAV instead of MP3.")
        print("  Install it with:  brew install ffmpeg")
        print(f"  Then convert:     ffmpeg -i \"{out}\" \"{out.with_suffix('.mp3')}\"")
    else:
        write_mp3(out, samples, sample_rate)

    elapsed = time.time() - started
    size_mb = out.stat().st_size / 1e6
    print(f"\nDone: {out}  ({duration/60:.1f} min of audio, {size_mb:.1f} MB, "
          f"generated in {elapsed:.0f}s)")


if __name__ == "__main__":
    main()
