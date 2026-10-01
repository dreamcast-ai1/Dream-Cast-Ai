from abc import ABC, abstractmethod

# Top-level areas. Keys look like "<area>/<project_id>/<file>".
AREAS = ("projects", "uploads", "generated")


class Storage(ABC):
    """Swap this for S3/R2/Supabase Storage later; the rest of the app only uses keys."""

    @abstractmethod
    def save(self, area: str, project_id: str, filename: str, data: bytes) -> str:
        """Store bytes and return an opaque storage key."""

    @abstractmethod
    def save_file(self, area: str, project_id: str, filename: str, source_path: str) -> str:
        """Store an existing local file (moved or copied) without loading it into memory; returns the key."""

    @abstractmethod
    def copy(self, key: str, area: str, project_id: str, filename: str) -> str: ...

    def local_path(self, key: str):
        """Filesystem path for streaming, or None for storages that aren't local (they should offer signed URLs instead)."""
        return None

    # --- names used by the application design: upload / download / delete / exists / generate_signed_url
    def upload(self, area: str, project_id: str, filename: str, data: bytes) -> str:
        return self.save(area, project_id, filename, data)

    def download(self, key: str) -> bytes:
        return self.read(key)

    def generate_signed_url(self, key: str, expires_seconds: int = 300, filename: str | None = None) -> str | None:
        """A short-lived direct link to the object for storages that support it (object storage), else None. Only ever created
        by the backend after an ownership check; the storage credentials themselves never leave the server."""
        return None

    def ping(self) -> tuple[bool, str]:
        """(ok, short safe message) - used by the admin system page. Never includes credentials."""
        return True, "ok"

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def size(self, key: str) -> int: ...

    @abstractmethod
    def read_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes start..end inclusive (HTTP Range semantics)."""

    def iter_range(self, key: str, start: int, end: int, chunk: int = 1024 * 1024):
        """Streams start..end inclusive without loading the whole object (video playback and downloads)."""
        pos = start
        while pos <= end:
            stop = min(pos + chunk - 1, end)
            yield self.read_range(key, pos, stop)
            pos = stop + 1

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Delete everything under a key prefix (used when a project is removed)."""

    @abstractmethod
    def usage_bytes(self, prefix: str = "") -> int: ...
