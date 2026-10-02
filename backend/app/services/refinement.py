"""PromptRefinementService: user prompt + options + project context -> refined, generator-specific prompt.
Exactly one LLM call per refinement. Without a configured LLM it uses a deterministic template (labelled as such)."""
import logging
import re
from dataclasses import dataclass

from ..providers import ErrorCode, ProviderError, prompt_refiner
from . import context as ctx_service
from .generation_schema import duration_in_text

log = logging.getLogger("dreamcast.refine")

RULES = ("You rewrite a user's creative request into one clear, production-ready generation prompt.\n"
         "Rules: preserve the user's intent exactly. Do NOT add major plot points, new characters, or a different genre. "
         "Do NOT change any requested duration, aspect ratio, language or style. Do not add unwanted content. "
         "You may improve clarity, structure and specific detail. Use the project context only where it is relevant. "
         "Output ONLY the refined prompt as plain text, no preface, no markdown, no quotes.")

EMPHASIS = {
    "video": ("Write the video prompt as these labelled lines, one per line: Subject:, Action:, Environment:, Lighting:, Camera:, Motion:, "
              "Mood:, Style:. Be concrete and visual. Keep the user's concept: do not invent major characters or plot events, and do not "
              "change the stated style, duration or aspect ratio. Use provided character, scene and reference details only where relevant.", 150),
    "image": ("Write one image prompt as comma-separated visual descriptors: subject, setting, composition, lighting, colours, mood and style. "
              "Be concrete and visual. Keep the user's concept; do not add characters or text overlays, and do not change the stated style "
              "or aspect ratio.", 90),
    "music": ("Write a compact music-generation prompt: comma-separated descriptors of genre, mood, instruments, energy, tempo and "
              "atmosphere, plus how the piece develops. Instrumental only: no lyrics, no artist names.", 60),
    "voice": ("The user's text is the script to be spoken: keep the spoken words verbatim. Add brief delivery direction "
              "(gender, accent, emotion, pace).", 160),
    "lyrics": ("Emphasise: theme, perspective, structure (verses/chorus), tone and imagery. Do not write the lyrics.", 90),
    "story": ("Emphasise: premise, protagonist, conflict, progression and ending, keeping it an outline. Do not write the story.", 110),
    "script": ("Emphasise: plot beats, characters, scene structure, dialogue style, visual direction and production detail. "
               "Do not write the script.", 120),
    "face_replacement": ("Emphasise: keeping the identity of the supplied face, matching lighting, angle and skin tone, "
                         "and natural blending.", 80),
    "ai_avatar": ("The user's text is what the avatar speaks: keep it verbatim. Add brief delivery direction.", 100),
    "interactive_avatar": ("Emphasise: the avatar's role, tone, knowledge boundaries and how the conversation should flow.", 120),
}


@dataclass
class RefinementResult:
    refined_prompt: str
    structured_prompt: dict
    metadata: dict


def _options_line(options: dict) -> str:
    parts = []
    for k, v in options.items():
        if k in ("character_ids",) or k.endswith("_custom"):
            continue
        label = k.replace("_", " ")
        if k == "duration_seconds":
            parts.append(f"duration: {v} seconds")
        else:
            custom = options.get(f"{k}_custom")
            parts.append(f"{label}: {custom if v == 'Custom' and custom else v}")
    return "; ".join(parts)


NOUNS = {"video": "video", "image": "image", "music": "music track", "voice": "voice-over", "lyrics": "set of song lyrics", "story": "story",
         "script": "screenplay", "face_replacement": "face replacement", "ai_avatar": "avatar video", "interactive_avatar": "avatar conversation"}
# Option key -> line label, in the order the lines are written.
OPTION_LINES = [("style", "Style"), ("mood", "Mood"), ("tone", "Tone"), ("emotion", "Delivery"), ("gender", "Voice"), ("accent", "Accent"),
                ("vocals_mode", "Vocals"), ("language", "Language"), ("length", "Length"), ("target_minutes", "Target length"),
                ("aspect_ratio", "Aspect ratio"), ("duration_seconds", "Duration"), ("method", "Method")]
GUIDANCE = {"image": "Composition: describe subject, setting, framing, lighting and colours clearly; show only what was asked.",
            "video": "Visual direction: describe subject, action, environment, camera movement, lighting and motion clearly; stay within the scene requested.",
            "music": "Musical direction: genre, mood, instruments, energy, tempo and how the piece develops.",
            "voice": "Delivery: speak the text exactly as written.",
            "story": "Structure: a clear beginning, middle, climax and ending; no unrequested plot elements.",
            "script": "Structure: standard scene headings, action, dialogue and camera notes; keep to the requested scenes.",
            "lyrics": "Structure: verses and a chorus that follow the theme and mood."}


def template_refine(generator: str, prompt: str, options: dict, context: dict, adjustments: list[str] | None = None) -> str:
    """Basic (non-AI) refinement. Deterministic: it only arranges what the user gave, in a clearer structure, and invents nothing."""
    def value(key):
        v = options.get(key)
        if v in (None, ""):
            return None
        custom = options.get(f"{key}_custom")
        return str(custom) if v == "Custom" and custom else str(v)

    genre, noun = value("genre"), NOUNS.get(generator, generator.replace("_", " "))
    subject = prompt.strip() or "the idea described by the options below"
    lines = [f"Create a {(genre + ' ') if genre else ''}{noun} about: {subject}"]
    for key, label in OPTION_LINES:
        v = value(key)
        if v:
            lines.append(f"{label}: {v} seconds" if key == "duration_seconds" else f"{label}: {v}")
    if (ids := options.get("character_ids")) and isinstance(ids, list):
        lines.append(f"Important elements: {len(ids)} selected character(s) from the project.")
    if ctx := ctx_service.context_text(context):
        lines.append("Project context:\n" + ctx)
    if guide := GUIDANCE.get(generator):
        lines.append(guide)
    if adjustments:
        lines.append(" ".join(adjustments))
    return "\n".join(lines)


_VOICE_WRAPPER = re.compile(r"^\s*(?:please\s+)?(?:say|read|speak|narrate|voice)(?:\s+this)?(?:\s+(?:in|with)\s+(?:an?\s+)?(?P<style>[^:\n\"“]{0,80}?))?\s*[:\-–]\s*(?P<text>.+)$",
                            re.I | re.S)
_EMOTION_WORDS = {"calm": "Calm", "peaceful": "Calm", "happy": "Happy", "cheerful": "Happy", "joyful": "Happy", "sad": "Sad", "sorrowful": "Sad",
                  "angry": "Angry", "furious": "Angry", "excited": "Excited", "energetic": "Excited", "fear": "Fearful", "scared": "Fearful",
                  "afraid": "Fearful", "neutral": "Neutral", "serious": "Serious", "stern": "Serious"}


def refine_voice(prompt: str, options: dict, extra_warnings: list[str]) -> RefinementResult:
    """Voice requests are refined locally (no LLM call): the spoken text must stay exactly as written, and the delivery
    instructions map to the emotion option."""
    text, opts, notes = prompt.strip(), dict(options), list(extra_warnings)
    m = _VOICE_WRAPPER.match(text)
    if m:
        text = m.group("text").strip().strip('"“”')
        style = (m.group("style") or "").lower()
        if not opts.get("emotion"):
            emotion = next((v for k, v in _EMOTION_WORDS.items() if k in style), None)
            if emotion:
                opts["emotion"] = emotion
                notes.append(f"Emotion set to {emotion} from your instructions.")
    notes.append("Voice requests skip AI rewriting: the text below is exactly what will be spoken.")
    structured = {"generator": "voice", "user_request": prompt, "options": opts, "context": {}, "constraints": "spoken text kept verbatim"}
    return RefinementResult(text, structured, {"method": "local", "label": "Basic refinement", "provider": None, "model": None, "warnings": notes, "options": opts,
                                               "context_used": {}})


def refine_face(prompt: str, options: dict, extra_warnings: list[str]) -> RefinementResult:
    """Face replacement is refined locally: the models take images, not creative prompts, so an LLM call would be wasted."""
    text = prompt.strip() or "Replace the face in the source with the supplied face image, matching lighting, angle and skin tone."
    structured = {"generator": "face_replacement", "user_request": prompt, "options": options, "context": {}, "constraints": "images only"}
    return RefinementResult(text, structured, {"method": "local", "label": "Basic refinement", "provider": None, "model": None, "options": options, "context_used": {},
                                               "warnings": [*extra_warnings, "Face replacement skips AI rewriting: your note is saved with the request."]})


def _enforce_duration(text: str, options: dict) -> str:
    """The validated duration is authoritative: if the text still mentions a different one (e.g. the user's
    original '45 seconds'), state the real duration explicitly so it can never be misread downstream."""
    d = options.get("duration_seconds")
    mentioned = duration_in_text(text)
    if d is not None and mentioned is not None and mentioned != d:
        return f"{text.rstrip()}\nFinal duration: {d} seconds."
    return text


LABELS = {"llm": "AI refined", "template": "Basic refinement", "local": "Basic refinement", "none": "Not refined"}


def refine(generator: str, prompt: str, options: dict, context: dict, *, extra_warnings: list[str] | None = None,
           use_llm: bool = True, basic: bool = True, fallback_on_error: bool = True, skip_reason: str | None = None) -> RefinementResult:
    """use_llm: AI may be used for this request. basic: the built-in structured template may be used. fallback_on_error: use the
    template if the AI call fails. skip_reason: why AI was not used (shown to the user). The result is always labelled honestly."""
    if generator == "voice":
        return refine_voice(prompt, options, list(extra_warnings or []))
    if generator == "face_replacement":
        return refine_face(prompt, options, list(extra_warnings or []))
    warnings = list(extra_warnings or [])
    structured = {"generator": generator, "user_request": prompt, "options": options,
                  "context": ctx_service.summarize(context), "constraints": EMPHASIS[generator][0]}
    provider = prompt_refiner()
    method, refined, model = "template", "", None
    if use_llm and provider.is_configured():
        emphasis, max_words = EMPHASIS[generator]
        system = f"{RULES}\n{emphasis}\nKeep it under {max_words} words."
        user = f"Generator: {generator}\nUser request: {prompt or '(none)'}\nOptions: {_options_line(options) or 'none'}"
        if generator == "video" and options.get("method") == "Image to Video":
            user += ("\nThe user supplied a source image (image-to-video). Describe only how the scene should move and change; "
                     "do not re-describe or alter what the image already shows.")
        if ctx := ctx_service.context_text(context):
            user += f"\nProject context:\n{ctx}"
        if warnings:
            user += "\nAuthoritative adjustments (override anything in the request): " + " ".join(warnings)
        try:
            refined = provider.complete(system, user, max_tokens=max_words * 3)
            method, model = "llm", provider.model
        except ProviderError as e:
            log.warning("LLM refinement failed (%s: %s)", e.code.value, e.detail)
            basic = basic and fallback_on_error
            what = "rejected its API key" if e.code == ErrorCode.AUTHENTICATION_ERROR else "is unavailable right now"
            warnings.append(f"The prompt-refinement AI {what}, so " + ("basic (non-AI) refinement was used instead." if basic else "your prompt was left as written."))
    elif not use_llm:
        warnings.append((skip_reason or "AI prompt refinement wasn't used for this request.") + (" Basic (non-AI) refinement was used instead." if basic else " Your prompt was left as written."))
    else:
        warnings.append("No prompt-refinement AI is configured, so " + ("basic (non-AI) refinement was used. See the README to add a free LLM key." if basic
                                                                      else "your prompt was left as written."))
    if not refined and basic:
        refined = template_refine(generator, prompt, options, context, extra_warnings)
        method = "template"
    elif not refined:
        refined, method = prompt.strip() or template_refine(generator, prompt, options, context, extra_warnings), "none"
    refined = _enforce_duration(refined, options)
    return RefinementResult(refined.strip(), structured, {
        "method": method, "label": LABELS[method], "provider": provider.name if method == "llm" else None, "model": model,
        "warnings": warnings, "options": options, "context_used": ctx_service.summarize(context)})
