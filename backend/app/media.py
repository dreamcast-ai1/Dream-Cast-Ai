"""Local media inspection with the bundled ffmpeg (no AI, no network): probe duration/resolution and make thumbnails.
Everything degrades gracefully: without ffmpeg the video is still stored and playable, it just has no thumbnail."""
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("dreamcast.media")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def ffmpeg_exe() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - optional dependency
        return None


@dataclass
class MediaInfo:
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool = False

    @property
    def aspect_ratio(self) -> str | None:
        if not (self.width and self.height):
            return None
        for label, r in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0), ("4:3", 4 / 3), ("3:4", 3 / 4)):
            if abs(self.width / self.height - r) < 0.03:
                return label
        return f"{self.width}:{self.height}"


def probe(path: str | Path) -> MediaInfo | None:
    """Reads duration and resolution. Returns None if ffmpeg is missing or the file isn't decodable media."""
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30).stderr
    except (subprocess.SubprocessError, OSError):
        return None
    v = _VIDEO.search(out)
    if not v:
        return None
    d = _DURATION.search(out)
    duration = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return MediaInfo(duration, int(v.group(1)), int(v.group(2)), has_audio=bool(re.search(r"Stream #\S+.*?Audio:", out)))


def make_thumbnail(src: str | Path, dest: str | Path, width: int = 480) -> bool:
    """One JPEG frame (video) or a downscaled copy (image). Returns False if it can't be made."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    for seek in ("0.5", "0"):
        try:
            r = subprocess.run([exe, "-y", "-ss", seek, "-i", str(src), "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", str(dest)],
                               capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            return False
        if r.returncode == 0 and Path(dest).exists() and Path(dest).stat().st_size > 0:
            return True
    log.warning("thumbnail generation failed for %s", Path(src).name)
    return False
