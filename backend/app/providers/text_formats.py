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
{length_line} Do not add material beyond the request."""
    return TextJob(system, refined, _budget(words if not custom else 700, lang, cap), lang, words)


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
Camera: <visual direction, only where useful>
Lighting: <light and colour>
Sound: <ambient sound and effects>
Music cue: <only if relevant>
DIALOGUE
<CHARACTER NAME>:
"<line>"
Transition: <CUT TO / FADE OUT / ...>
End the script with the single line:
END
If a story is provided below, adapt it faithfully: same characters, events and ending."""
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


BUILDERS = {"story": build_story, "script": build_script, "lyrics": build_lyrics}
