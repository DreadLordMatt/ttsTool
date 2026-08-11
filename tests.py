#!/usr/bin/env python
"""tests.py — unit tests for script_parser + routing. No model loads.

Run:  ./tts-env/bin/python tests.py
"""

import sys
import tempfile
from pathlib import Path

import engines
from script_parser import (
    EMOTIONS, Chunk, SpeakerSpec, apply_pronunciations, build_plan,
    chunk_paragraph, parse_frontmatter, parse_script, split_pause_tags,
    strip_links_for_chatterbox,
)

PASS = 0


def ok(cond, label, detail=""):
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


def expect_error(fn, label, needle=""):
    try:
        fn()
    except ValueError as e:
        ok(needle.lower() in str(e).lower(), label, f"(got: {e})")
        return
    raise AssertionError(f"FAIL: {label} — expected ValueError")


ref = Path(tempfile.mkdtemp()) / "sam.wav"
ref.write_bytes(b"RIFF0000WAVE")   # existence is all the parser checks

print("— header forms —")
h, body = parse_frontmatter("---\nspeakers:\n  ALEX: af_bella\n---\n\nX.")
ok(h.speakers["ALEX"].voice == "af_bella" and h.speakers["ALEX"].speed is None,
   "short form = voice only")
h, _ = parse_frontmatter(
    f"---\nmodel: kokoro\nvoice: af_heart\nspeed: 1.2\npauses: {{sentence: 0.3}}\n"
    f"pronunciations: {{SQL: sequel}}\nspeakers:\n  SAM:\n    voice: bm_george\n"
    f"    speed: 1.1\n    emotion: excited\n    ref_audio: {ref}\n---\n\nX.")
s = h.speakers["SAM"]
ok(h.model == "kokoro" and h.voice == "af_heart" and h.speed == 1.2
   and h.pauses == {"sentence": 0.3} and h.pronunciations == {"SQL": "sequel"},
   "long-form header globals")
ok(s.voice == "bm_george" and s.speed == 1.1 and s.emotion == "excited"
   and s.ref_audio == str(ref), "long-form speaker fields")
h, body = parse_frontmatter("---\nJust a divider\n---\n\nReal text.")
ok(h.speakers == {} and body.startswith("---"), "plain --- divider not eaten")
expect_error(lambda: parse_frontmatter("---\nspeakers:\n  A: am_bogus\n---\n\nX."),
             "bad voice in header", "unknown voice")
expect_error(lambda: parse_frontmatter("---\nspeakers:\n  A:\n    vioce: af_heart\n---\n\nX."),
             "typo'd speaker key", "unknown key")
expect_error(lambda: parse_frontmatter("---\nspeakers:\n  A:\n    emotion: hyped\n---\n\nX."),
             "bad emotion in header", "unknown emotion")
expect_error(lambda: parse_frontmatter("---\nspeakers:\n  A:\n    ref_audio: nope.wav\n---\n\nX."),
             "missing ref file", "not found")
expect_error(lambda: parse_frontmatter("---\nmodel: gpt\n---\n\nX."), "bad model", "model")

print("— script parsing —")
turns = parse_script("ALEX: Hi.\n\nSAM (calm): Hello there.")
ok(turns[1].speaker == "SAM" and turns[1].emotion == "calm"
   and turns[1].text == "Hello there.", "emotion parenthetical")
turns = parse_script("WARNING: do not eat.\n\nPlain text.")
ok(turns[0].speaker is None and turns[0].text.startswith("WARNING:"),
   "single-label gate keeps text verbatim")
turns = parse_script("ALEX (excited): Hi.\n\nJust plain text.")
ok(turns[0].speaker is None and "ALEX (excited): Hi." == turns[0].text,
   "gate re-serializes emotion parenthetical")
expect_error(lambda: parse_script("A (hyped): x.\n\nB: y."), "bad inline emotion", "unknown emotion")

print("— pause tags —")
parts = split_pause_tags("One. [pause] Two. [PAUSE 2.5] Three. [pause 99]")
ok(parts[1] == 0.6 and parts[3] == 2.5 and parts[5] == 10.0,
   "pause default, value, case, cap")
ok(split_pause_tags("Keep [sic] as is.") == ["Keep [sic] as is."], "literal [sic] untouched")

print("— link handling —")
t, m = strip_links_for_chatterbox("Say [kokoro](/kOkO/) and [word](2) and [docs](http://x).")
ok(t == "Say kokoro and word and docs." and m, "links reduced, markup flagged")
t, m = strip_links_for_chatterbox("Only a [link](https://y).")
ok(not m, "plain URL link is not markup")

print("— pronunciations —")
t, _ = apply_pronunciations("Use SQL now.", {"SQL": "sequel"}, "kokoro")
ok(t == "Use sequel now.", "respelling substitution")
t, _ = apply_pronunciations("Say Kokoro.", {"Kokoro": "/kO/"}, "kokoro")
ok(t == "Say [Kokoro](/kO/).", "phoneme entry becomes misaki link")
t, skipped = apply_pronunciations("Say Kokoro.", {"Kokoro": "/kO/"}, "chatterbox")
ok(t == "Say Kokoro." and skipped, "phoneme entry skipped on chatterbox")
t, _ = apply_pronunciations("A [SQL](/x/) query on SQL.", {"SQL": "sequel"}, "kokoro")
ok(t == "A [SQL](/x/) query on sequel.", "no substitution inside links")
t, _ = apply_pronunciations("SQLite is not SQL.", {"SQL": "sequel"}, "kokoro")
ok(t == "SQLite is not sequel.", "word-boundary only")

print("— chunking —")
long_sent = "word " * 200
ok(all(len(c) <= 601 for c in chunk_paragraph(long_sent, 600)), "kokoro cap 600")
ok(all(len(c) <= 301 for c in chunk_paragraph(long_sent, 300)), "chatterbox cap 300")

print("— routing matrix —")
CASES = [
    (dict(header_model="chatterbox"), "chatterbox", "header"),
    (dict(header_model="kokoro", any_ref=True), "kokoro", "header"),
    (dict(override="chatterbox"), "chatterbox", "explicitly"),
    (dict(any_ref=True), "chatterbox", "reference audio"),
    (dict(any_ref=True, speakers_present=True, all_have_refs=True), "chatterbox", "reference audio"),
    (dict(any_ref=True, speakers_present=True, all_have_refs=False), "kokoro", "default"),
    (dict(has_markup=True, emotions_present=True), "kokoro", "markup"),
    (dict(preset_voices_requested=True, emotions_present=True), "kokoro", "preset"),
    (dict(emotions_present=True), "chatterbox", "emotion"),
    (dict(emotions_present=True, speakers_present=True), "kokoro", "default"),
    (dict(), "kokoro", "default"),
]
for kwargs, want_model, want_reason in CASES:
    model, reason = engines.route(**kwargs)
    ok(model == want_model and want_reason in reason,
       f"route({kwargs or 'nothing'}) -> {model}", f"reason={reason}")

print("— build_plan integration —")
script = f"""---
speed: 1.1
speakers:
  ALEX:
    voice: af_bella
    speed: 1.2
  SAM:
    ref_audio: {ref}
---

ALEX (excited): Big news! [pause 2] Huge.

SAM: Steady on.
"""
plan = build_plan(script)
ok(plan.model_key == "kokoro" and "preset" in plan.reason,
   "mixed refs + preset voice routes kokoro")
alex_chunks = [c for c in plan.chunks if c.speaker == "ALEX" and c.text]
want = 1.1 * 1.2 * EMOTIONS["excited"].k_speed
ok(abs(alex_chunks[0].speed - want) < 1e-9, "speed = global*speaker*emotion")
pause_only = [c for c in plan.chunks if not c.text]
ok(len(pause_only) == 1 and pause_only[0].pause_after == 2.0, "[pause 2] chunk emitted")
ok(any("ignored by Kokoro" in w for w in plan.warnings), "ref-on-kokoro warning")

plan = build_plan(f"He said hello. [pause] More.", cli_refs={"*": str(ref)})
ok(plan.model_key == "chatterbox" and plan.chunks[0].ref_audio == str(ref),
   "global ref routes chatterbox and lands on chunks")
ok(plan.chunks[0].params.get("exaggeration") == 0.5, "chatterbox neutral params")

plan = build_plan("Say [Kokoro](/kO/) please.\n\nAnd more text here.",
                  model_override="chatterbox")
ok(all("(/kO/)" not in c.text for c in plan.chunks), "markup stripped for chatterbox")
ok(any("Kokoro-only" in w for w in plan.warnings), "markup-stripped warning")

plan = build_plan("ALEX: Hi there friend.\n\nSAM: Hello back at you.")
ok(plan.model_key == "kokoro" and plan.cast["ALEX"].voice == "am_michael"
   and plan.cast["SAM"].voice == "bm_george", "legacy two-speaker auto palette")

print("— back-compat wrappers —")
import speak
chunks, cast = speak.build_chunks("ALEX: Hi.\n\nSAM: Yo.", "am_michael", {"SAM": "af_heart"})
ok(cast == {"ALEX": "am_michael", "SAM": "af_heart"}, "build_chunks wrapper cast")
ok(isinstance(chunks[0], Chunk), "build_chunks returns Chunk objects")

print(f"\nAll {PASS} checks passed.")
