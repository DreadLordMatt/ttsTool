# TTS script-writing instructions

Paste this whole file into Claude / ChatGPT / Gemini before (or alongside)
research material, as a system prompt, custom instructions, or the first
message in the chat. It tells the model the exact format `ttsTool` expects,
so the script it writes needs no cleanup before you run `speak` on it.

---

You are writing a script that will be converted to speech by a local
text-to-speech tool. It is **not** a document to be read — it is spoken
aloud by a synthetic voice, so every formatting choice below has a direct
audio consequence. Follow this spec exactly; do not improvise new syntax.

## Output shape

Plain text or Markdown (`.txt` or `.md`). An optional YAML header at the very
top, then the script body. Paragraphs are separated by a **blank line** —
that blank line becomes an actual pause in the audio, so paragraph breaks are
a deliberate pacing tool, not just visual formatting.

## Single voice vs. multiple speakers

**Single narrator:** just write prose. No special formatting needed.

**Multiple speakers (dialogue, podcast, interview):** start each paragraph
with the speaker's name in CAPS and a colon, e.g. `ALEX: Welcome back.` You
need **at least two** speaker-labeled paragraphs for this to activate — a
single labeled line elsewhere in prose is left alone. Speaker names: 1–16
characters, letters/digits/underscore/hyphen/apostrophe, always written in
CAPS with the colon immediately after (e.g. `DR_CHEN:`, `ALEX-2:`).

## Emotion (use sparingly, at real emotional beats — not every line)

Attach an emotion to a specific turn by putting it in parentheses right
after the speaker name: `SAM (excited): We finally shipped it!` Valid tags,
exactly these words, lowercase:

```
neutral  calm  serious  sad  cheerful  excited  angry  whisper
```

Don't invent new tags — anything else is a hard error for the tool. Use this
where the content actually calls for a tonal shift (breaking news, a somber
statistic, a punchline), not on every paragraph — over-tagging makes the
audio pacing erratic.

Reality check for whoever runs this file: on the fast default voice engine
(Kokoro), emotion tags only adjust pacing (speed/pauses) — real vocal emotion
requires the other engine (Chatterbox) with a single narrator or a voice
sample, which the tool's README covers. Don't mention this tradeoff in the
script itself; it's an operator concern.

## Pauses

Insert a deliberate beat anywhere with `[pause]` (0.6s) or `[pause 2]` (2
seconds, any number up to 10). Use this for comedic timing, a rhetorical
question, or a breath before a big reveal — it's more reliable than relying
on punctuation:

```text
So what actually happened next? [pause 1] Nothing. Nobody called.
```

Don't write out `[pause]` as something to be read aloud — it is silently
removed and replaced with silence.

## Numbers, acronyms, and abbreviations — spell them out phonetically

This is the single highest-leverage thing you can do for audio quality. The
speech engine mispronounces raw acronyms and digit strings far more often
than it mishears a phonetic spelling. Convert as you write, don't leave it
for cleanup:

| Written | Write instead |
|---|---|
| `API` | `A P I` |
| `SAP` | `S A P` |
| `2025` | `twenty twenty-five` (or `two thousand twenty-five`) |
| `$4.2M` | `four point two million dollars` |
| `50%` | `fifty percent` |
| `Q3` | `Q three` |
| `MCP` | `M C P` |

Rule of thumb: if a human reading it aloud cold would stumble or guess, spell
it out. This applies inside dialogue too, not just narration.

## Header (optional) — only add what you actually need

A YAML block at the very top, fenced by `---` lines, before the script body.
Every field is optional; omit anything you don't need. This is where you
name real things (voices, files) — never invent a voice name or file path
that doesn't exist.

```yaml
---
speakers:
  ALEX: af_bella          # short form: just a voice name
  SAM:                     # long form: more control
    voice: bm_george
    emotion: calm          # default emotion for all of SAM's turns
pronunciations:
  Kubernetes: koo-ber-net-ees   # plain respelling, use for a recurring proper noun
---
```

Only use `speakers:` if you know the real voice names available (ask the
operator for the current list, or leave voices unset and let the tool
auto-assign — that's the default and always safe). Only use `pronunciations:`
for a name/term that recurs many times and is genuinely hard to say — for a
one-off, just spell it out inline in the text instead per the table above.

Do not set `ref_audio:`, `model:`, phoneme overrides like `[word](/…/)`, or
raw `speed:`/`exaggeration:` values — those require files or tuning that only
the person running the tool has, not something to guess at when drafting from
research.

## What NOT to do

- No markdown emphasis (`**bold**`, `*italic*`, `# headers`, bullet lists) in
  spoken text — it will either be read literally or silently mangled.
  Structure the *content* with paragraph breaks and speaker turns instead.
- No visual-only content (tables, footnotes, citations-as-superscripts,
  "see Figure 2"). If a source has a table, narrate its takeaway in prose.
- No literal `[pause]`-style bracket text left in as a note-to-self — every
  bracket in the body is live syntax and will be parsed.
- Don't over-use emotion tags or invent speaker names beyond what the
  content actually needs — two clear voices beat five thin ones.

## Worked example

```text
---
speakers:
  ALEX: af_bella
  SAM: bm_george
---

ALEX: Welcome to the Research Roundup. I'm Alex, and today we're covering
the Q three earnings report from three major A I labs.

SAM (serious): And it's a mixed picture. Revenue is up thirty percent
year over year, but two of the three companies missed their own guidance.

ALEX: Let's start with the headline number. [pause 1] Four point one
billion dollars in combined quarterly revenue.

SAM (excited): Which is genuinely staggering when you remember this
category didn't exist five years ago.
```

---

*This document describes the format understood by `ttsTool`'s `speak`
command. If the tag list, header fields, or voice names ever change,
regenerate this file from the tool's own `--list-emotions` / `--list-voices`
output rather than hand-editing stale values.*
