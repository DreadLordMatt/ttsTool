#!/usr/bin/env python
"""gui.py — browser GUI for speak.py (Gradio).

Run:  ./tts-env/bin/python gui.py   then open http://127.0.0.1:7860
"""

import tempfile
from pathlib import Path

import gradio as gr

import engines
import speak
from script_parser import EMOTIONS, build_plan, parse_frontmatter, parse_script

VOICE_CHOICES = [
    (f"{name} — {group.lower()}, {desc}", name)
    for group, voices in speak.VOICES
    for name, desc in voices
]
EMOTION_CHOICES = ["(none)"] + list(EMOTIONS)
MODEL_CHOICES = ["Auto", "Kokoro", "Chatterbox"]

HERE = Path(__file__).parent
DOCS = [  # (tab title, filename, gr component kind)
    ("README", "README.md", "markdown"),
    ("Script-writing prompt", "SCRIPT_PROMPT.md", "code"),
]


def read_doc(filename):
    path = HERE / filename
    if path.is_file():
        return path.read_text(encoding="utf-8"), str(path)
    return f"*({filename} not found next to gui.py)*", None


def read_source(file, text):
    if file:
        source = Path(file)
        return source.read_text(encoding="utf-8-sig", errors="replace"), source.stem, source.parent
    return (text or ""), "speech", None


def _plan(file, text, model, voice, speed, emotion, exag, refs):
    raw, stem, base_dir = read_source(file, text)
    plan = build_plan(
        raw, default_voice=voice, speed=speed,
        cli_refs={k: v for k, v in (refs or {}).items() if v},
        cli_emotion=None if emotion in (None, "(none)") else emotion,
        # slider at 0.5 = neutral default; only override emotion values when moved
        cli_exaggeration=None if exag == 0.5 else exag,
        model_override=None if model == "Auto" else model.lower(),
        base_dir=base_dir)
    return raw, stem, plan


def detect_speakers(file, text):
    """Cheap speaker-name parse for the ref-audio rows; never raises."""
    try:
        raw, _, base = read_source(file, text)
        _, body = parse_frontmatter(raw, base_dir=base)
        return list(dict.fromkeys(t.speaker for t in parse_script(body) if t.speaker))
    except ValueError:
        return []


def cast_preview(file, text, model, voice, emotion, exag, refs):
    """Live preview: routed model, reason, warnings, and the resolved cast."""
    if not (file or (text or "").strip()):
        return ""
    try:
        _, _, plan = _plan(file, text, model, voice, 1.0, emotion, exag, refs)
    except ValueError as e:
        return f"⚠️ {e}"
    lines = [f"**Model: {plan.model_key}** — {plan.reason}"]
    if plan.emotions_used:
        approx = " *(approximated on Kokoro)*" if plan.model_key == "kokoro" else ""
        lines.append(f"Emotions in play: *{', '.join(plan.emotions_used)}*{approx}")
    for name, s in plan.cast.items():
        if plan.model_key == "chatterbox":
            ref = s.ref_audio or (refs or {}).get("*")
            v = f"cloned from `{Path(ref).name}`" if ref else "default voice"
        else:
            v = f"`{s.voice}`" + ("  *(from script header)*" if s.source == "header" else "  *(auto)*")
        lines.append(f"- **{name}** → {v}" + (f", emotion: *{s.emotion}*" if s.emotion else ""))
    if not plan.cast:
        lines.append("No speaker labels — single voice throughout.")
    lines += [f"⚠️ {w}" for w in plan.warnings]
    return "\n".join(lines)


def tts(file, text, model, voice, speed, emotion, exag, refs, progress=gr.Progress()):
    raw, stem, _ = read_source(file, text)
    if not raw.strip():
        raise gr.Error("Upload a .txt/.md file or type some text first.")
    try:
        _, _, plan = _plan(file, text, model, voice, speed, emotion, exag, refs)
    except ValueError as e:
        raise gr.Error(str(e))

    progress(0, desc=f"{len(raw.split())} words, {len(plan.chunks)} chunks on {plan.model_key}")
    samples, sample_rate = speak.synthesize(
        plan.chunks, plan.model_key,
        on_chunk=lambda done, total: progress(done / total, desc=f"chunk {done}/{total}"))

    out = Path(tempfile.mkdtemp()) / f"{stem}.mp3"
    note = ""
    try:
        speak.write_mp3(out, samples, sample_rate)
    except FileNotFoundError:
        out = out.with_suffix(".wav")
        speak.write_wav(out, samples, sample_rate)
        note = " as WAV (no ffmpeg — `brew install ffmpeg` for MP3)"
    minutes = len(samples) / sample_rate / 60
    return str(out), (f"Done on **{plan.model_key}** ({plan.reason}): {minutes:.1f} min of "
                      f"audio{note} — player above has a download button.")


with gr.Blocks(title="Local TTS") as demo:
    with gr.Row():
        gr.Markdown("# Local text-to-speech\nKokoro (preset voices) + Chatterbox "
                    "(emotion & voice cloning) — runs entirely on this Mac.", scale=8)
        docs_link = gr.Button("📖 Docs", size="sm", scale=1)

    with gr.Tabs() as tabs:
        with gr.Tab("Generate", id=0):
            with gr.Row():
                with gr.Column():
                    file_in = gr.File(label="Text file (.txt or .md)",
                                      file_types=[".txt", ".md"], type="filepath")
                    text_in = gr.Textbox(label="…or paste text here", lines=5)
                    model_in = gr.Dropdown(MODEL_CHOICES, value="Auto", label="Model",
                                           info="Auto routes per script and says why below")
                    cast_out = gr.Markdown()
                    refs_state = gr.State({})

                    @gr.render(inputs=[file_in, text_in, model_in])
                    def ref_rows(file, text, model):
                        if model == "Kokoro":
                            return   # capability-driven: Kokoro never uses reference audio
                        speakers = detect_speakers(file, text)
                        targets = speakers or ["*"]
                        gr.Markdown("**Reference voices** (Chatterbox) — upload or record "
                                    "5–10 s of clean speech; leave empty for the default voice:")
                        for name in targets:
                            label = "Everyone (global)" if name == "*" else name
                            a = gr.Audio(label=f"Reference — {label}",
                                         sources=["upload", "microphone"], type="filepath")
                            a.change(
                                lambda p, s, n=name: ({**s, n: p} if p
                                                      else {k: v for k, v in s.items() if k != n}),
                                [a, refs_state], refs_state)

                    with gr.Accordion("Script syntax (speakers, emotions, modifiers)", open=False):
                        gr.Markdown(
                            "Each turn is a paragraph starting with `NAME:` or `NAME (emotion):` — "
                            f"emotions: {', '.join(EMOTIONS)}.\n\n"
                            "```text\n---\nspeakers:\n  ALEX: af_bella\n  SAM:\n    voice: bm_george\n"
                            "    emotion: excited\n---\n\nALEX: Welcome back.\n\n"
                            "SAM (calm): Good to be here. [pause 1.5] Let's begin.\n```\n\n"
                            "Inline: `[pause 2]` inserts silence; `[word](/fənimz/)` overrides "
                            "pronunciation and `[word](2)` adds emphasis (Kokoro only). The header "
                            "also takes `model:`, `voice:`, `speed:`, `pauses:`, `pronunciations:`, "
                            "and per-speaker `ref_audio:`. Full details in the **📖 Docs** tab above.")

                    voice_in = gr.Dropdown(VOICE_CHOICES, value=speak.DEFAULT_VOICE,
                                           label="Voice (Kokoro)")
                    speed_in = gr.Slider(0.5, 2.0, value=1.0, step=0.05, label="Speed (Kokoro)")
                    emotion_in = gr.Dropdown(EMOTION_CHOICES, value="(none)", label="Emotion",
                                             info="Default for the whole text; NAME (emotion): overrides per turn")
                    exag_in = gr.Slider(0, 1, value=0.5, step=0.05,
                                        label="Expressiveness (Chatterbox)",
                                        info="0.5 = neutral; moving this overrides the emotion presets")
                    go = gr.Button("Generate", variant="primary")
                with gr.Column():
                    audio_out = gr.Audio(label="Result", type="filepath")
                    status = gr.Markdown()

        with gr.Tab("📖 Docs", id=1):
            gr.Markdown("Reference material for this tool. The **script-writing prompt** is "
                        "meant to be copied into Claude, ChatGPT, or Gemini before you ask it "
                        "to turn research into a script — it teaches the model this tool's "
                        "exact syntax so the output needs no cleanup.")
            with gr.Tabs():
                for title, filename, kind in DOCS:
                    content, path = read_doc(filename)
                    with gr.Tab(title):
                        if kind == "markdown":
                            gr.Markdown(content)
                        else:
                            gr.Code(content, language="markdown", label=filename,
                                    buttons=["copy", "download"], interactive=False)
                        if path:
                            gr.File(value=path, label=f"Download {filename}")

    docs_link.click(lambda: gr.Tabs(selected=1), None, tabs)

    def capability_update(model):
        kokoro_ui = model != "Chatterbox"
        chatterbox_ui = model != "Kokoro"
        return (gr.update(visible=kokoro_ui), gr.update(visible=kokoro_ui),
                gr.update(visible=chatterbox_ui))

    model_in.change(capability_update, model_in, [voice_in, speed_in, exag_in])

    preview_inputs = [file_in, text_in, model_in, voice_in, emotion_in, exag_in, refs_state]
    for source in (file_in, text_in, model_in, voice_in, emotion_in, refs_state):
        source.change(cast_preview, preview_inputs, cast_out)

    go.click(tts, [file_in, text_in, model_in, voice_in, speed_in, emotion_in,
                   exag_in, refs_state], [audio_out, status])

if __name__ == "__main__":
    # show_error surfaces real exception messages in the browser instead of a blank toast
    demo.launch(inbrowser=True, show_error=True)
