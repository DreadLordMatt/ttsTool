"""engines.py — model registry, auto-routing, and engine adapters.

One engine is resident at a time (Chatterbox alone is ~2.4GB of weights);
get_engine() drops the previous engine and clears the MLX cache on switch.
"""

from dataclasses import dataclass

# Swap Chatterbox checkpoints here. chatterbox-fp16 (multilingual v2) ships a
# built-in default voice (conds.safetensors); Chatterbox-TTS-fp16 (English v1)
# does not — with it, reference audio becomes mandatory.
CHATTERBOX_REPO = "mlx-community/chatterbox-fp16"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo: str               # Hugging Face repo; weights are fetched from here on first use
    preset_voices: bool     # supports Kokoro-style named voices
    uses_ref_audio: bool    # voice comes from a reference clip
    emotion_param: bool     # real emotion control (exaggeration)
    speed_param: bool       # consumes a speed multiplier
    max_chunk_chars: int    # per-generation text cap
    size_hint: str
    license: str = ""       # license of the repo we actually download
    upstream: str = ""      # the original project the MLX conversion came from

    @property
    def url(self):
        return f"https://huggingface.co/{self.repo}"


# No weights ship with this tool. Each model is downloaded from its source
# repository on first use and cached in ~/.cache/huggingface, shared with any
# other Hugging Face tooling on the machine.
MODELS = {
    "kokoro": ModelSpec("kokoro", "mlx-community/Kokoro-82M-bf16",
                        preset_voices=True, uses_ref_audio=False,
                        emotion_param=False, speed_param=True,
                        max_chunk_chars=600, size_hint="~390 MB",
                        license="apache-2.0",
                        upstream="hexgrad/Kokoro-82M"),
    "chatterbox": ModelSpec("chatterbox", CHATTERBOX_REPO,
                            preset_voices=False, uses_ref_audio=True,
                            emotion_param=True, speed_param=False,
                            max_chunk_chars=300, size_hint="~2.6 GB",
                            license="apache-2.0",
                            upstream="ResembleAI/chatterbox (MIT)"),
}

# Pulled in automatically by Chatterbox the first time it loads — listed here
# so the download is documented rather than a surprise.
COMPANION_DOWNLOADS = {
    "chatterbox": [("mlx-community/S3TokenizerV2", "~470 MB", "speech tokenizer")],
}


def model_sources():
    """Every repo this tool may download, for display in --list-models and docs."""
    rows = []
    for spec in MODELS.values():
        rows.append((spec.key, spec.repo, spec.size_hint, spec.license, spec.upstream))
        for repo, size, note in COMPANION_DOWNLOADS.get(spec.key, []):
            rows.append((f"{spec.key} ⤷", repo, size, "", note))
    return rows


def route(*, header_model=None, override=None, any_ref=False, all_have_refs=False,
          speakers_present=False, has_markup=False, preset_voices_requested=False,
          emotions_present=False):
    """Pick a model. First match wins; the reason string is shown to the user."""
    if header_model:
        return header_model, f"script header says '{header_model}'"
    if override:
        return override, "model set explicitly"
    if any_ref and (not speakers_present or all_have_refs):
        return "chatterbox", "reference audio provided"
    if has_markup:
        return "kokoro", "phoneme/stress markup needs Kokoro"
    if preset_voices_requested:
        return "kokoro", "preset voices requested"
    if emotions_present and not speakers_present:
        return "chatterbox", "emotion tags, single voice"
    return "kokoro", "default"


def is_downloaded(key):
    """Cheap presence check: does the cache have this repo's config.json?

    Deliberately not snapshot_download(local_files_only=True) — that demands
    every file in the repo (READMEs, .gitattributes, ...), so it reports
    "not downloaded" even after real use, since mlx-audio only ever fetches
    the files it actually needs (weights, config, voice packs).
    """
    from huggingface_hub import try_to_load_from_cache
    return isinstance(try_to_load_from_cache(MODELS[key].repo, "config.json"), str)


class KokoroEngine:
    spec = MODELS["kokoro"]

    def __init__(self):
        self._model = None

    def load(self):
        if self._model is None:
            _download_note(self.spec)
            print(f"Loading model {self.spec.repo} ...")
            from mlx_audio.tts.utils import load_model
            self._model = load_model(self.spec.repo)
        return self._model

    @property
    def sample_rate(self):
        return self.load().sample_rate

    def generate_chunk(self, chunk):
        import numpy as np
        model = self.load()
        pieces = [
            np.array(result.audio, dtype=np.float32)
            for result in model.generate(
                text=chunk.text, voice=chunk.voice, speed=chunk.speed,
                # Kokoro lang_code is the voice's first letter (a=American, ...)
                lang_code=chunk.voice[0], verbose=False)
        ]
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)


class ChatterboxEngine:
    spec = MODELS["chatterbox"]

    def __init__(self):
        self._model = None
        self._conds = {}    # ref_audio path (or None) -> Conditionals

    def load(self):
        if self._model is None:
            _download_note(self.spec)
            print(f"Loading model {self.spec.repo} ...")
            from mlx_audio.tts.utils import load_model
            self._model = load_model(self.spec.repo)
        return self._model

    @property
    def sample_rate(self):
        return self.load().sample_rate

    def _conditionals(self, ref_audio, exaggeration):
        model = self.load()
        if ref_audio not in self._conds:
            if ref_audio is None:
                if getattr(model, "_conds", None) is None:
                    raise RuntimeError(
                        f"{self.spec.repo} has no built-in voice — provide reference "
                        f"audio (header ref_audio:, --ref-audio, or the GUI recorder)")
                self._conds[None] = model._conds
            else:
                print(f"Preparing voice from {ref_audio} ...")
                self._conds[ref_audio] = model.prepare_conditionals(
                    ref_audio, self.sample_rate, exaggeration)
        return self._conds[ref_audio]

    def generate_chunk(self, chunk):
        import numpy as np
        model = self.load()
        p = chunk.params
        conds = self._conditionals(chunk.ref_audio, p.get("exaggeration", 0.5))
        pieces = [
            np.array(result.audio, dtype=np.float32)
            for result in model.generate(
                text=chunk.text, conds=conds,
                exaggeration=p.get("exaggeration", 0.5),
                cfg_weight=p.get("cfg_weight", 0.5),
                temperature=p.get("temperature", 0.8),
                verbose=False)
        ]
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)


_ENGINES = {"kokoro": KokoroEngine, "chatterbox": ChatterboxEngine}
_active = None


def get_engine(key):
    """Single-engine cache: switching models drops the old one and frees MLX memory."""
    global _active
    if _active is not None and _active.spec.key != key:
        _active = None
        import mlx.core as mx
        mx.clear_cache()
    if _active is None:
        _active = _ENGINES[key]()
    return _active


def _download_note(spec):
    if not is_downloaded(spec.key):
        print(f"NOTE: first use of {spec.key} downloads {spec.size_hint} "
              f"from Hugging Face ({spec.repo}) — one time.")
