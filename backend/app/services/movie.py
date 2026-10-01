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


def start_assembly(db: Session, user: User, project: Project) -> GenerationJob:
    """Validates, then queues the assembly job. Nothing is created (and nothing is charged) if a scene is missing."""
    state = readiness(db, project)
    if not state["can_assemble"]:
        raise AppError(" ".join(state["missing"]), 422, "scenes_missing")
    if active_job(db, project, user.id):
        raise AppError("Your movie is already being assembled.", 409, "assembly_running")
    ordered = scenes.list_scenes(db, project.id)
    job = GenerationJob(user_id=user.id, project_id=project.id, type=MOVIE_JOB, original_prompt=f"Assemble movie ({len(ordered)} scenes)",
                        options={"scene_ids": [s.id for s in ordered], "clips": [scenes.clip_asset(db, s).id for s in ordered],
                                 "numbers": [s.number for s in ordered]})
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


def _build_command(exe: str, paths: list[str], infos: list[media.MediaInfo], out: str) -> list[str]:
    """One ffmpeg call: every clip is scaled/padded to the first clip's size (so mixed sizes still join), then concatenated.
    If any clip has sound, silent clips get silent audio so the audio track stays in sync."""
    w, h = (infos[0].width // 2 * 2, infos[0].height // 2 * 2)
    any_audio = any(i.has_audio for i in infos)
    cmd, parts, labels, extra = [exe, "-y", "-hide_banner", "-loglevel", "error"], [], [], len(paths)
    for p in paths:
        cmd += ["-i", p]
    for n, info in enumerate(infos):
        parts.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                     f"setsar=1,fps={FPS},format=yuv420p[v{n}]")
        labels.append(f"[v{n}]")
        if any_audio:
            if info.has_audio:
                parts.append(f"[{n}:a]aresample=44100,aformat=channel_layouts=stereo[a{n}]")
            else:
                cmd += ["-f", "lavfi", "-t", f"{info.duration or 1:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                parts.append(f"[{extra}:a]aformat=channel_layouts=stereo[a{n}]")
                extra += 1
            labels.append(f"[a{n}]")
    parts.append("".join(labels) + f"concat=n={len(paths)}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else ""))
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if any_audio else ["-an"])
    return cmd + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]


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
        out = os.path.join(workdir, "movie.mp4")
        jobs.set_stage(db, job, "ASSEMBLING")
        total = sum(i.duration or 10 for i in infos)
        timeout = min(float(get_settings().job_timeout_seconds), max(120.0, total * 8))
        if not _run_ffmpeg(db, job, _build_command(exe, paths, infos, out), workdir, timeout):
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
