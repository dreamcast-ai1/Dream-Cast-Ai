"""S3-compatible object storage (production media). Runs against moto's in-memory fake S3: the real boto3 code paths, no network, no credentials.
MOCK TEST ONLY: a real bucket (R2/S3/B2) has not been exercised."""
import io
import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import GeneratedAsset
from app.storage import get_storage
from app.storage.s3 import S3Storage
from app.streaming import parse_range

from .conftest import PNG
from .helpers import make_project, mp4_bytes, run_all, upload_ref
from .test_movie import give_clip, movie_assets, new_scene, state

BUCKET = "dc-test-bucket"


def s3_settings(**kw) -> Settings:
    return Settings(_env_file=None, storage_backend="s3", s3_bucket=BUCKET, s3_region="us-east-1", s3_access_key_id="test-access-key",
                    s3_secret_access_key="test-secret-key", **kw)


@pytest.fixture
def fake_s3():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def store(fake_s3):
    return S3Storage(s3_settings())


@pytest.fixture
def s3_app(fake_s3, monkeypatch):
    """The whole application running on object storage instead of the local folder."""
    s = get_settings()
    for k, v in dict(storage_backend="s3", s3_bucket=BUCKET, s3_region="us-east-1", s3_endpoint_url="", s3_access_key_id="test-access-key",
                     s3_secret_access_key="test-secret-key", s3_prefix="").items():
        monkeypatch.setattr(s, k, v)
    get_storage.cache_clear()
    yield fake_s3
    get_storage.cache_clear()


# ------------------------------------------------------------------ the storage contract
def test_save_read_exists_size_and_delete(store, fake_s3):
    key = store.save("generated", "proj1", "My Video (1).mp4", b"0123456789")
    assert key.startswith("generated/proj1/") and key.endswith("_My_Video_1_.mp4") and ".." not in key
    assert store.exists(key) and store.read(key) == b"0123456789" and store.size(key) == 10
    assert store.upload("uploads", "p", "a.png", b"x") and store.download(key) == b"0123456789"        # the design's upload/download names
    assert fake_s3.head_object(Bucket=BUCKET, Key=key)["ContentType"] == "video/mp4"
    store.delete(key)
    assert not store.exists(key)
    with pytest.raises(FileNotFoundError):
        store.read(key)


def test_ranges_and_streaming(store):
    key = store.save("generated", "p", "v.bin", bytes(range(100)))
    assert store.read_range(key, 10, 19) == bytes(range(10, 20)) and store.read_range(key, 90, 150) == bytes(range(90, 100))
    assert b"".join(store.iter_range(key, 5, 94, chunk=7)) == bytes(range(5, 95))


def test_save_file_uploads_then_removes_the_temp_file(store, tmp_path):
    f = tmp_path / "big.mp4"
    f.write_bytes(b"v" * 5000)
    key = store.save_file("generated", "p", "movie.mp4", str(f))
    assert store.read(key) == b"v" * 5000 and not f.exists()


def test_copy_prefix_delete_and_usage(store):
    a = store.save("generated", "projA", "a.png", b"A" * 10)
    b = store.copy(a, "generated", "projA", "copy.png")
    other = store.save("generated", "projB", "b.png", b"B" * 5)
    assert store.read(b) == b"A" * 10 and store.usage_bytes("generated/projA") == 20 and store.usage_bytes() == 25
    store.delete_prefix("generated/projA")
    assert not store.exists(a) and not store.exists(b) and store.exists(other) and store.usage_bytes() == 5


def test_keys_cannot_escape_or_use_unknown_areas(store):
    for bad in ("../secret", "/etc/passwd", "generated/../../x", ""):
        with pytest.raises(ValueError):
            store.exists(bad)
    with pytest.raises(ValueError):
        store.save("elsewhere", "p", "a.png", b"x")


def test_prefix_keeps_objects_under_a_folder_but_keys_stay_the_same(fake_s3):
    st = S3Storage(s3_settings(s3_prefix="dreamcast/prod"))
    key = st.save("generated", "p", "a.png", b"x")
    assert not key.startswith("dreamcast") and st.exists(key)
    assert [o["Key"] for o in fake_s3.list_objects_v2(Bucket=BUCKET)["Contents"]] == [f"dreamcast/prod/{key}"]


def test_signed_url_is_short_lived_scoped_and_carries_no_credentials_in_the_clear(store):
    key = store.save("generated", "p", "movie.mp4", b"x")
    url = store.generate_signed_url(key, expires_seconds=120, filename="My Movie.mp4")
    assert url.startswith("https://") and BUCKET in url and key in url and "X-Amz-Signature=" in url and "X-Amz-Expires=120" in url
    assert "response-content-disposition=attachment" in url and "test-secret-key" not in url
    assert S3Storage(s3_settings()).signed_seconds == 300


def test_ping_reports_problems_without_leaking_credentials(store, fake_s3):
    assert store.ping()[0] is True
    bad = S3Storage(s3_settings())
    bad.bucket = "missing-bucket"
    ok, message = bad.ping()
    assert ok is False and "bucket not found" in message and "test-secret-key" not in message


def test_configuration_rules():
    with pytest.raises(ValueError):
        Settings(_env_file=None, storage_backend="s3")                                  # S3 selected but not configured: fail fast
    with pytest.raises(ValueError):
        Settings(_env_file=None, storage_backend="ftp")
    with pytest.raises(ValueError):
        Settings(_env_file=None, storage_backend="s3", s3_bucket="REPLACE_WITH_BUCKET", s3_access_key_id="REPLACE_WITH_ID", s3_secret_access_key="REPLACE_WITH_SECRET")
    assert Settings(_env_file=None).storage_backend == "local"


def test_range_header_parsing():
    assert parse_range(None, 100) is None and parse_range("bytes=0-9", 100) == (0, 9) and parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99) and parse_range("bytes=50-500", 100) == (50, 99) and parse_range("garbage", 100) is None
    with pytest.raises(Exception) as e:
        parse_range("bytes=200-300", 100)
    assert getattr(e.value, "status_code", None) == 416


# ------------------------------------------------------------------ the whole app on object storage
def test_uploaded_reference_is_stored_in_the_bucket_and_served_only_to_its_owner(client, make_user, s3_app):
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    pid = make_project(client, ha)
    ref = upload_ref(client, ha, pid, PNG)
    keys = [o["Key"] for o in s3_app.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert len(keys) == 1 and keys[0].startswith(f"uploads/{pid}/")
    r = client.get(ref["url"], headers=ha)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get(ref["url"], headers=hb).status_code == 404 and client.get(ref["url"]).status_code == 401      # IDOR + anonymous


def test_video_streams_with_range_requests_and_downloads_via_a_signed_link(client, make_user, s3_app):
    h, _ = make_user()
    pid = make_project(client, h)
    sid = new_scene(client, h, pid)["id"]
    data = mp4_bytes(3)
    asset_id = give_clip(sid, data)
    url = client.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": asset_id}).json()["url"]
    full = client.get(url)
    assert full.status_code == 200 and full.content == data and full.headers["accept-ranges"] == "bytes" and full.headers["content-type"] == "video/mp4"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == data[:100] and part.headers["content-range"] == f"bytes 0-99/{len(data)}"
    tail = client.get(url, headers={"Range": f"bytes={len(data) - 50}-"})
    assert tail.status_code == 206 and tail.content == data[-50:]
    assert client.get(url, headers={"Range": f"bytes={len(data) + 10}-"}).status_code == 416
    dl = client.get(url + "?download=1", follow_redirects=False)
    assert dl.status_code == 302 and "X-Amz-Signature=" in dl.headers["location"] and "attachment" in dl.headers["location"] and dl.headers["cache-control"] == "no-store"
    api_dl = client.get(f"/api/assets/{asset_id}/download", headers=h)
    assert api_dl.status_code == 200 and api_dl.content == data and "attachment" in api_dl.headers["content-disposition"]


def test_media_of_one_user_is_not_reachable_by_another_even_with_the_object_id(client, make_user, s3_app):
    ha, _ = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    pid = make_project(client, ha)
    asset_id = give_clip(new_scene(client, ha, pid)["id"])
    url_a = client.post("/api/media/stream-url", headers=ha, json={"kind": "asset", "id": asset_id}).json()["url"]
    assert client.post("/api/media/stream-url", headers=hb, json={"kind": "asset", "id": asset_id}).status_code == 404
    for path in (f"/api/files/asset/{asset_id}", f"/api/assets/{asset_id}", f"/api/assets/{asset_id}/download"):
        assert client.get(path, headers=hb).status_code == 404
    with SessionLocal() as db:
        key = db.get(GeneratedAsset, asset_id).file_path
    assert client.get(f"/api/files/asset/{key}", headers=ha).status_code == 404         # a storage key is not an id
    assert client.get(url_a).status_code == 200                                         # the owner's signed link works...
    tampered = url_a[:-3] + ("abc" if not url_a.endswith("abc") else "xyz")
    assert client.get(tampered).status_code == 401                                      # ...a modified one does not


def test_missing_object_gives_a_clean_error_not_a_crash(client, make_user, s3_app):
    h, _ = make_user()
    pid = make_project(client, h)
    asset_id = give_clip(new_scene(client, h, pid)["id"])
    with SessionLocal() as db:
        key = db.get(GeneratedAsset, asset_id).file_path
    s3_app.delete_object(Bucket=BUCKET, Key=key)                                        # the object disappears from the bucket
    r = client.get(f"/api/assets/{asset_id}/download", headers=h)
    assert r.status_code == 404 and r.json()["error"]["message"] and "Traceback" not in r.text and BUCKET not in r.text
    assert state(client, h, pid)["missing"] == ["Scene 1 has not been generated yet."]


def test_movie_assembly_runs_on_object_storage(client, make_user, s3_app):
    h, _ = make_user()
    pid = make_project(client, h)
    for _ in range(2):
        give_clip(new_scene(client, h, pid)["id"])
    client.post(f"/api/projects/{pid}/movie/assemble", headers=h)
    run_all()
    [m] = movie_assets(client, h, pid)
    assert m["has_file"] and m["thumbnail_url"] and m["duration_seconds"] > 3
    url = client.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": m["id"]}).json()["url"]
    assert client.get(url, headers={"Range": "bytes=0-7"}).status_code == 206 and client.get(url).content[4:8] == b"ftyp"
    assert client.get(m["thumbnail_url"], headers=h).content[:3] == b"\xff\xd8\xff"
    keys = [o["Key"] for o in s3_app.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert any(k.endswith("movie.mp4") for k in keys) and any(k.endswith("thumbnail.jpg") for k in keys)
    assert client.delete(f"/api/projects/{pid}", headers=h).status_code == 204          # deleting a project removes its objects too
    assert [o for o in s3_app.list_objects_v2(Bucket=BUCKET).get("Contents", [])] == []


def test_duplicate_and_delete_asset_on_object_storage(client, make_user, s3_app):
    h, _ = make_user()
    pid = make_project(client, h)
    asset_id = give_clip(new_scene(client, h, pid)["id"])
    copy = client.post(f"/api/assets/{asset_id}/duplicate", headers=h).json()
    assert copy["has_file"] and len(s3_app.list_objects_v2(Bucket=BUCKET)["Contents"]) == 2
    assert client.delete(f"/api/assets/{copy['id']}", headers=h).status_code == 204
    assert len(s3_app.list_objects_v2(Bucket=BUCKET)["Contents"]) == 1


def test_admin_system_page_reports_object_storage_without_secrets(client, make_user, s3_app, monkeypatch):
    ah, _ = make_user("boss@example.com")
    items = {i["id"]: i for i in client.get("/api/admin/system", headers=ah).json()["items"]}
    r = client.get("/api/admin/system", headers=ah)
    assert items["storage"]["ok"] and "object storage" in items["storage"]["detail"] and "permanent" in items["storage"]["detail"]
    assert "test-secret-key" not in r.text and "test-access-key" not in r.text
