"""Prompt templates for text generation. The format instructions live here (and only here) so every text provider
produces the same structure. Output amounts are tied to what the user asked for; nothing is padded."""
from dataclasses import dataclass

COPYRIGHT = ("Write ORIGINAL content only. Never reproduce or closely imitate existing copyrighted stories, screenplays, songs or "
             "lyrics. If the request names an existing work, take only its general genre and mood and invent new characters, events and words.")
LANGUAGE_NOTES = {"English": "English", "Hindi": "Hindi (Devanagari script)", "Telugu": "Telugu (Telugu script)"}
TOKENS_PER_WORD = {"English": 1.6, "Hindi": 4.0, "Telugu": 7.0}   # rough tokenizer cost; used only to size the output cap
STORY_WORDS = {"Short": 300, "Medium": 600, "Long": 1000}
SCRIPT_PROFILES = {  # (scenes, total words) by target duration in minutes
    5: (5, 1100), 10: (8, 1800), 20: (12, 2600), 30: (15, 3200)}
SCRIPT_BY_LENGTH = {"Short": (5, 1100), "Medium": (8, 1800), "Long": (12, 2600)}
LYRICS_WORDS = 220


@dataclass
class TextJob:
    system: str
    user: str
    max_tokens: int
    language: str
    words: int


def _language(options: dict) -> str:
    return options.get("language") if options.get("language") in LANGUAGE_NOTES else "English"


def _lang_rule(lang: str) -> str:
    return (f"Write the content in {LANGUAGE_NOTES[lang]}. Keep the structural labels (TITLE, ACT 1, SCENE, INT./EXT., CAMERA, "
            f"[VERSE 1] ...) in English exactly as shown." if lang != "English" else "Write in English.")


def _budget(words: int, lang: str, cap: int) -> int:
    return int(min(cap, words * TOKENS_PER_WORD[lang] * 1.5 + 300))


def _tone_line(options: dict) -> str:
    tone = (options.get("tone") or "").strip()
    return f" Tone: {tone}." if tone else ""


def build_story(refined: str, options: dict, ctx: dict, cap: int) -> TextJob:
    lang = _language(options)
    custom = (options.get("length_custom") or "").strip()
    words = STORY_WORDS.get(options.get("length") or "Medium", 600)
    length_line = f"Length: {custom} (keep it a compact outline)." if custom else f"Length: about {words} words in total."
    system = f"""You are a story developer. {COPYRIGHT}
{_lang_rule(lang)}
Write a story OUTLINE (premise, characters, conflict, progression, ending) - not a screenplay, no scene-by-scene dialogue.
Plain text only (no markdown symbols). Use exactly this structure:
TITLE: <title>
LOGLINE: <one sentence>
GENRE: <genre>
SETTING: <time and place, 1-2 sentences>
MAIN CHARACTERS:
- <Name>: <who they are and what they want>
STORY OUTLINE
ACT 1: <summary>
ACT 2: <summary>
ACT 3: <summary>
ENDING: <how it resolves>
{length_line}{_tone_line(options)} Do not add material beyond the request."""
    return TextJob(system, refined, _budget(words if not custom else 700, lang, cap), lang, words)


def _style_line(options: dict) -> str:
    style = (options.get("style") or "").strip()
    tone = (options.get("tone") or "").strip()
    return (f"\nScript style: {style}." if style else "") + (f"\nTone: {tone}." if tone else "")


def build_script(refined: str, options: dict, ctx: dict, cap: int) -> TextJob:
    lang = _language(options)
    minutes = int(str(options.get("target_minutes") or "0").split()[0] or 0)
    scenes, words = SCRIPT_PROFILES.get(minutes) or SCRIPT_BY_LENGTH.get(options.get("length") or "Medium", (8, 1800))
    duration_line = (f"The screenplay covers a story of about {minutes} minutes of screen time, condensed into {scenes} scenes."
                     if minutes else f"Use about {scenes} scenes.")
    system = f"""You are a screenwriter. {COPYRIGHT}
{_lang_rule(lang)}
Write a detailed, production-ready screenplay as readable plain text (no markdown symbols). {duration_line}
Total length about {words} words: give every scene concrete, useful direction, but no filler and no unnecessary camera jargon.
Use exactly this structure:
TITLE: <title>
GENRE: <genre>
LOGLINE: <one sentence>
CHARACTERS:
- <NAME>: <short description>
CHARACTER NOTES:
- <NAME>: <look, manner, arc - one line>
SCENE LIST:
1. <INT./EXT. LOCATION - TIME: one-line summary>
(one line per scene)
Then each scene, numbered with two digits and separated by a blank line:
SCENE 01
INT. or EXT. LOCATION - TIME
Environment: <what the space looks like>
Characters: <who is present, and how they enter>
Action:
<present-tense action lines>
Narration: <voice-over narration, only if the scene truly needs it; otherwise leave this line out>
Camera: <visual direction, only where useful>
Lighting: <light and colour>
Sound: <ambient sound and sound effects (SFX)>
Music cue: <only if relevant>
DIALOGUE
<CHARACTER NAME>:
"<line>"
Transition: <CUT TO / FADE OUT / ...>
End the script with the single line:
END
If a story is provided below, adapt it faithfully: keep the same characters, events, order of events and ending. Do not add, remove or change major
plot points or characters unless the instructions explicitly ask for that. Add only what a screenplay needs: locations, times of day, visual action and dialogue
that follow from the story.{_style_line(options)}"""
    user = refined
    if story := ctx.get("story_full"):
        user += f"\n\nSTORY TO ADAPT:\n{story}"
    elif story := ctx.get("story"):
        user += f"\n\nSTORY CONTEXT:\n{story}"
    if chars := ctx.get("characters"):
        user += "\n\nEXISTING CHARACTERS (keep consistent):\n" + "\n".join(
            f"- {c['name']}: " + "; ".join(str(v) for k, v in c.items() if k not in ("name",)) for c in chars)
    return TextJob(system, user, _budget(words, lang, cap), lang, words)


def build_lyrics(refined: str, options: dict, ctx: dict, cap: int) -> TextJob:
    lang = _language(options)
    system = f"""You are a songwriter. {COPYRIGHT}
{_lang_rule(lang)}
Write original song lyrics as plain text (no markdown symbols), about {LYRICS_WORDS} words. Structure:
TITLE: <song title>
[VERSE 1]
...
[PRE-CHORUS]
...
[CHORUS]
...
[VERSE 2]
...
[BRIDGE]
...
[FINAL CHORUS]
...
Use only the sections that suit the song (omit others). Lines should scan and rhyme naturally in the chosen language."""
    return TextJob(system, refined, _budget(LYRICS_WORDS, lang, cap), lang, LYRICS_WORDS)


STUDIO_STORY_WORDS = {"Short": 400, "Medium": 800, "Long": 1400}
SCRIPT_FORMATS = {
    "Screenplay": "Standard screenplay: a balance of visual action and dialogue.",
    "Narrated video (voice-over)": "Tell the story mainly through voice-over narration with short visual scene descriptions and little dialogue.",
    "Dialogue-led": "Drive the story mainly through dialogue between the characters, with brief action lines.",
    "Visual (minimal dialogue)": "Tell the story visually with minimal or no dialogue; use narration only where essential.",
}


def _json_lang_rule(lang: str) -> str:
    return f"Write every text value in {LANGUAGE_NOTES[lang]}. Keep the JSON keys in English exactly as listed."


def build_story_json(prompt: str, options: dict, ctx: dict, cap: int) -> TextJob:
    """A complete story as one JSON object (used by the Story Generator). The registered 'story' generator above stays a plain-text outline builder."""
    lang = _language(options)
    words = STUDIO_STORY_WORDS.get(options.get("length") or "Medium", 800)
    genre = (options.get("genre") or "").strip()
    system = f"""You are a fiction writer. {COPYRIGHT}
{_json_lang_rule(lang)}
Write a COMPLETE short story (clear beginning, middle, climax and ending) about {words} words.{f" Genre: {genre}." if genre else ""}{_tone_line(options)}
Stay with what the user asked for; do not add unrelated material. No markdown symbols inside the text values.
Return ONLY one JSON object (no commentary, no code fences) with exactly these keys:
{{"title": string, "logline": string (one-sentence premise), "setting": string (time and place, 1-2 sentences),
"characters": [{{"name": string, "description": string (who they are and what they want)}}],
"beginning": string (2-4 sentence summary of the opening), "middle": string (summary), "climax": string (summary), "ending": string (summary),
"full_story": string (the complete story as prose; separate paragraphs with a blank line)}}"""
    return TextJob(system, prompt, int(min(cap, _budget(words, lang, cap) * 1.25)), lang, words)


def build_script_json(instructions: str, options: dict, ctx: dict, cap: int) -> TextJob:
    """A screenplay as one JSON object (used by Story to Script). It must follow the story: no major plot changes unless the notes ask for them."""
    lang = _language(options)
    minutes = int(str(options.get("target_minutes") or "0").split()[0] or 0)
    scenes, words = SCRIPT_PROFILES.get(minutes) or SCRIPT_BY_LENGTH["Medium"]
    duration_line = (f"The screenplay covers about {minutes} minutes of screen time in about {scenes} scenes." if minutes else f"Use about {scenes} scenes.")
    fmt = SCRIPT_FORMATS.get(options.get("script_format") or "Screenplay", SCRIPT_FORMATS["Screenplay"])
    system = f"""You are a screenwriter. {COPYRIGHT}
{_json_lang_rule(lang)}
Convert the story below into a production-ready screenplay. {duration_line} Total about {words} words. Format: {fmt}
Adapt the story faithfully: keep the same characters, events, order of events and ending. Do not add, remove or change major plot points or characters
unless the notes explicitly ask for it. Add only what a screenplay needs: locations, times of day, visual action and dialogue that follow from the story.{_style_line(options)}
No markdown symbols inside the text values.
Return ONLY one JSON object (no commentary, no code fences) with exactly these keys:
{{"title": string, "logline": string, "characters": [{{"name": string, "description": string}}],
"scenes": [{{"number": integer starting at 1, "heading": string (a slug line such as "EXT. HARBOUR - DUSK"), "location": string, "time": string (day/night/dawn... or ""),
"action": string (present-tense visual description of what we see), "narration": string (voice-over, or ""),
"dialogue": [{{"speaker": string (CHARACTER NAME), "line": string}}] (empty list if none),
"sound": string (ambient sound and sound effects, or ""), "camera": string (only if useful, or ""), "transition": string ("CUT TO", "FADE OUT" ... or ""),
"estimated_seconds": integer (realistic screen time of this scene)}}]}}"""
    user = (instructions or "Convert this story into a screenplay.") + f"\n\nSTORY TO ADAPT:\n{ctx.get('story_full', '')}"
    return TextJob(system, user, int(min(cap, _budget(words, lang, cap) * 1.35)), lang, words)


BUILDERS = {"story": build_story, "script": build_script, "lyrics": build_lyrics}
