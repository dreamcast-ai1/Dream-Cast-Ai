from sqlalchemy.orm import Session

from ..models import GeneratedAsset
from ..schemas import AssetDetail, AssetOut, AssetVersion
from . import assets


def asset_out(a: GeneratedAsset) -> AssetOut:
    out = AssetOut.model_validate(a)
    out.has_file = bool(a.file_path)
    out.url = f"/api/files/asset/{a.id}" if a.file_path else None
    out.thumbnail_url = f"/api/files/thumbnail/{a.id}" if (a.meta or {}).get("thumbnail") else None
    out.text_preview = (a.text_content or "")[:320].strip() or None
    out.meta = {k: v for k, v in (a.meta or {}).items() if k != "scenes"}
    return out


def asset_detail(db: Session, a: GeneratedAsset) -> AssetDetail:
    d = AssetDetail(**asset_out(a).model_dump(), text_content=a.text_content)
    d.meta = a.meta or {}
    d.versions = [AssetVersion(id=v.id, version=v.version, title=v.title, created_at=v.created_at) for v in assets.versions(db, a)]
    return d
