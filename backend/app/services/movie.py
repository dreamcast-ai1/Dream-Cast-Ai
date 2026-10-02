"""Movie assembly: joins the scenes' video clips (in scene order) into one MP4 with the bundled FFmpeg.
It runs as a normal background job (type "movie"), so the request returns immediately and the user may leave the page.
Assembling only re-uses clips that already exist: it never calls an AI provider and never touches the video allowance."""
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import media
from ..config import get_settings
from ..errors import AppError
from ..generators import ACTIVE_STATUSES
from ..models import GeneratedAsset, GenerationJob, Project, User
from ..providers import ErrorCode, ProviderError
from ..storage import get_storage
from . import jobs, scenes

log = logging.getLogger("dreamcast.movie")
MOVIE_JOB = "movie"
FPS = 24


def readiness(db: Session, project: Project) -> dict:
    """Which scenes have a usable clip. `missing` carries the exact user-facing sentences."""
    items, missing = [], []
    for s in scenes.list_scenes(db, project.id):
        clip = scenes.clip_asset(db, s)
        items.append({"scene_id": s.id, "number": s.number, "title": s.title, "ready": clip is not None,
                      "duration_seconds": clip.duration_seconds if clip else None})
        if not clip:
            missing.append(f"Scene {s.number} has not been generated yet.")
    if not items:
        missing = ["Add at least one scene before assembling your movie."]
    return {"scenes": items, "missing": missing, "can_assemble": not missing,
            "total_seconds": round(sum(i["duration_seconds"] or 0 for i in items), 1)}


def active_job(db: Session, project: Project, user_id: str) -> GenerationJob | None:
    return db.scalars(select(GenerationJob).where(GenerationJob.project_id == project.id, GenerationJob.user_id == user_id,
                                                  GenerationJob.type == MOVIE_JOB, GenerationJob.status.in_(ACTIVE_STATUSES))).first()


def latest_movie(db: Session, project: Project) -> GeneratedAsset | None:
    for a in db.scalars(select(GeneratedAsset).where(GeneratedAsset.project_id == project.id, GeneratedAsset.type == "VIDEO")
                        .order_by(GeneratedAsset.created_at.desc())):
        if (a.meta or {}).get("movie") and a.file_path:
            return a
    return None


def start_assembly(db: Session, user: User, project: Project, narration: bool = True, music_asset_id: str | None = None) -> GenerationJob:
    """Validates, then queues the assembly job. Nothing is created (and nothing is charged) if a scene is missing."""
    state = readiness(db, project)
    if not state["can_assemble"]:
        raise AppError(" ".join(state["missing"]), 422, "scenes_missing")
    if active_job(db, project, user.id):
        raise AppError("Your movie is already being assembled.", 409, "assembly_running")
    music = None
    if music_asset_id:
        music = db.get(GeneratedAsset, music_asset_id)
        if not music or music.project_id != project.id or music.user_id != user.id or music.type != "MUSIC" or not music.file_path:
            raise AppError("The selected music couldn't be found in this project.", 422, "validation_error")
    ordered = scenes.list_scenes(db, project.id)
    narrations = [(n.id if (narration and (n := scenes.narration_asset(db, s))) else None) for s in ordered]
    job = GenerationJob(user_id=user.id, project_id=project.id, type=MOVIE_JOB, original_prompt=f"Assemble movie ({len(ordered)} scenes)",
                        options={"scene_ids": [s.id for s in ordered], "clips": [scenes.clip_asset(db, s).id for s in ordered],
                                 "numbers": [s.number for s in ordered], "narrations": narrations, "music": music.id if music else None})
    db.add(job)
    db.commit()
    return job


# ---------------------------------------------------------------------------------------------- worker side
def _fail(message: str, detail: str = "") -> ProviderError:
    return ProviderError(ErrorCode.GENERATION_FAILED, detail or message, message=message)


def _local_copy(asset: GeneratedAsset, workdir: str, index: int) -> str:
    storage = get_storage()
    local = storage.local_path(asset.file_path)
    if local:
        return str(local)
    path = os.path.join(workdir, f"in{index}.mp4")
    Path(path).write_bytes(storage.read(asset.file_path))
    return path


def _build_command(exe: str, paths: list[str], infos: list[media.MediaInfo], out: str, narrations: list[tuple[str, float] | None] | None = None) -> list[str]:
    """One ffmpeg call: every clip is scaled/padded to the first clip's size (so mixed sizes still join), then concatenated.
    A scene with narration plays it over the clip (the clip's own sound is lowered); if the narration is longer than the clip, the last frame is
    held until it ends. If any scene has sound, silent scenes get silent audio so the audio track stays in sync."""
    narrations = narrations or [None] * len(paths)
    w, h = (infos[0].width // 2 * 2, infos[0].height // 2 * 2)
    any_audio = any(i.has_audio for i in infos) or any(narrations)
    cmd, parts, labels = [exe, "-y", "-hide_banner", "-loglevel", "error"], [], []
    for p in paths:
        cmd += ["-i", p]
    nar_index: dict[int, int] = {}
    for n, nar in enumerate(narrations):
        if nar:
            nar_index[n] = len(paths) + len(nar_index)
            cmd += ["-i", nar[0]]
    extra = len(paths) + len(nar_index)
    for n, info in enumerate(infos):
        clip_d = info.duration or 1.0
        nar = narrations[n]
        total = max(clip_d, nar[1]) if nar else clip_d
        hold = f",tpad=stop_mode=clone:stop_duration={total - clip_d:.3f}" if total - clip_d > 0.05 else ""
        parts.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                     f"setsar=1,fps={FPS},format=yuv420p{hold}[v{n}]")
        labels.append(f"[v{n}]")
        if not any_audio:
            continue
        fmt = "aresample=44100,aformat=channel_layouts=stereo"
        if nar:
            parts.append(f"[{nar_index[n]}:a]{fmt},apad=whole_dur={total:.3f}[nar{n}]")
            if info.has_audio:
                parts.append(f"[{n}:a]{fmt},volume=0.3,apad=whole_dur={total:.3f}[ca{n}]")
                parts.append(f"[ca{n}][nar{n}]amix=inputs=2:duration=longest:dropout_transition=0,atrim=duration={total:.3f}[a{n}]")
            else:
                parts.append(f"[nar{n}]atrim=duration={total:.3f}[a{n}]")
        elif info.has_audio:
            parts.append(f"[{n}:a]{fmt}[a{n}]")
        else:
            cmd += ["-f", "lavfi", "-t", f"{clip_d:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            parts.append(f"[{extra}:a]aformat=channel_layouts=stereo[a{n}]")
            extra += 1
        labels.append(f"[a{n}]")
    parts.append("".join(labels) + f"concat=n={len(paths)}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else ""))
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if any_audio else ["-an"])
    return cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]


def _music_command(exe: str, movie_path: str, music_path: str, out: str, duration: float, has_audio: bool) -> list[str]:
    """Second pass: loops the soundtrack under the finished movie at low volume. The picture is copied, not re-encoded."""
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", movie_path, "-stream_loop", "-1", "-i", music_path]
    if has_audio:
        graph = "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.2[m];[0:a]aresample=44100,aformat=channel_layouts=stereo[v0];[v0][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
    else:
        graph = "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.5[a]"
    return cmd + ["-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-t", f"{duration:.3f}", "-movflags", "+faststart", out]


def _run_ffmpeg(db: Session, job: GenerationJob, cmd: list[str], workdir: str, timeout: float) -> bool:
    """Runs ffmpeg and watches for a cancel request. Returns False if cancelled."""
    log_path = os.path.join(workdir, "ffmpeg.log")
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=logf)
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            time.sleep(0.5)
            db.refresh(job)
            if job.cancel_requested:
                proc.terminate()
                proc.wait(10)
                return False
            if time.monotonic() > deadline:
                proc.kill()
                raise _fail("Assembling took too long and was stopped. Try again with fewer scenes.", "ffmpeg timeout")
    if proc.returncode != 0:
        tail = Path(log_path).read_text(errors="replace")[-600:]
        raise _fail("The movie couldn't be assembled. Check that every scene clip plays, then try again.", f"ffmpeg exit {proc.returncode}: {tail}")
    return True


def _load_narrations(db: Session, job: GenerationJob, clips: list, workdir: str, notes: list[str]) -> list[tuple[str, float] | None]:
    """(local path, seconds) of each scene's narration, or None. A missing or unreadable narration is skipped with a note, never fatal."""
    out: list[tuple[str, float] | None] = []
    ids = job.options.get("narrations") or [None] * len(clips)
    for i, ((number, _), asset_id) in enumerate(zip(clips, ids)):
        a = db.get(GeneratedAsset, asset_id) if asset_id else None
        if not a or not a.file_path or not get_storage().exists(a.file_path):
            if asset_id:
                notes.append(f"Scene {number}'s narration was no longer available and was skipped.")
            out.append(None)
            continue
        path = _local_copy(a, workdir, 1000 + i)
        if get_storage().local_path(a.file_path) is None:
            os.rename(path, path + ".mp3")
            path += ".mp3"
        dur = media.audio_duration(path)
        if not dur:
            notes.append(f"Scene {number}'s narration couldn't be read and was skipped.")
            out.append(None)
        else:
            out.append((path, dur))
    return out


def _add_music(db: Session, job: GenerationJob, exe: str, movie_path: str, workdir: str, timeout: float, notes: list[str]) -> str | None:
    """Optional soundtrack (job.options['music']). Returns the path of the final file, or None if the job was cancelled."""
    music_id = job.options.get("music")
    a = db.get(GeneratedAsset, music_id) if music_id else None
    if not a or not a.file_path or not get_storage().exists(a.file_path):
        if music_id:
            notes.append("The selected music was no longer available, so the movie has no soundtrack.")
        return movie_path
    info = media.probe(movie_path)
    music_path = _local_copy(a, workdir, 2000)
    if get_storage().local_path(a.file_path) is None:
        os.rename(music_path, music_path + f".{a.format or 'mp3'}")
        music_path += f".{a.format or 'mp3'}"
    final = os.path.join(workdir, "movie_music.mp4")
    if not _run_ffmpeg(db, job, _music_command(exe, movie_path, music_path, final, (info.duration if info else None) or 10, bool(info and info.has_audio)), workdir, timeout):
        return None
    return final


def run_assembly(db: Session, job: GenerationJob) -> None:
    exe = media.ffmpeg_exe()
    if not exe:
        raise _fail("Movie assembly is unavailable because FFmpeg isn't installed on the server.")
    project = db.get(Project, job.project_id)
    if not project:
        raise _fail("This project no longer exists.")
    if job.cancel_requested:
        return jobs.cancel_now(db, job)
    jobs.set_stage(db, job, "PREPARING")
    clips = []
    for number, asset_id in zip(job.options.get("numbers", []), job.options.get("clips", [])):
        a = db.get(GeneratedAsset, asset_id)
        if not a or not a.file_path or not get_storage().exists(a.file_path):
            raise _fail(f"Scene {number} has not been generated yet.")        # e.g. the clip was deleted after queuing
        clips.append((number, a))
    if not clips:
        raise _fail("Add at least one scene before assembling your movie.")
    workdir = tempfile.mkdtemp(prefix="dc_movie_")
    try:
        paths = [_local_copy(a, workdir, i) for i, (_, a) in enumerate(clips)]
        infos, notes = [], []
        for (number, _), p in zip(clips, paths):
            info = media.probe(p)
            if info is None or not info.width:
                raise _fail(f"The video for Scene {number} can't be read. Generate that scene again.")
            infos.append(info)
        for (number, _), info in list(zip(clips, infos))[1:]:
            if (info.width, info.height) != (infos[0].width, infos[0].height):
                notes.append(f"Scene {number} was resized to match Scene {clips[0][0]}.")
        narrations = _load_narrations(db, job, clips, workdir, notes)
        out = os.path.join(workdir, "movie.mp4")
        jobs.set_stage(db, job, "ASSEMBLING")
        total = sum(max(i.duration or 10, (nr[1] if nr else 0)) for i, nr in zip(infos, narrations))
        timeout = min(float(get_settings().job_timeout_seconds), max(120.0, total * 8))
        if not _run_ffmpeg(db, job, _build_command(exe, paths, infos, out, narrations), workdir, timeout):
            return jobs.cancel_now(db, job)
        out = _add_music(db, job, exe, out, workdir, timeout, notes)
        if out is None:
            return jobs.cancel_now(db, job)
        jobs.set_stage(db, job, "FINALIZING")
        asset = _store_movie(db, job, project, clips, out, notes)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    jobs.complete(db, job, {"asset_id": asset.id, "version": asset.version, "scene_count": len(clips), "notes": notes})


def _store_movie(db: Session, job: GenerationJob, project: Project, clips: list, out: str, notes: list[str]) -> GeneratedAsset:
    info = media.probe(out)
    if info is None:
        raise _fail("The assembled movie couldn't be read back. Please try again.", "output not decodable")
    storage = get_storage()
    thumb = os.path.join(os.path.dirname(out), "thumb.jpg")
    thumb_key = storage.save_file("generated", project.id, "thumbnail.jpg", thumb) if media.make_thumbnail(out, thumb) else None
    prev = latest_movie(db, project)
    lineage = (prev.lineage_id or prev.id) if prev else None
    asset = GeneratedAsset(project_id=project.id, user_id=job.user_id, job_id=job.id, type="VIDEO", title=f"{project.title} — Movie"[:200],
                           file_path=storage.save_file("generated", project.id, "movie.mp4", out), provider="ffmpeg", status="READY",
                           format="mp4", mime_type="video/mp4", duration_seconds=round(info.duration, 2) if info.duration else None,
                           version=(prev.version + 1) if prev else 1, lineage_id=lineage,
                           meta={"movie": True, "scene_ids": job.options.get("scene_ids", []), "clips": job.options.get("clips", []),
                                 "width": info.width, "height": info.height, "aspect_ratio": info.aspect_ratio, "thumbnail": thumb_key,
                                 "notes": notes, "simulated": False})
    db.add(asset)
    db.flush()
    if not asset.lineage_id:
        asset.lineage_id = asset.id
    db.commit()
    return asset
