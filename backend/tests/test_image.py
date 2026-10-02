"""Image generation (fal.ai behind the provider interface) and the Library. All provider HTTP is mocked; no real calls."""
import json

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import GeneratedAsset, GenerationJob, Project
from app.services import subscriptions

from .helpers import FakeFal, generate, make_project, run_all, use_fal

pytestmark = pytest.mark.usefixtures("all_features")

PROMPT = "A lone warrior at the gate of a ruined castle at sunrise."
OPTS = {"style": "Cinematic", "aspect_ratio": "16:9"}


def image_fal(monkeypatch, **kw):
    return use_fal(monkeypatch, FakeFal(result_kind="image", **kw))


def used(client, h, gen="image"):
    return next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == gen)


def make_image(client, h, pid=None, prompt=PROMPT, options=OPTS):
    r = generate(client, h, "image", prompt=prompt, options=options, project_id=pid)
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def job(client, h, jid):
    return client.get(f"/api/jobs/{jid}", headers=h).json()


def library(client, h, query=""):
    return client.get(f"/api/assets{query}", headers=h).json()


# ------------------------------------------------------------------ generation
def test_image_generator_is_listed_with_its_options(client, make_user):
    h, _ = make_user()
    g = next(x for x in client.get("/api/generate/schema", headers=h).json()["generators"] if x["id"] == "image")
    assert g["label"] == "Image" and [f["key"] for f in g["fields"]] == ["style", "aspect_ratio"] and g["configured"] is False
    assert "has not been configured" in g["config_message"] and not g["simulated"]


def test_image_without_a_key_fails_cleanly_creates_nothing_and_refunds(client, make_user):
    h, _ = make_user()
    pid = make_project(client, h)
    jid = make_image(client, h, pid)
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_message"] == "Image generation is currently unavailable because the image provider has not been configured."
    assert used(client, h) == 0 and client.get(f"/api/projects/{pid}/assets", headers=h).json() == []        # no fake image


def test_image_is_generated_stored_and_described(client, make_user, monkeypatch):
    fake = image_fal(monkeypatch)
    h, user = make_user()
    pid = make_project(client, h)
    jid = make_image(client, h, pid)
    assert job(client, h, jid)["status"] == "QUEUED"                           # returns at once; the worker does the rest
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED" and j["provider"] == "fal-image" and j["type"] == "image"
    sent = fake.submits[0]
    assert sent["model"] == "fal-ai/flux/schnell" and sent["payload"] == {"prompt": PROMPT, "image_size": "landscape_16_9", "num_images": 1}
    assert fake.headers_seen[0]["authorization"] == "Key test-video-key"        # falls back to the fal.ai key already configured
    [a] = client.get(f"/api/projects/{pid}/assets?type=IMAGE", headers=h).json()
    assert a["type"] == "IMAGE" and a["status"] == "READY" and a["has_file"] and a["thumbnail_url"] and a["format"] == "png" and a["mime_type"] == "image/png"
    assert a["prompt"] == PROMPT and a["provider"] == "fal-image" and a["project_id"] == pid and a["created_at"] and a["title"].startswith("A lone warrior")
    d = client.get(f"/api/assets/{a['id']}", headers=h).json()
    assert d["meta"]["width"] == 128 and d["meta"]["height"] == 128 and d["meta"]["model"] == "fal-ai/flux/schnell"
    assert d["meta"]["provider_job_id"] == "req1" and d["meta"]["options"]["style"] == "Cinematic" and d["job_id"] == jid
    with SessionLocal() as db:
        assert json.loads(db.get(GenerationJob, jid).external_id)["r"] == "req1"             # provider job id persisted on the job
        assert db.get(GeneratedAsset, a["id"]).user_id == user["id"]
    assert client.get(a["url"], headers=h).content[:4] == b"\x89PNG" and client.get(a["thumbnail_url"], headers=h).content[:3] == b"\xff\xd8\xff"
    assert client.get("/api/notifications", headers=h).json()["items"][0]["title"] == "Your image is ready."
    assert used(client, h) == 1 and "test-video-key" not in json.dumps([j, d])


@pytest.mark.parametrize("ratio,size", [("16:9", "landscape_16_9"), ("9:16", "portrait_16_9"), ("1:1", "square_hd")])
def test_aspect_ratios_map_to_provider_sizes(client, make_user, monkeypatch, ratio, size):
    fake = image_fal(monkeypatch)
    h, _ = make_user()
    make_image(client, h, make_project(client, h), options={"aspect_ratio": ratio})
    run_all()
    assert fake.submits[0]["payload"]["image_size"] == size


def test_invalid_image_requests_cost_nothing(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    h, _ = make_user()
    assert generate(client, h, "image", prompt="", options={}).status_code == 422
    assert generate(client, h, "image", prompt=PROMPT, options={"aspect_ratio": "7:3"}).status_code == 422
    assert generate(client, h, "image", prompt="x" * 5000, options={}).status_code == 422
    assert used(client, h) == 0


def test_provider_rejection_before_work_refunds_and_gives_a_clean_message(client, make_user, monkeypatch):
    image_fal(monkeypatch, submit_status=401)
    h, _ = make_user()
    jid = make_image(client, h, make_project(client, h))
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_message"] and "Traceback" not in json.dumps(j)
    assert used(client, h) == 0


def test_no_project_still_saves_the_image_in_quick_creations(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    h, _ = make_user()
    jid = make_image(client, h, None)
    run_all()
    j = job(client, h, jid)
    assert j["status"] == "COMPLETED" and j["project_title"] == "Quick creations" and j["assets"][0]["type"] == "IMAGE"
    [item] = library(client, h)
    assert item["project_title"] == "Quick creations" and item["type"] == "IMAGE"
    make_image(client, h, None)                                                  # reuses the same project
    with SessionLocal() as db:
        assert db.query(Project).filter_by(title="Quick creations").count() == 1


# ------------------------------------------------------------------ usage per plan
def test_image_allowance_per_plan(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    ah, _ = make_user("boss@example.com")
    h, user = make_user()
    pid = make_project(client, h)
    codes = [generate(client, h, "image", prompt=PROMPT, options=OPTS, project_id=pid).status_code for _ in range(31)]
    assert codes == [201] * 30 + [429]                                           # Teaser: 30 a month
    assert client.patch(f"/api/admin/users/{user['id']}/subscription", headers=ah, json={"plan_id": "trailer", "days": 30}).status_code == 200
    assert next(i["limit"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == "image") == 90          # Trailer: 3x
    assert generate(client, h, "image", prompt=PROMPT, options=OPTS, project_id=pid).status_code == 201
    client.patch(f"/api/admin/users/{user['id']}/subscription", headers=ah, json={"plan_id": "movie", "days": 30})
    assert next(i["limit"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == "image") == 240         # Movie: 8x


def test_regenerate_adds_a_version_and_costs_one_credit_once(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    make_image(client, h, pid)
    run_all()
    [a] = client.get(f"/api/projects/{pid}/assets?type=IMAGE", headers=h).json()
    r = client.post(f"/api/assets/{a['id']}/regenerate", headers=h)
    assert r.status_code == 201
    assert used(client, h) == 2                                                  # one new credit, reserved once
    run_all()
    assert sorted(x["version"] for x in client.get(f"/api/projects/{pid}/assets?type=IMAGE", headers=h).json()) == [1, 2]
    assert used(client, h) == 2


def test_cancelling_a_queued_image_refunds(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    h, _ = make_user()
    jid = make_image(client, h, make_project(client, h))
    assert used(client, h) == 1
    assert client.post(f"/api/jobs/{jid}/cancel", headers=h).json()["status"] == "CANCELLED"
    run_all()
    assert used(client, h) == 0 and library(client, h) == []


# ------------------------------------------------------------------ library
def test_library_lists_only_my_assets_across_projects_with_filters_and_paging(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    ha, ua = make_user("a@example.com")
    hb, _ = make_user("b@example.com")
    p1, p2 = make_project(client, ha, "Film One"), make_project(client, ha, "Film Two")
    for pid in (p1, p1, p2):
        make_image(client, ha, pid)
    make_image(client, hb, make_project(client, hb, "Other"))
    run_all()
    with SessionLocal() as db:                                                   # a story and an assembled movie belong to the library too
        db.add(GeneratedAsset(project_id=p1, user_id=ua["id"], type="STORY", title="My story", text_content="Once upon a time.", status="READY", format="txt"))
        db.add(GeneratedAsset(project_id=p2, user_id=ua["id"], type="VIDEO", title="Film Two — Movie", file_path="x", status="READY", duration_seconds=44.5,
                              meta={"movie": True}, mime_type="video/mp4", format="mp4"))
        db.add(GeneratedAsset(project_id=p2, user_id=ua["id"], type="VIDEO", title="A clip", file_path="y", status="READY", mime_type="video/mp4", format="mp4"))
        db.commit()
    everything = library(client, ha)
    assert len(everything) == 6 and {i["project_title"] for i in everything} == {"Film One", "Film Two"}
    assert [i["created_at"] for i in everything] == sorted((i["created_at"] for i in everything), reverse=True)        # newest first
    assert len(library(client, ha, "?type=IMAGE")) == 3 and [i["title"] for i in library(client, ha, "?type=STORY")] == ["My story"]
    [movie] = library(client, ha, "?type=VIDEO&movies=true")
    assert movie["is_movie"] and movie["duration_seconds"] == 44.5 and movie["title"] == "Film Two — Movie"
    assert [i["title"] for i in library(client, ha, "?type=VIDEO&movies=false")] == ["A clip"]
    assert len(library(client, ha, f"?project_id={p1}")) == 3
    assert len(library(client, ha, "?limit=2")) == 2 and len(library(client, ha, "?limit=2&offset=4")) == 2 and library(client, ha, "?limit=2&offset=6") == []
    assert all(i["prompt"] == PROMPT for i in library(client, ha, "?type=IMAGE"))
    mine_b = library(client, hb)
    assert len(mine_b) == 1 and mine_b[0]["project_title"] == "Other" and not {i["id"] for i in mine_b} & {i["id"] for i in everything}
    assert client.get("/api/assets").status_code == 401
    assert library(client, ha, "?type=BOGUS") == []


def test_library_and_media_survive_logout_and_login(client, make_user, monkeypatch):
    image_fal(monkeypatch)
    h, user = make_user("persist@example.com")
    pid = make_project(client, h, "Persistent")
    make_image(client, h, pid)
    run_all()
    before = library(client, h)
    r = client.post("/api/auth/login", json={"email": "persist@example.com", "password": "password123"})        # a fresh session
    h2 = {"Authorization": "Bearer " + r.json()["access_token"]}
    after = library(client, h2)
    assert [i["id"] for i in after] == [i["id"] for i in before] and after[0]["project_title"] == "Persistent"
    assert client.get(after[0]["url"], headers=h2).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=h2).status_code == 200


# ------------------------------------------------------------------ configuration
def test_image_key_can_be_separate_or_shared_and_placeholders_are_ignored(monkeypatch):
    from app.config import Settings
    from app.providers.image import FalImageProvider
    assert Settings(_env_file=None, image_provider_api_key="REPLACE_WITH_FAL_AI_KEY").image_provider_api_key == ""
    only_video = FalImageProvider(Settings(_env_file=None, video_provider_api_key="vk"))
    assert only_video.is_configured() and only_video.api_key == "vk"
    separate = FalImageProvider(Settings(_env_file=None, video_provider_api_key="vk", image_provider_api_key="ik"))
    assert separate.api_key == "ik"
    assert not FalImageProvider(Settings(_env_file=None)).is_configured()
    assert get_settings().image_provider_model == "fal-ai/flux/schnell"


def test_video_asset_records_model_and_provider_job_id_and_survives_relogin(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal())
    h, _ = make_user("vid@example.com")
    pid = make_project(client, h)
    jid = generate(client, h, "video", prompt="A knight rides at dawn.", options={"duration_seconds": 10, "aspect_ratio": "16:9"}, project_id=pid).json()["job_id"]
    run_all()
    [a] = client.get(f"/api/projects/{pid}/assets?type=VIDEO", headers=h).json()
    d = client.get(f"/api/assets/{a['id']}", headers=h).json()
    assert d["meta"]["model"].endswith("text-to-video") and d["meta"]["provider_job_id"] == "req1" and d["meta"]["width"] == 320 and d["duration_seconds"]
    with SessionLocal() as db:
        assert json.loads(db.get(GenerationJob, jid).external_id)["r"] == "req1"
    h2 = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"email": "vid@example.com", "password": "password123"}).json()["access_token"]}
    [item] = library(client, h2)
    assert item["type"] == "VIDEO" and item["has_file"] and item["duration_seconds"] and not item["is_movie"]
    stream = client.post("/api/media/stream-url", headers=h2, json={"kind": "asset", "id": a["id"]}).json()["url"]
    assert client.get(stream, headers={"Range": "bytes=0-9"}).status_code == 206
