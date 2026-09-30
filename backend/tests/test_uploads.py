import os

from app.config import get_settings
from app.storage import get_storage

from .conftest import PNG


def _project(client, h):
    return client.post("/api/projects", json={"title": "P"}, headers=h).json()["id"]


def _upload(client, h, pid, data=PNG, name="pic.png", type_="STYLE"):
    return client.post(f"/api/projects/{pid}/references", headers=h, data={"type": type_},
                       files={"file": (name, data, "image/png")})


def test_reference_upload_fetch_delete(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    r = _upload(client, h, pid)
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["mime_type"] == "image/png" and ref["type"] == "STYLE"
    got = client.get(ref["url"], headers=h)
    assert got.status_code == 200 and got.content == PNG
    assert client.get(ref["url"]).status_code == 401
    assert client.delete(f"/api/projects/{pid}/references/{ref['id']}", headers=h).status_code == 204
    assert client.get(ref["url"], headers=h).status_code == 404
    assert get_storage().usage_bytes(f"uploads/{pid}") == 0


def test_rejects_non_images_and_spoofed_content_type(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    assert _upload(client, h, pid, data=b"<?php echo 1; ?>", name="x.png").status_code == 415
    assert _upload(client, h, pid, data=b"").status_code == 400


def test_rejects_oversize(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    big = PNG + b"\0" * (get_settings().max_upload_bytes + 1)
    assert _upload(client, h, pid, data=big).status_code == 413


def test_filename_is_sanitized_and_stays_in_storage(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    ref = _upload(client, h, pid, name="../../../evil name.png").json()
    assert "/" not in ref["original_filename"] and ".." not in ref["original_filename"]
    root = str(get_storage().root)
    for dirpath, _, files in os.walk(root):
        assert dirpath.startswith(root)


def test_other_user_cannot_fetch_or_delete_reference(client, make_user):
    h1, _ = make_user("a@example.com")
    h2, _ = make_user("b@example.com")
    pid = _project(client, h1)
    ref = _upload(client, h1, pid).json()
    assert client.get(ref["url"], headers=h2).status_code == 404
    assert client.delete(f"/api/projects/{pid}/references/{ref['id']}", headers=h2).status_code == 404
    assert client.post(f"/api/projects/{pid}/references", headers=h2, data={"type": "OTHER"},
                       files={"file": ("a.png", PNG, "image/png")}).status_code == 404


def test_character_image_and_project_delete_cleans_files(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    c = client.post(f"/api/projects/{pid}/characters", json={"name": "A"}, headers=h).json()
    r = client.post(f"/api/projects/{pid}/characters/{c['id']}/image", headers=h, files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["image_url"]
    assert client.get(r.json()["image_url"], headers=h).content == PNG
    assert get_storage().usage_bytes(f"uploads/{pid}") > 0
    client.delete(f"/api/projects/{pid}", headers=h)
    assert get_storage().usage_bytes(f"uploads/{pid}") == 0


def test_poster_from_reference(client, make_user):
    h, _ = make_user()
    pid = _project(client, h)
    ref = _upload(client, h, pid).json()
    p = client.put(f"/api/projects/{pid}/poster", json={"reference_id": ref["id"]}, headers=h).json()
    assert p["thumbnail_url"]
    assert client.get(p["thumbnail_url"], headers=h).status_code == 200
    client.delete(f"/api/projects/{pid}/references/{ref['id']}", headers=h)
    assert client.get(f"/api/projects/{pid}", headers=h).json()["thumbnail_url"] is None
