import pytest
import base64
import json

from app.config import get_settings
from .helpers import FakeFal, make_png, make_project, mp4_bytes, run_all, upload_ref, use_fal, use_llm, used

pytestmark = pytest.mark.usefixtures("all_features")


def refine(client, h, gen="face_replacement", prompt="", **kw):
    return client.post("/api/generate/refine", headers=h, json={"generator_type": gen, "prompt": prompt, **kw})


def setup(client, h, pid=None, source_type="SOURCE"):
    pid = pid or make_project(client, h)
    src = upload_ref(client, h, pid, make_png(120, 90, 10), "scene.png", source_type)
    face = upload_ref(client, h, pid, make_png(96, 96, 200), "face.png", "FACE")
    return pid, src, face


def face_body(pid, src, face, **over):
    body = {"generator_type": "face_replacement", "original_prompt": "", "refined_prompt": "Swap the face.", "project_id": pid,
            "reference_assets": [src["id"], face["id"]],
            "options": {"source_asset_id": src["id"], "face_asset_id": face["id"], "permission_confirmed": True}}
    body.update(over)
    return body


def test_face_schema(client, make_user):
    h, _ = make_user()
    f = {g["id"]: g for g in client.get("/api/generate/schema", headers=h).json()["generators"]}["face_replacement"]
    assert f["ui"] == "face" and f["reference"] == "required" and f["prompt"]["required"] is False and f["fields"] == []
    assert f["configured"] is False and f["config_message"] == "Face replacement provider is not configured."


# ------------------------------------------------------------------ uploads
def test_video_upload_is_validated_by_content_and_streamed(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    up = lambda data, name="clip.mp4", mime="video/mp4": client.post(f"/api/projects/{pid}/references/video", headers=h, data={"type": "SOURCE"},
                                                                     files={"file": (name, data, mime)})
    ok = up(mp4_bytes())
    assert ok.status_code == 201, ok.text
    v = ok.json()
    assert v["mime_type"] == "video/mp4" and v["type"] == "SOURCE" and v["width"] == 320 and v["height"] == 180 and 1.5 < v["duration_seconds"] < 2.5
    assert up(mp4_bytes()).status_code == 200 and up(mp4_bytes()).json()["id"] == v["id"]                 # duplicate -> existing reference
    assert up(make_png()).status_code == 415                                                              # an image is not a video
    assert up(b"MZ\x90\x00" + b"\0" * 400, "movie.mp4", "video/mp4").status_code == 415                  # executable disguised as mp4
    assert up(b"").status_code == 400
    assert up(b"\x00\x00\x00\x18ftypmp42" + b"\0" * 200).status_code == 422                               # right header, not decodable video
    assert up(mp4_bytes(121), "long.mp4").status_code == 422                                              # longer than 2 minutes
    other, _ = make_user("b@example.com")
    assert client.post(f"/api/projects/{pid}/references/video", headers=other, data={"type": "SOURCE"}, files={"file": ("c.mp4", mp4_bytes(), "video/mp4")}).status_code == 404
    assert len(client.get(f"/api/projects/{pid}/references", headers=h).json()) == 1


def test_video_upload_size_limit(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_video_upload_mb", 0)
    h, _ = make_user()
    pid = make_project(client, h)
    r = client.post(f"/api/projects/{pid}/references/video", headers=h, data={"type": "SOURCE"}, files={"file": ("c.mp4", mp4_bytes(), "video/mp4")})
    assert r.status_code == 413 and "MB" in r.json()["error"]["message"]
    from app.storage import get_storage
    assert get_storage().usage_bytes(f"uploads/{pid}") == 0                       # nothing left behind


def test_face_image_validation(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    up = lambda data, type_="FACE", name="f.png": client.post(f"/api/projects/{pid}/references", headers=h, data={"type": type_}, files={"file": (name, data, "image/png")})
    assert up(make_png(63, 200)).status_code == 422 and up(make_png(64, 64)).status_code == 201
    assert up(b"#!/bin/sh\nrm -rf /\n" + b"\0" * 300, name="run.sh").status_code == 415
    assert up(make_png(70, 70, 2), "NOT_A_TYPE").status_code == 422


# ------------------------------------------------------------------ generation
def test_face_refinement_is_local_and_free(client, make_user, monkeypatch):
    llm = use_llm(monkeypatch)
    h, _ = make_user()
    pid, src, face = setup(client, h)
    r = refine(client, h, prompt="Match the lighting", project_id=pid, reference_assets=[src["id"], face["id"]],
               options={"source_asset_id": src["id"], "face_asset_id": face["id"]}).json()
    assert r["metadata"]["method"] == "local" and r["refined_prompt"] == "Match the lighting" and llm.requests == []
    assert any("text instructions" in w for w in r["metadata"]["warnings"])


def test_face_replacement_success_with_mocked_provider(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(result_kind="image"), video=False, face=True)
    h, _ = make_user()
    pid, src, face = setup(client, h)
    r = client.post("/api/generations", headers=h, json=face_body(pid, src, face))
    assert r.status_code == 201, r.text
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "COMPLETED" and j["provider"] == "fal-face" and j["options"]["permission_confirmed"] is True
    sent = fake.submits[0]
    assert sent["model"] == "fal-ai/face-swap"
    assert base64.b64decode(sent["payload"]["base_image_url"].split(",", 1)[1]) == make_png(120, 90, 10)
    assert base64.b64decode(sent["payload"]["swap_image_url"].split(",", 1)[1]) == make_png(96, 96, 200)
    assert fake.headers_seen[0]["authorization"] == "Key test-face-key"
    [a] = client.get(f"/api/projects/{pid}/assets?type=FACE", headers=h).json()
    assert a["type"] == "FACE" and a["format"] == "png" and a["mime_type"] == "image/png" and a["has_file"] and a["thumbnail_url"]
    assert a["meta"]["source_asset_id"] == src["id"] and a["meta"]["face_asset_id"] == face["id"] and a["provider"] == "fal-face"
    assert client.get(a["url"], headers=h).content == make_png(128, 128, 60)
    n = client.get("/api/notifications", headers=h).json()["items"][0]
    assert n["title"] == "Your face replacement is ready." and n["asset_id"] == a["id"]
    assert used(client, h, "face_replacement") == 1 and "test-face-key" not in json.dumps([j, a])
    dl = client.get(f"/api/assets/{a['id']}/download", headers=h)
    assert dl.status_code == 200 and dl.headers["content-disposition"].endswith('-face-v1.png"')
    other, _ = make_user("b@example.com")
    assert client.get(a["url"], headers=other).status_code == 404 and client.get(f"/api/assets/{a['id']}", headers=other).status_code == 404


def test_face_permission_and_inputs_are_enforced_and_free_when_invalid(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(result_kind="image"), video=False, face=True)
    h, _ = make_user()
    pid, src, face = setup(client, h)
    post = lambda **kw: client.post("/api/generations", headers=h, json=face_body(pid, src, face, **kw))
    no_perm = post(options={"source_asset_id": src["id"], "face_asset_id": face["id"]})
    assert no_perm.status_code == 422 and no_perm.json()["error"]["code"] == "permission_required"
    assert post(options={"source_asset_id": src["id"], "face_asset_id": face["id"], "permission_confirmed": False}).status_code == 422
    assert post(options={"source_asset_id": src["id"], "face_asset_id": face["id"], "permission_confirmed": "yes"}).status_code == 422
    assert post(options={"face_asset_id": face["id"], "permission_confirmed": True}).status_code == 422             # no source
    assert post(options={"source_asset_id": src["id"], "permission_confirmed": True}).status_code == 422            # no face
    assert post(options={"source_asset_id": src["id"], "face_asset_id": src["id"], "permission_confirmed": True}).status_code == 422   # same file
    assert post(reference_assets=[src["id"]]).status_code == 422                                                       # face not among the files
    v = client.post(f"/api/projects/{pid}/references/video", headers=h, data={"type": "SOURCE"}, files={"file": ("c.mp4", mp4_bytes(), "video/mp4")}).json()
    bad_face = post(reference_assets=[src["id"], v["id"]], options={"source_asset_id": src["id"], "face_asset_id": v["id"], "permission_confirmed": True})
    assert bad_face.status_code == 422 and "face" in bad_face.json()["error"]["message"].lower()
    other_project = make_project(client, h, "Other")
    assert post(project_id=other_project).status_code == 422                                                           # files belong to another project
    assert used(client, h, "face_replacement") == 0 and fake.submits == []
    assert post().status_code == 201


def test_video_sources_are_refused_with_a_clear_message(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(result_kind="image"), video=False, face=True)
    h, _ = make_user()
    pid, _src, face = setup(client, h)
    v = client.post(f"/api/projects/{pid}/references/video", headers=h, data={"type": "SOURCE"}, files={"file": ("c.mp4", mp4_bytes(), "video/mp4")}).json()
    body = face_body(pid, v, face)
    r = client.post("/api/generations", headers=h, json=body)
    assert r.status_code == 422 and "only supports image sources" in r.json()["error"]["message"]
    assert client.post("/api/generate/refine", headers=h, json={"generator_type": "face_replacement", "prompt": "", "project_id": pid,
                                                              "reference_assets": body["reference_assets"], "options": body["options"]}).status_code == 422
    assert fake.submits == [] and used(client, h, "face_replacement") == 0


def test_face_provider_not_configured_fails_cleanly(client, make_user):
    h, _ = make_user()
    pid, src, face = setup(client, h)
    r = client.post("/api/generations", headers=h, json=face_body(pid, src, face))
    assert r.status_code == 201
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "FAILED" and j["error_code"] == "API_NOT_CONFIGURED" and j["error_message"] == "Face replacement provider is not configured."
    assert used(client, h, "face_replacement") == 0 and client.get(f"/api/projects/{pid}/assets?type=FACE", headers=h).json() == []
    assert client.get("/api/notifications", headers=h).json()["items"][0]["title"] == "Your face replacement generation failed."


def test_face_provider_failures_and_limits(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(submit_status=500, result_kind="image"), video=False, face=True)
    h, _ = make_user()
    pid, src, face = setup(client, h)
    r = client.post("/api/generations", headers=h, json=face_body(pid, src, face))
    run_all()
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert j["status"] == "FAILED" and j["attempts"] == 2 and j["error_message"] == "Face replacement generation failed because the configured face replacement provider is unavailable."
    fake.submit_status = 200
    codes = [client.post("/api/generations", headers=h, json=face_body(pid, src, face)).status_code for _ in range(4)]
    assert codes == [201, 201, 201, 429]           # the first failed job never reached the provider and was refunded, so 3 more fit
