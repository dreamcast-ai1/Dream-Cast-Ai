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

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Delete everything under a key prefix (used when a project is removed)."""

    @abstractmethod
    def usage_bytes(self, prefix: str = "") -> int: ...
