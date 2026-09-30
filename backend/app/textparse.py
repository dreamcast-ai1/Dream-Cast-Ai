"""Deterministic parsing of generated text (no AI calls). The generation prompts require fixed headings, and this
module re-derives structure from the text itself, so it also stays correct after the user edits a script."""
import re

_TITLE = re.compile(r"^\s*(?:\*\*)?TITLE(?:\*\*)?\s*[:\-–]\s*(.+?)\s*$", re.I | re.M)
_SCENE = re.compile(r"^[ \t#*]*SCENE[ \t]+0*(\d{1,3})\b[ \t]*[:.\-–—|]*[ \t]*(.*?)[ \t*]*$", re.I | re.M)
_MARKDOWN = re.compile(r"(\*\*|__|^#{1,6}[ \t]+)", re.M)


def clean_generated(text: str) -> str:
    """Strip code fences and markdown emphasis that models add even when told not to."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", t).strip()
    return _MARKDOWN.sub("", t).replace("\r\n", "\n")


def parse_title(text: str) -> str | None:
    m = _TITLE.search(text or "")
    if not m:
        return None
    return m.group(1).strip().strip('"“”*_ ')[:120] or None


def parse_scenes(text: str) -> list[dict]:
    """[{number, heading, start, end}] where start/end are character offsets of each scene in `text`."""
    matches = list(_SCENE.finditer(text or ""))
    scenes = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        # The "SCENE LIST" summary near the top repeats headings; keep only real scene bodies (have content).
        if len(body.strip()) < 40 and i + 1 < len(matches):
            continue
        heading = (m.group(2) or "").strip() or next((l.strip() for l in body.splitlines() if l.strip()), "")[:100]
        scenes.append({"number": int(m.group(1)), "heading": heading[:120], "start": m.start(), "end": end})
    # de-duplicate by number, keeping the longest body
    best: dict[int, dict] = {}
    for s in scenes:
        if s["number"] not in best or (s["end"] - s["start"]) > (best[s["number"]]["end"] - best[s["number"]]["start"]):
            best[s["number"]] = s
    return sorted(best.values(), key=lambda s: s["start"])


def scene_text(text: str, scene: dict) -> str:
    return text[scene["start"]:scene["end"]].strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


_LABEL = re.compile(r"^\s*(Environment|Characters|Action|Camera|Lighting|Sound|Music cue|Transition)\s*:\s*(.*)$", re.I)
_SPEAKER = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{1,30}?)\s*(?:\([^)]*\))?\s*:\s*$")
_QUOTED = re.compile(r"^\s*[\"“](.+?)[\"”]\s*$")
STORY_SECTIONS = ("SETTING", "LOGLINE", "ACT 1", "ACT 2", "ACT 3", "ENDING")
_STORY_LABEL = re.compile(r"^\s*(TITLE|LOGLINE|GENRE|SETTING|MAIN CHARACTERS|STORY OUTLINE|ACT [123]|ENDING)\s*:?\s*(.*)$")


def parse_scene_fields(scene_body: str) -> dict:
    """Structured view of one scene (location, time, environment, characters, action, camera, lighting, sound, dialogue),
    derived from the labelled lines the script format requires. Missing labels simply stay empty."""
    lines = [l.rstrip() for l in scene_body.splitlines()]
    fields: dict[str, str] = {"environment": "", "characters": "", "action": "", "camera": "", "lighting": "", "sound": "", "music_cue": "", "transition": ""}
    heading, current, in_dialogue, speaker, dialogue = "", None, False, None, []
    for raw in lines[1:] if lines and lines[0].strip().upper().startswith("SCENE") else lines:
        line = raw.strip()
        if not line:
            continue
        if not heading and re.match(r"^(INT|EXT|INT\./EXT|I/E)[. ]", line, re.I):
            heading = line
            continue
        if line.upper() == "DIALOGUE":
            in_dialogue, current = True, None
            continue
        m = _LABEL.match(line)
        if m:
            in_dialogue = False
            current = m.group(1).lower().replace(" ", "_")
            fields[current] = m.group(2).strip()
            continue
        if in_dialogue:
            if (sm := _SPEAKER.match(line)):
                speaker = sm.group(1).strip().title()
            elif (qm := _QUOTED.match(line)) and speaker:
                dialogue.append({"speaker": speaker, "line": qm.group(1).strip()})
            continue
        if current:
            fields[current] = (fields[current] + " " + line).strip()
    location, _, time = heading.partition(" - ") if " - " in heading else heading.partition(" – ")
    return {"heading": heading, "location": location.strip(), "time": time.strip(), **fields, "dialogue": dialogue}


def compose_video_prompt(f: dict, limit: int = 700) -> str:
    """A plain-language draft video prompt from scene fields (no AI). The user reviews/edits it, then refinement runs once."""
    parts = [f["heading"] + "." if f["heading"] else "", f["environment"], f["action"]]
    for label, key in (("Lighting", "lighting"), ("Camera", "camera")):
        if f[key]:
            parts.append(f"{label}: {f[key]}")
    if f["music_cue"] or f["sound"]:
        parts.append("Mood: " + (f["music_cue"] or f["sound"]))
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_story_sections(text: str) -> list[dict]:
    """[{key, text}] for SETTING, LOGLINE, ACT 1-3 and ENDING of a generated story outline."""
    out: list[dict] = []
    cur = None
    for line in (text or "").splitlines():
        m = _STORY_LABEL.match(line)
        if m:
            cur = {"key": m.group(1), "text": m.group(2).strip()} if m.group(1) in STORY_SECTIONS else None
            if cur:
                out.append(cur)
            continue
        if cur and line.strip():
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
    return [s for s in out if s["text"]]
