"""Per-generator configuration schemas. Served to the frontend (so the UI is rendered from it) and enforced on
the backend (so the UI is never the only line of defence)."""
import re
from dataclasses import dataclass, field

from ..errors import AppError
from ..generators import ASPECT_RATIOS

MAX_DURATION_SECONDS = 30
ALLOWED_DURATIONS = (10, 20, 30)
DEFAULT_DURATION = 10
LANGUAGES = ["English", "Hindi", "Telugu"]
GENRES = ["Action", "Adventure", "Comedy", "Drama", "Romance", "Thriller", "Horror", "Sci-Fi", "Fantasy", "Mystery", "Crime",
          "Historical", "Custom"]
# Options that point at other assets in the same project (validated against the database in services/generation.py).
AUX_KEYS = {"story_asset_id": "STORY", "lyrics_asset_id": "LYRICS", "script_asset_id": "SCRIPT"}
STORY_SECTION_KEYS = ("SETTING", "LOGLINE", "ACT 1", "ACT 2", "ACT 3", "ENDING")
_LONGER = re.compile(r"\b(make (it|the video|this) longer|as long as (possible|you can)|longest (possible|length)|max(imum)? (length|duration))\b", re.I)


@dataclass
class Field:
    key: str
    label: str
    kind: str = "select"                # select | text | textarea | duration | asset
    choices: list[str] = field(default_factory=list)
    default: str | int | None = None
    help: str = ""
    allow_custom: bool = False          # when choice == "Custom", "<key>_custom" text is kept
    asset_types: list[str] = field(default_factory=list)   # for kind == "asset": which project assets can be picked (keep last: positional order matters)

    def to_dict(self):
        return {"key": self.key, "label": self.label, "kind": self.kind, "choices": self.choices,
                "default": self.default, "help": self.help, "allow_custom": self.allow_custom,
                "asset_types": self.asset_types}


@dataclass
class Spec:
    fields: list[Field]
    prompt_label: str
    prompt_placeholder: str
    prompt_required: bool = True
    prompt_max: int = 4000
    reference: str | None = None        # None | "optional" | "required"
    reference_label: str = ""
    uses_characters: bool = False
    note: str = ""                      # shown under the form (e.g. placeholders for future config)
    ui: str = ""                        # "video" | "face": generator-specific Create sections the frontend renders itself

    def to_dict(self):
        return {"fields": [f.to_dict() for f in self.fields], "prompt": {"label": self.prompt_label,
                "placeholder": self.prompt_placeholder, "required": self.prompt_required, "max_length": self.prompt_max},
                "reference": self.reference, "reference_label": self.reference_label,
                "uses_characters": self.uses_characters, "note": self.note, "ui": self.ui}


STYLE = Field("style", "Style", choices=["Cinematic", "Realistic", "Anime", "3D", "Cartoon", "Fantasy", "Horror", "Sci-Fi",
                                         "Documentary", "Custom"], allow_custom=True)
MUSIC_GENRE = Field("genre", "Genre", choices=["Cinematic", "Pop", "Rock", "Classical", "Electronic", "Ambient", "Folk", "Lo-fi",
                                               "Horror", "Fantasy", "Custom"], allow_custom=True)
STORY_LENGTH = Field("length", "Length", choices=["Short", "Medium", "Long", "Custom"], allow_custom=True,
                     help="Optional. Stories are outlines, so they stay short. Choose Custom to say what you want.")
LANGUAGE = Field("language", "Language", choices=LANGUAGES, default="English")

SPECS: dict[str, Spec] = {
    "video": Spec(
        [Field("method", "Method", choices=["Text to Video", "Image to Video"], default="Text to Video"), STYLE,
         Field("duration_seconds", "Duration", "duration", [str(d) for d in ALLOWED_DURATIONS], DEFAULT_DURATION, "Maximum 30 seconds"),
         Field("aspect_ratio", "Aspect ratio", choices=list(ASPECT_RATIOS), default="16:9")],
        "What should happen in the video?", "e.g. A warrior walks through an ancient city at night while smoke moves through the streets",
        reference="optional", reference_label="Reference image", uses_characters=True, ui="video",
        note="Each generation makes exactly one video. Reference images and characters are described in the prompt; only the source image of an image-to-video is sent to the provider."),
    "image": Spec(
        [STYLE, Field("aspect_ratio", "Aspect ratio", choices=list(ASPECT_RATIOS), default="1:1")],
        "What should the image show?", "e.g. A lone warrior standing at the gate of a ruined castle at sunrise, mist rolling over the hills",
        uses_characters=True, note="Each generation makes exactly one image."),
    "music": Spec(
        [MUSIC_GENRE, Field("mood", "Mood", choices=["Happy", "Sad", "Epic", "Romantic", "Suspense", "Peaceful", "Dark", "Energetic", "Emotional"]),
         Field("duration_seconds", "Duration", "duration", [str(d) for d in ALLOWED_DURATIONS], DEFAULT_DURATION,
               "Maximum 30 seconds"),
         Field("lyrics_asset_id", "Reference lyrics", "asset", asset_types=["LYRICS"],
               help="Optional. The lyrics inform the mood and theme of the music prompt; the provider does not sing them.")],
        "Describe your music (optional if you pick a genre or mood)", "e.g. A dark cinematic battle theme with deep drums, rising strings and an intense final section",
        prompt_required=False, note="Music is generated as an instrumental clip. Lyrics you select are used to inform the mood of the music prompt; they are not sung."),
    "voice": Spec(
        [Field("gender", "Gender", choices=["Male", "Female"]),
         Field("accent", "Accent", choices=["Indian English", "American", "British", "Hindi", "Telugu", "Other"]),
         Field("emotion", "Emotion", choices=["Neutral", "Happy", "Sad", "Angry", "Excited", "Fearful", "Calm"]),
         Field("language", "Language", choices=LANGUAGES, help="Optional. Inferred from the accent if left empty.")],
        "What should the voice say?", "Type the text to be spoken, e.g. Say this in a calm but emotional voice: The kingdom will rise again.",
        prompt_max=3000, note="Your text is spoken as written (no AI rewriting). Emotion and accent are matched to what the voice provider supports."),
    "lyrics": Spec([Field("language", "Language", choices=LANGUAGES, default="English")], "Describe the song or lyrics you want.",
                   "e.g. A hopeful song about leaving home to chase a dream"),
    "story": Spec([Field("genre", "Genre", choices=GENRES, allow_custom=True), LANGUAGE, STORY_LENGTH],
                  "What is your story about?", "e.g. A young engineer discovers a hidden city beneath Hyderabad and must escape before sunrise",
                  uses_characters=True),
    "script": Spec([Field("genre", "Genre", choices=GENRES, allow_custom=True), LANGUAGE,
                    Field("length", "Length", choices=["Short", "Medium", "Long"], help="Optional"),
                    Field("target_minutes", "Target duration", choices=["5 minutes", "10 minutes", "20 minutes", "30 minutes"],
                          help="Optional. How much screen time the script should cover."),
                    Field("story_asset_id", "Story to base the script on", "asset", asset_types=["STORY"], help="Optional. The story is supplied automatically.")],
                   "What should the script cover?", "e.g. The opening scene where the hero arrives in the ruined city", uses_characters=True),
    "face_replacement": Spec([], "Instructions (optional)", "e.g. Match the lighting of the original photo", prompt_required=False,
                             reference="required", reference_label="Source and face", prompt_max=2000, ui="face",
                             note="A creative editing tool: use only images and videos you have permission to use."),
    "ai_avatar": Spec([Field("avatar_name", "Avatar name", "text", help="Optional. Avatar configuration is coming in a later phase.")],
                      "What should the avatar say?", "Type the text your avatar will speak", prompt_max=3000,
                      note="Avatar appearance/voice configuration will be added when an avatar provider is connected."),
    "interactive_avatar": Spec([Field("avatar_name", "Avatar name", "text", help="Optional. Avatar selection is coming in a later phase."),
                                Field("persona", "Persona / conversation setup", "textarea", help="Optional. Who is the avatar, and how should it talk?")],
                               "What is the conversation about?", "e.g. A friendly museum guide who explains ancient history",
                               note="Avatar selection will be added when an avatar provider is connected."),
}

_DUR_TEXT = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(seconds?|secs?|minutes?|mins?)\b|\b(\d+)s\b", re.I)


def duration_in_text(text: str) -> float | None:
    """First duration the user mentions in free text, in seconds (used to enforce the cap even when typed in the prompt)."""
    m = _DUR_TEXT.search(text or "")
    if not m:
        return None
    if m.group(3):
        return float(m.group(3))
    n = float(m.group(1))
    return n * 60 if m.group(2).lower().startswith("min") else n


def _resolve_duration(label: str, explicit, prompt: str, warnings: list[str], strict: bool) -> int:
    """A duration the user typed in their prompt wins over the form field (the form always sends its default).
    In strict mode (submit) only the already-normalised option is considered."""
    requested = None
    if explicit not in (None, ""):
        try:
            requested = float(explicit)
        except (TypeError, ValueError):
            raise AppError("Duration must be a number of seconds.", 422, "validation_error")
    in_text = None if strict else duration_in_text(prompt)
    if not strict and in_text is None and label == "video" and _LONGER.search(prompt or ""):
        in_text = float(MAX_DURATION_SECONDS)
        warnings.append(f"Using the maximum video duration ({MAX_DURATION_SECONDS} seconds) because you asked for a longer video.")
    if in_text is not None:
        if requested is not None and in_text != requested and in_text <= MAX_DURATION_SECONDS and in_text in ALLOWED_DURATIONS:
            warnings.append(f"Using the {int(in_text)}-second duration from your prompt.")
        requested = in_text
    if requested is None:
        return DEFAULT_DURATION
    if requested > MAX_DURATION_SECONDS:
        msg = f"Maximum {label} duration is {MAX_DURATION_SECONDS} seconds."
        if strict:
            raise AppError(msg, 422, "validation_error")
        warnings.append(f"{msg} Duration adjusted to {MAX_DURATION_SECONDS} seconds.")
        return MAX_DURATION_SECONDS
    if requested not in ALLOWED_DURATIONS:
        nearest = min(ALLOWED_DURATIONS, key=lambda a: (abs(a - requested), -a))
        if strict:
            raise AppError(f"Duration must be one of {', '.join(map(str, ALLOWED_DURATIONS))} seconds.", 422, "validation_error")
        warnings.append(f"{label.capitalize()} duration adjusted to {nearest} seconds (allowed: 10, 20 or 30).")
        return nearest
    return int(requested)


def normalize_options(generator: str, raw: dict | None, prompt: str, *, strict: bool = False) -> tuple[dict, list[str]]:
    """Validate/clean options. strict=False (refine step) adjusts out-of-range durations and reports a warning;
    strict=True (submit step) rejects them, so nothing out-of-range can reach a provider."""
    spec, raw, warnings, clean = SPECS[generator], raw or {}, [], {}
    if not isinstance(raw, dict):
        raise AppError("Options must be an object.", 422, "validation_error")
    for f in spec.fields:
        value = raw.get(f.key)
        if f.kind == "duration":
            clean[f.key] = _resolve_duration(generator, value, prompt, warnings, strict)
            continue
        if value in (None, ""):
            if f.default not in (None, "") and f.kind == "select":
                clean[f.key] = f.default
            continue
        if not isinstance(value, str):
            raise AppError(f"Invalid value for {f.label}.", 422, "validation_error")
        value = value.strip()
        if f.kind == "select":
            match = next((c for c in f.choices if c.lower() == value.lower()), None)
            if not match:
                raise AppError(f"Invalid value for {f.label}.", 422, "validation_error")
            clean[f.key] = match
            if match == "Custom" and f.allow_custom:
                custom = str(raw.get(f"{f.key}_custom") or "").strip()[:80]
                if custom:
                    clean[f"{f.key}_custom"] = custom
        else:
            clean[f.key] = value[:1000 if f.kind == "textarea" else 120]
    for key in AUX_KEYS:
        v = raw.get(key)
        if v not in (None, ""):
            if not isinstance(v, str) or len(v) > 40:
                raise AppError("Invalid asset selection.", 422, "validation_error")
            clean[key] = v
    if raw.get("story_section") not in (None, ""):
        if raw["story_section"] not in STORY_SECTION_KEYS:
            raise AppError("Invalid story section.", 422, "validation_error")
        clean["story_section"] = raw["story_section"]
    if generator == "face_replacement":
        for key in ("source_asset_id", "face_asset_id"):
            v = raw.get(key)
            if v not in (None, ""):
                if not isinstance(v, str) or len(v) > 40:
                    raise AppError("Invalid file selection.", 422, "validation_error")
                clean[key] = v
        if raw.get("permission_confirmed") is not None:
            clean["permission_confirmed"] = bool(raw["permission_confirmed"]) is True and raw["permission_confirmed"] in (True, "true", "True")
    if raw.get("scene_number") not in (None, ""):
        try:
            clean["scene_number"] = max(1, min(int(raw["scene_number"]), 500))
        except (TypeError, ValueError):
            raise AppError("Invalid scene number.", 422, "validation_error")
    ids = raw.get("character_ids")
    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 20:
            raise AppError("Invalid character selection.", 422, "validation_error")
        clean["character_ids"] = ids
    return clean, warnings


def validate_prompt(generator: str, prompt: str, options: dict) -> str:
    spec = SPECS[generator]
    prompt = (prompt or "").strip()
    if len(prompt) > spec.prompt_max:
        raise AppError(f"Prompt is too long (maximum {spec.prompt_max} characters).", 422, "validation_error")
    optional_prompt = generator == "video" and options.get("method") == "Image to Video"
    if spec.prompt_required and not optional_prompt and len(prompt) < 3:
        raise AppError("Please describe what you want to create.", 422, "validation_error")
    if generator == "music" and not prompt and not (options.get("genre") or options.get("mood")):
        raise AppError("Describe your music or choose a genre or mood.", 422, "validation_error")
    return prompt
