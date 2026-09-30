from functools import lru_cache

from ..config import get_settings
from .base import Storage
from .local import LocalStorage


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "local":
        return LocalStorage(s.storage_dir)
    raise RuntimeError(f"Unsupported STORAGE_BACKEND '{s.storage_backend}'")
