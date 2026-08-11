"""script_parser.py — header schema, script/modifier parsing, and chunk planning.

Turns raw script text into a RenderPlan: model-ready chunks plus the resolved
cast, routed model, and warnings. Both the CLI (speak.py) and GUI (gui.py)
call build_plan() so what they show is exactly what generation will do.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Voices (Kokoro presets)
# ---------------------------------------------------------------------------

# Kokoro preset voices. Name prefix: 1st letter = language/accent
# (a=American, b=British English), 2nd letter = gender (f/m).
VOICES = [
    ("American female", [
        ("af_heart",    "warm, natural — best overall quality"),
        ("af_bella",    "expressive, energetic — high quality"),
        ("af_nicole",   "soft, whispery (ASMR-style)"),
        ("af_aoede",    "clear, even"),
        ("af_kore",     "bright"),
        ("af_sarah",    "neutral, professional"),
        ("af_alloy",    "plain"),
        ("af_jessica",  "plain"),
        ("af_nova",     "plain"),
        ("af_river",    "plain"),
        ("af_sky",      "light"),
    ]),
    ("American male", [
        ("am_michael",  "warm, even — default, good narrator"),
        ("am_fenrir",   "deeper, energetic"),
        ("am_puck",     "playful"),
        ("am_adam",     "deep"),
        ("am_echo",     "plain"),
        ("am_eric",     "plain"),
        ("am_liam",     "plain"),
        ("am_onyx",     "deep"),
        ("am_santa",    "Santa Claus style"),
    ]),
    ("British female", [
        ("bf_emma",     "natural — best UK female"),
        ("bf_isabella", "soft"),
        ("bf_alice",    "bright"),
        ("bf_lily",     "light"),
    ]),
    ("British male", [
        ("bm_george",   "classic RP narrator — best UK male"),
        ("bm_fable",    "storyteller"),
        ("bm_daniel",   "plain"),
        ("bm_lewis",    "deeper"),
    ]),
]

OTHER_LANGUAGES = (
    "Non-English presets also exist (first letter = language): jf_/jm_ Japanese, "
    "zf_/zm_ Mandarin, ef_/em_ Spanish, ff_ French, hf_/hm_ Hindi, if_/im_ Italian, "
    "pf_/pm_ Portuguese. Japanese/Mandarin need extra packages (pip install misaki[ja] / misaki[zh])."
)

DEFAULT_VOICE = "am_michael"

# Voices auto-assigned to speakers in order of first appearance (kept distinct).
AUTO_SPEAKER_VOICES = ["am_michael", "bm_george", "af_heart", "bf_emma",
                       "am_fenrir", "af_sarah"]

_KNOWN_VOICES = {name for _, voices in VOICES for name, _ in voices}


def is_valid_voice(v):
    """English presets must match the table; other languages just need the right
    shape. Comma-blends ("af_heart,af_bella") are valid if every part is."""
    parts = [p.strip() for p in v.split(",")] if "," in v else [v]
    return all(
        p in _KNOWN_VOICES or re.fullmatch(r"[efhijpz][fm]_\w+", p)
        for p in parts if p
    ) and any(parts)


# ---------------------------------------------------------------------------
# Emotions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Emotion:
    k_speed: float          # Kokoro: multiplier on turn speed (approximation)
    k_pause: float          # Kokoro: multiplier on that turn's pauses
    cb_exaggeration: float  # Chatterbox: real emotion intensity (0..1)
    cb_cfg_weight: float    # Chatterbox: lower = more expressive
    cb_temperature: float


EMOTIONS = {
    "neutral":  Emotion(1.00, 1.00, 0.50, 0.50, 0.80),
    "calm":     Emotion(0.92, 1.40, 0.30, 0.50, 0.70),
    "serious":  Emotion(0.95, 1.20, 0.45, 0.55, 0.75),
    "sad":      Emotion(0.88, 1.60, 0.40, 0.55, 0.75),
    "cheerful": Emotion(1.06, 0.85, 0.65, 0.45, 0.85),
    "excited":  Emotion(1.12, 0.70, 0.80, 0.35, 0.90),
    "angry":    Emotion(1.08, 0.80, 0.90, 0.35, 0.85),
    "whisper":  Emotion(0.90, 1.30, 0.25, 0.60, 0.70),
}


def check_emotion(tag):
    if tag not in EMOTIONS:
        raise ValueError(f"unknown emotion '{tag}' — valid: {', '.join(EMOTIONS)}")
    return tag


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

DEFAULT_PAUSES = {"sentence": 0.15, "paragraph": 0.45}
PAUSE_TAG_DEFAULT = 0.6   # bare [pause]
PAUSE_TAG_MAX = 10.0


@dataclass
class Chunk:
    text: str                  # model-ready; "" = pause-only, never sent to a model
    pause_after: float         # seconds of silence appended (we own ALL silence)
    voice: str | None = None   # Kokoro preset/blend; None on Chatterbox
    speed: float = 1.0         # global * speaker * emotion (Kokoro consumes)
    speaker: str | None = None
    ref_audio: str | None = None            # path; Chatterbox only
    params: dict = field(default_factory=dict)   # Chatterbox emotion params


@dataclass
class SpeakerSpec:
    voice: str | None = None
    speed: float | None = None
    emotion: str | None = None
    ref_audio: str | None = None
    source: str = "auto"       # where the voice came from: header | cli | auto


@dataclass
class Header:
    model: str | None = None
    voice: str | None = None
    speed: float | None = None
    pauses: dict = field(default_factory=dict)
    pronunciations: dict = field(default_factory=dict)
    speakers: dict = field(default_factory=dict)   # NAME -> SpeakerSpec


@dataclass
class Turn:
    speaker: str | None
    emotion: str | None
    text: str


@dataclass
class RenderPlan:
    chunks: list
    cast: dict                 # NAME -> resolved SpeakerSpec, in order of appearance
    model_key: str             # "kokoro" | "chatterbox"
    reason: str                # one-line routing explanation
    warnings: list
    header: Header
    emotions_used: list = field(default_factory=list)   # every tag in play, for display


# ---------------------------------------------------------------------------
# Frontmatter header
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^\s*---[ \t]*\n(.*?)\n---[ \t]*\n?", re.S)

_SPEAKER_KEYS = {"voice", "speed", "emotion", "ref_audio"}


def _parse_speaker_entry(name, value, base_dir):
    spec = SpeakerSpec(source="header")
    if isinstance(value, str):                      # short form: NAME: voice
        spec.voice = value.strip()
    elif isinstance(value, dict):                   # long form
        unknown = set(value) - _SPEAKER_KEYS
        if unknown:
            raise ValueError(f"unknown key(s) {sorted(unknown)} for speaker '{name}' "
                             f"— allowed: {sorted(_SPEAKER_KEYS)}")
        if value.get("voice") is not None:
            spec.voice = str(value["voice"]).strip()
        if value.get("speed") is not None:
            spec.speed = float(value["speed"])
        if value.get("emotion") is not None:
            spec.emotion = check_emotion(str(value["emotion"]).strip().lower())
        if value.get("ref_audio") is not None:
            spec.ref_audio = _resolve_ref(str(value["ref_audio"]).strip(), base_dir, name)
    else:
        raise ValueError(f"speaker '{name}' must map to a voice name or a mapping")
    if spec.voice is not None and not is_valid_voice(spec.voice):
        raise ValueError(f"script header assigns unknown voice '{spec.voice}' to "
                         f"'{name}' — run --list-voices to see the options")
    return spec


def _resolve_ref(path_str, base_dir, owner):
    p = Path(path_str).expanduser()
    if not p.is_absolute() and base_dir is not None:
        p = Path(base_dir) / p
    if not p.is_file():
        raise ValueError(f"ref_audio for '{owner}' not found: {p}")
    return str(p)


def parse_frontmatter(text, base_dir=None):
    """Split an optional YAML header off the script; returns (Header, body).

    Anything that isn't a YAML mapping is treated as ordinary text (a plain
    '---' divider doesn't eat the script). Unknown top-level keys are ignored;
    unknown keys inside a speaker mapping are errors.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return Header(), text
    import yaml
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        meta = None
    if not isinstance(meta, dict):
        return Header(), text
    body = text[m.end():]

    h = Header()
    if meta.get("model") is not None:
        model = str(meta["model"]).strip().lower()
        if model not in ("auto", "kokoro", "chatterbox"):
            raise ValueError(f"header model: must be auto, kokoro, or chatterbox (got '{model}')")
        h.model = None if model == "auto" else model
    if meta.get("voice") is not None:
        h.voice = str(meta["voice"]).strip()
        if not is_valid_voice(h.voice):
            raise ValueError(f"header voice '{h.voice}' unknown — run --list-voices")
    if meta.get("speed") is not None:
        h.speed = float(meta["speed"])
        if not 0.25 <= h.speed <= 3.0:
            raise ValueError(f"header speed {h.speed} out of range (0.25 to 3.0)")
    pauses = meta.get("pauses") or {}
    if not isinstance(pauses, dict):
        raise ValueError("'pauses:' in the header must map sentence/paragraph to seconds")
    for k in pauses:
        if k not in DEFAULT_PAUSES:
            raise ValueError(f"unknown pause '{k}' — allowed: {sorted(DEFAULT_PAUSES)}")
        h.pauses[k] = float(pauses[k])
    prons = meta.get("pronunciations") or {}
    if not isinstance(prons, dict):
        raise ValueError("'pronunciations:' in the header must map word: replacement")
    h.pronunciations = {str(k): str(v).strip() for k, v in prons.items()}
    speakers = meta.get("speakers") or {}
    if not isinstance(speakers, dict):
        raise ValueError("'speakers:' in the header must map NAME: voice (or a mapping)")
    for name, value in speakers.items():
        h.speakers[str(name).strip().upper()] = _parse_speaker_entry(name, value, base_dir)
    return h, body


# ---------------------------------------------------------------------------
# Script body: speaker turns with optional (emotion) parentheticals
# ---------------------------------------------------------------------------

# "ALEX: text" or "ALEX (excited): text" at the start of a paragraph.
SPEAKER_RE = re.compile(r"^([A-Z][A-Z0-9_'-]{0,15})(?:\s*\(([a-z][a-z ]{0,23})\))?:\s+(.+)$", re.S)


def parse_script(text):
    """Split text into Turns. Speaker labels only take effect when at least two
    labeled paragraphs exist — a lone 'WARNING: ...' paragraph stays plain text."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    turns = []
    for para in paragraphs:
        m = SPEAKER_RE.match(para)
        if m:
            emotion = m.group(2)
            if emotion is not None:
                emotion = check_emotion(emotion.strip().lower())
            turns.append(Turn(m.group(1), emotion, m.group(3)))
        else:
            turns.append(Turn(None, None, para))
    if sum(1 for t in turns if t.speaker) < 2:
        turns = [Turn(None, None, _unparse(t)) for t in turns]
    return turns


def _unparse(turn):
    if not turn.speaker:
        return turn.text
    tag = f" ({turn.emotion})" if turn.emotion else ""
    return f"{turn.speaker}{tag}: {turn.text}"


def assign_specs(turns, header, cli_specs=None, default_voice=DEFAULT_VOICE):
    """Resolve one SpeakerSpec per speaker. Field-wise precedence:
    CLI/GUI > header > auto palette (voices only)."""
    order = list(dict.fromkeys(t.speaker for t in turns if t.speaker))
    cast = {}
    taken = set()
    for name in order:
        spec = SpeakerSpec()
        header_spec = header.speakers.get(name)
        cli_spec = (cli_specs or {}).get(name)
        for source, other in (("header", header_spec), ("cli", cli_spec)):
            if other is None:
                continue
            for f in ("voice", "speed", "emotion", "ref_audio"):
                v = getattr(other, f)
                if v is not None:
                    setattr(spec, f, v)
                    if f == "voice":
                        spec.source = source
        cast[name] = spec
        if spec.voice:
            taken.add(spec.voice)
    # Auto palette for voiceless speakers, in order of appearance
    pool = [default_voice] + [v for v in AUTO_SPEAKER_VOICES if v != default_voice]
    unused = (v for v in pool if v not in taken)
    for name in order:
        if cast[name].voice is None:
            cast[name].voice = next(unused, default_voice)
    return cast


# ---------------------------------------------------------------------------
# Inline modifiers
# ---------------------------------------------------------------------------

PAUSE_TAG_RE = re.compile(r"\[pause(?:\s+(\d+(?:\.\d+)?))?\]", re.I)
# misaki-style links: [text](payload). Payload /…/ or ±N = markup; else plain link.
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_MARKUP_PAYLOAD_RE = re.compile(r"/.*|[+-]?\d+(?:\.\d+)?|#.*#")


def split_pause_tags(text):
    """Split text on [pause]/[pause N] tags -> list of str runs and float pauses."""
    parts, pos = [], 0
    for m in PAUSE_TAG_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
        seconds = float(m.group(1)) if m.group(1) else PAUSE_TAG_DEFAULT
        parts.append(min(seconds, PAUSE_TAG_MAX))
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return parts


def strip_links_for_chatterbox(text):
    """Reduce [text](payload) links to their text. Returns (text, had_markup)
    where had_markup is True if any payload was misaki phoneme/stress markup."""
    had_markup = False

    def repl(m):
        nonlocal had_markup
        if _MARKUP_PAYLOAD_RE.fullmatch(m.group(2).strip()):
            had_markup = True
        return m.group(1)

    return LINK_RE.sub(repl, text), had_markup


def apply_pronunciations(text, prons, model_key):
    """Apply the header pronunciation dictionary. Respellings are plain word
    substitutions; /phoneme/ entries become misaki links (Kokoro only).
    Never substitutes inside an existing [..](..) link."""
    if not prons:
        return text, False
    skipped_phonemes = False
    protected = []

    def protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = LINK_RE.sub(protect, text)
    for word, repl in prons.items():
        if repl.startswith("/") and repl.endswith("/") and len(repl) > 1:
            if model_key == "chatterbox":
                skipped_phonemes = True
                continue
            replacement = f"[{word}]({repl})"
        else:
            replacement = repl
        text = re.sub(rf"\b{re.escape(word)}\b", replacement.replace("\\", "\\\\"), text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)
    return text, skipped_phonemes


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_paragraph(para, max_chars=600):
    """Split one paragraph into chunk strings on sentence boundaries."""
    para = re.sub(r"\s+", " ", para).strip()
    if not para:
        return []
    # Split after .!?… — also when a closing quote/bracket follows the terminator
    sentences = re.split(r"(?<=[.!?…])\s+|(?<=[.!?…][\"'”’)\]])\s+", para)
    chunks = []
    buf = ""
    for sentence in sentences:
        if buf and len(buf) + len(sentence) + 1 > max_chars:
            chunks.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
        # A single sentence longer than the cap: split on commas/spaces as a last resort
        while len(buf) > max_chars:
            cut = buf.rfind(",", 0, max_chars)
            if cut < max_chars // 2:
                cut = buf.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            chunks.append(buf[:cut + 1].strip())
            buf = buf[cut + 1:].strip()
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------------------
# The single entry point
# ---------------------------------------------------------------------------

def build_plan(text, *, default_voice=DEFAULT_VOICE, speed=1.0, cli_speakers=None,
               cli_refs=None, cli_emotion=None, cli_exaggeration=None,
               model_override=None, base_dir=None):
    """Parse header + body, resolve the cast, route the model, emit chunks."""
    import engines

    header, body = parse_frontmatter(text, base_dir=base_dir)
    turns = parse_script(body)

    # Merge CLI refs/speakers into per-speaker specs
    cli_specs = dict(cli_speakers or {})
    for name, path in (cli_refs or {}).items():
        if name == "*":
            continue
        spec = cli_specs.setdefault(name, SpeakerSpec(source="cli"))
        spec.ref_audio = path
    cast = assign_specs(turns, header, cli_specs, header.voice or default_voice)
    global_ref = (cli_refs or {}).get("*")

    # Routing signals
    body_no_pause = PAUSE_TAG_RE.sub("", body)
    has_markup = any(_MARKUP_PAYLOAD_RE.fullmatch(m.group(2).strip())
                     for m in LINK_RE.finditer(body_no_pause))
    speakers_present = bool(cast)
    any_ref = bool(global_ref) or any(s.ref_audio for s in cast.values())
    all_have_refs = bool(cast) and all(s.ref_audio or global_ref for s in cast.values())
    emotions_present = bool(cli_emotion) or any(t.emotion for t in turns) or \
        any(s.emotion for s in cast.values())
    preset_voices_requested = (
        any(s.voice is not None and s.source in ("header", "cli") for s in cast.values())
        or header.voice is not None
        or (default_voice != DEFAULT_VOICE)
    )
    model_key, reason = engines.route(
        header_model=header.model, override=model_override,
        any_ref=any_ref, all_have_refs=all_have_refs,
        speakers_present=speakers_present, has_markup=has_markup,
        preset_voices_requested=preset_voices_requested,
        emotions_present=emotions_present,
    )

    warnings = []
    if model_key == "kokoro" and any_ref:
        warnings.append("reference audio is ignored by Kokoro (use --model chatterbox to clone)")
    if model_key == "chatterbox":
        if speakers_present and not all_have_refs:
            missing = [n for n, s in cast.items() if not (s.ref_audio or global_ref)]
            warnings.append(f"speakers without ref_audio share the default voice: {', '.join(missing)}")
        if any(s.speed for s in cast.values()) or (header.speed or speed) != 1.0:
            warnings.append("Chatterbox ignores speed settings")
        if has_markup:
            warnings.append("phoneme/stress markup is Kokoro-only — stripped for Chatterbox")

    # Build chunks
    pauses = {**DEFAULT_PAUSES, **header.pauses}
    global_speed = header.speed if header.speed is not None else speed
    chunks = []
    skipped_phonemes = False

    for turn in turns:
        spec = cast.get(turn.speaker, SpeakerSpec())
        emotion_tag = turn.emotion or spec.emotion or cli_emotion
        emo = EMOTIONS[emotion_tag] if emotion_tag else EMOTIONS["neutral"]
        turn_speed = global_speed * (spec.speed or 1.0) * emo.k_speed
        pause_scale = emo.k_pause
        params = {}
        if model_key == "chatterbox":
            params = {
                "exaggeration": (cli_exaggeration if cli_exaggeration is not None
                                 else emo.cb_exaggeration),
                "cfg_weight": emo.cb_cfg_weight,
                "temperature": emo.cb_temperature,
            }
        voice = spec.voice or header.voice or default_voice
        ref = spec.ref_audio or global_ref

        text_t, skipped = apply_pronunciations(turn.text, header.pronunciations, model_key)
        skipped_phonemes = skipped_phonemes or skipped
        if model_key == "chatterbox":
            text_t, _ = strip_links_for_chatterbox(text_t)

        turn_start = len(chunks)
        for part in split_pause_tags(text_t):
            if isinstance(part, float):
                chunks.append(Chunk("", part, speaker=turn.speaker))
                continue
            for piece in chunk_paragraph(part, engines.MODELS[model_key].max_chunk_chars):
                chunks.append(Chunk(piece, pauses["sentence"] * pause_scale,
                                    voice=voice, speed=turn_speed, speaker=turn.speaker,
                                    ref_audio=ref, params=params))
        if len(chunks) > turn_start:
            last = chunks[-1]
            if last.text:   # explicit [pause] at end of turn wins over paragraph pause
                last.pause_after = pauses["paragraph"] * pause_scale

    if skipped_phonemes:
        warnings.append("phoneme pronunciations in header skipped (Kokoro-only)")

    emotions_used = sorted(
        {t.emotion for t in turns if t.emotion}
        | {s.emotion for s in cast.values() if s.emotion}
        | ({cli_emotion} if cli_emotion else set()))
    return RenderPlan(chunks=chunks, cast=cast, model_key=model_key,
                      reason=reason, warnings=warnings, header=header,
                      emotions_used=emotions_used)
