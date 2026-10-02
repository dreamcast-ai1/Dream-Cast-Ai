"""Local media inspection with the bundled ffmpeg (no AI, no network): probe duration/resolution and make thumbnails.
Everything degrades gracefully: without ffmpeg the video is still stored and playable, it just has no thumbnail."""
import logging
import os
import re
import shutil
import tempfile
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


def audio_duration(path: str | Path) -> float | None:
    """Length of an audio file in seconds (None if FFmpeg is missing or the file can't be read)."""
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30).stderr
    except (subprocess.SubprocessError, OSError):
        return None
    d = _DURATION.search(out)
    return round(int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)), 2) if d else None


def fit_audio_duration(data: bytes, ext: str, target: float, *, allow_speedup: bool = False, max_speedup: float = 1.25) -> tuple[bytes, str, str, float | None, list[str]]:
    """Brings audio to `target` seconds with FFmpeg. Returns (bytes, extension, mime, measured seconds, notes).
    Shorter audio is padded with silence. Longer audio is sped up slightly if allow_speedup (speech, up to max_speedup, so it is never distorted much);
    otherwise it is trimmed with a short fade-out (music). Speech that still doesn't fit is kept whole rather than cut in the middle."""
    notes: list[str] = []
    exe = ffmpeg_exe()
    workdir = tempfile.mkdtemp(prefix="dc_fit_")
    src, dst = os.path.join(workdir, "in" + (ext if ext.startswith(".") else "." + ext)), os.path.join(workdir, "out.mp3")
    try:
        Path(src).write_bytes(data)
        natural = audio_duration(src) if exe else None
        if not exe or not natural:
            return data, ext, "audio/mpeg" if ext == ".mp3" else "audio/" + ext.lstrip("."), natural, ["The audio length couldn't be adjusted (FFmpeg couldn't read it), so it was kept as generated."]
        if abs(natural - target) < 0.25:
            return data, ext, "audio/mpeg" if ext == ".mp3" else "audio/" + ext.lstrip("."), natural, notes
        if natural < target:
            graph = f"apad=whole_dur={target:.3f}"
            notes.append(f"Padded with silence from {natural:.1f} s to {target:.0f} s.")
        elif allow_speedup and natural / target <= max_speedup:
            graph = f"atempo={natural / target:.4f},apad=whole_dur={target:.3f},atrim=duration={target:.3f}"
            notes.append(f"Sped up slightly from {natural:.1f} s to fit {target:.0f} s.")
        elif allow_speedup:
            notes.append(f"The speech lasts {natural:.1f} s, longer than the {target:.0f} s requested; it was kept in full instead of being cut off.")
            return data, ext, "audio/mpeg" if ext == ".mp3" else "audio/" + ext.lstrip("."), natural, notes
        else:
            fade = min(0.6, target / 4)
            graph = f"atrim=duration={target:.3f},afade=t=out:st={target - fade:.3f}:d={fade:.3f}"
            notes.append(f"Trimmed from {natural:.1f} s to {target:.0f} s.")
        r = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-af", graph, "-c:a", "libmp3lame", "-q:a", "4", dst], capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(dst):
            return data, ext, "audio/mpeg" if ext == ".mp3" else "audio/" + ext.lstrip("."), natural, ["The audio length couldn't be adjusted, so it was kept as generated."]
        out = Path(dst).read_bytes()
        return out, ".mp3", "audio/mpeg", audio_duration(dst), notes
    except (subprocess.SubprocessError, OSError):
        return data, ext, "audio/mpeg" if ext == ".mp3" else "audio/" + ext.lstrip("."), None, ["The audio length couldn't be adjusted, so it was kept as generated."]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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
