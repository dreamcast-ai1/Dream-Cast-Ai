import shutil
import uuid
from pathlib import Path

from ..security import sanitize_filename
from .base import AREAS, Storage


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        for area in AREAS:
            (self.root / area).mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # blocks ../ traversal
            raise ValueError("Invalid storage key")
        return p

    def save(self, area: str, project_id: str, filename: str, data: bytes) -> str:
        if area not in AREAS:
            raise ValueError("Unknown storage area")
        safe = sanitize_filename(filename)
        key = f"{area}/{project_id}/{uuid.uuid4().hex[:12]}_{safe}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def save_file(self, area: str, project_id: str, filename: str, source_path: str) -> str:
        if area not in AREAS:
            raise ValueError("Unknown storage area")
        key = f"{area}/{project_id}/{uuid.uuid4().hex[:12]}_{sanitize_filename(filename)}"
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source_path, dest)
        return key

    def copy(self, key: str, area: str, project_id: str, filename: str) -> str:
        new_key = f"{area}/{project_id}/{uuid.uuid4().hex[:12]}_{sanitize_filename(filename)}"
        dest = self._path(new_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), dest)
        return new_key

    def local_path(self, key: str):
        return self._path(key)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)

    def usage_bytes(self, prefix: str = "") -> int:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return 0
        return sum(f.stat().st_size for f in base.rglob("*") if f.is_file())
