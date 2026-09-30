from app.db import SessionLocal
from app.models import GeneratedAsset
from app.storage import get_storage

from .helpers import STORY, WAV, generate_and_run, make_project, run_all, use_llm, use_music, use_voice, used


def a_list(client, h, pid, type_=""):
    return client.get(f"/api/projects/{pid}/assets" + (f"?type={type_}" if type_ else ""), headers=h).json()


def story(client, h, pid):
    generate_and_run(client, h, "story", project_id=pid)
    return a_list(client, h, pid, "STORY")[0]


def test_edit_save_copy_download_text(client, make_user, monkeypatch):
    rec = use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    a = story(client, h, pid)
    n = len(rec.requests)
    r = client.put(f"/api/assets/{a['id']}", headers=h, json={"text_content": STORY + "\n\nEDITED BY HAND"})
    assert r.status_code == 200 and r.json()["text_content"].endswith("EDITED BY HAND") and r.json()["meta"]["edited"]
    assert client.get(f"/api/assets/{a['id']}", headers=h).json()["text_content"].endswith("EDITED BY HAND")      # persisted
    assert client.put(f"/api/assets/{a['id']}", headers=h, json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert len(rec.requests) == n                                             # manual edits never call the AI
    txt = client.get(f"/api/assets/{a['id']}/download?format=txt", headers=h)
    assert txt.status_code == 200 and txt.text.endswith("EDITED BY HAND") and txt.headers["content-type"].startswith("text/plain")
    assert txt.headers["content-disposition"] == 'attachment; filename="renamed-story-v1.txt"'
    md = client.get(f"/api/assets/{a['id']}/download?format=md", headers=h)
    assert md.text.startswith("# Renamed\n\n") and md.headers["content-type"].startswith("text/markdown")
    assert client.get(f"/api/assets/{a['id']}/download?format=pdf", headers=h).status_code == 400
    assert client.put(f"/api/assets/{a['id']}", headers=h, json={"text_content": "x" * 200001}).status_code == 422


def test_audio_assets_cannot_be_text_edited(client, make_user, monkeypatch):
    use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "music", prompt="epic", options={"duration_seconds": 10}, project_id=pid)
    m = a_list(client, h, pid, "MUSIC")[0]
    assert client.put(f"/api/assets/{m['id']}", headers=h, json={"text_content": "hi"}).status_code == 400
    assert client.put(f"/api/assets/{m['id']}", headers=h, json={"title": "Battle Theme"}).json()["title"] == "Battle Theme"


def test_regeneration_creates_new_versions_and_keeps_old_ones(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    v1 = story(client, h, pid)
    client.put(f"/api/assets/{v1['id']}", headers=h, json={"text_content": "MY EDITED V1 TEXT " * 5})
    assert client.post(f"/api/assets/{v1['id']}/regenerate", headers=h).status_code == 201
    run_all()
    job_id = client.get(f"/api/assets/{v1['id']}", headers=h).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/regenerate", headers=h).status_code == 201       # regenerating via the job works too
    run_all()
    stories = a_list(client, h, pid, "STORY")
    assert sorted(a["version"] for a in stories) == [1, 2, 3]
    assert len({a["lineage_id"] for a in stories}) == 1
    d = client.get(f"/api/assets/{stories[0]['id']}", headers=h).json()
    assert [v["version"] for v in d["versions"]] == [1, 2, 3]
    assert client.get(f"/api/assets/{v1['id']}", headers=h).json()["text_content"].startswith("MY EDITED V1 TEXT")   # v1 untouched
    assert used(client, h, "story") == 3


def test_regenerating_audio_makes_a_new_version_with_its_own_file(client, make_user, monkeypatch):
    use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "music", prompt="epic", options={"duration_seconds": 10}, project_id=pid)
    first = a_list(client, h, pid, "MUSIC")[0]
    client.post(f"/api/assets/{first['id']}/regenerate", headers=h)
    run_all()
    vs = sorted(a_list(client, h, pid, "MUSIC"), key=lambda a: a["version"])
    assert [v["version"] for v in vs] == [1, 2] and vs[0]["url"] != vs[1]["url"]
    assert all(client.get(v["url"], headers=h).content == WAV for v in vs)


def test_duplicate_and_delete(client, make_user, monkeypatch):
    use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "music", prompt="epic", options={"duration_seconds": 10}, project_id=pid)
    m = a_list(client, h, pid, "MUSIC")[0]
    size = lambda: get_storage().usage_bytes(f"generated/{pid}")
    before = size()
    c = client.post(f"/api/assets/{m['id']}/duplicate", headers=h)
    assert c.status_code == 201 and c.json()["title"].endswith("(copy)") and c.json()["version"] == 1 and c.json()["lineage_id"] != m["lineage_id"]
    assert size() == before * 2 and client.get(c.json()["url"], headers=h).content == WAV
    assert client.delete(f"/api/assets/{c.json()['id']}", headers=h).status_code == 204
    assert size() == before and client.get(f"/api/assets/{c.json()['id']}", headers=h).status_code == 404
    assert client.get(m["url"], headers=h).status_code == 200                              # the original file is untouched


def test_asset_ownership_and_project_isolation(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    use_music(monkeypatch)
    h, _ = make_user()
    h2, _ = make_user("b@example.com")
    pid = make_project(client, h)
    s = story(client, h, pid)
    generate_and_run(client, h, "music", prompt="epic", options={"duration_seconds": 10}, project_id=pid)
    m = a_list(client, h, pid, "MUSIC")[0]
    for asset in (s, m):
        aid = asset["id"]
        assert client.get(f"/api/assets/{aid}", headers=h2).status_code == 404
        assert client.put(f"/api/assets/{aid}", headers=h2, json={"title": "x"}).status_code == 404
        assert client.delete(f"/api/assets/{aid}", headers=h2).status_code == 404
        assert client.post(f"/api/assets/{aid}/duplicate", headers=h2).status_code == 404
        assert client.post(f"/api/assets/{aid}/regenerate", headers=h2).status_code == 404
        assert client.get(f"/api/assets/{aid}/download", headers=h2).status_code == 404
    assert client.get(m["url"], headers=h2).status_code == 404
    assert client.get(f"/api/projects/{pid}/assets", headers=h2).status_code == 404
    assert client.get(f"/api/assets/{s['id']}").status_code == 401
    assert client.get(f"/api/assets/{s['id']}", headers=h).status_code == 200


def test_asset_list_filters_and_history(client, make_user, monkeypatch):
    use_llm(monkeypatch)
    use_music(monkeypatch)
    use_voice(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    for g, kw in [("story", {}), ("script", {}), ("lyrics", {}), ("music", {"options": {"duration_seconds": 10}}), ("voice", {"options": {"gender": "Male"}})]:
        assert generate_and_run(client, h, g, project_id=pid, **kw)["status"] == "COMPLETED", g
    types = lambda q: sorted({a["type"] for a in a_list(client, h, pid, q)})
    assert types("") == ["LYRICS", "MUSIC", "SCRIPT", "STORY", "VOICE"]
    assert types("STORY") == ["STORY"] and types("STORY,SCRIPT") == ["SCRIPT", "STORY"] and types("nonsense") == []
    counts = client.get(f"/api/projects/{pid}", headers=h).json()["counts"]
    assert {k: counts[k] for k in ("story", "script", "lyrics", "music", "voice")} == {"story": 1, "script": 1, "lyrics": 1, "music": 1, "voice": 1}
    assert {j["type"] for j in client.get(f"/api/jobs?project_id={pid}", headers=h).json()} == {"story", "script", "lyrics", "music", "voice"}
    assert [used(client, h, g) for g in ("story", "script", "lyrics", "music", "voice")] == [1] * 5


def test_project_delete_removes_audio_files(client, make_user, monkeypatch):
    use_music(monkeypatch)
    h, _ = make_user()
    pid = make_project(client, h)
    generate_and_run(client, h, "music", prompt="epic", options={"duration_seconds": 10}, project_id=pid)
    assert get_storage().usage_bytes(f"generated/{pid}") > 0
    client.delete(f"/api/projects/{pid}", headers=h)
    assert get_storage().usage_bytes(f"generated/{pid}") == 0


def test_legacy_simulated_assets_still_serialize(client, make_user):
    """Assets created by Phase 2's simulator (text on a MUSIC/VIDEO asset, no file) must keep working."""
    h, u = make_user()
    pid = make_project(client, h)
    with SessionLocal() as db:
        a = GeneratedAsset(project_id=pid, user_id=u["id"], type="MUSIC", title="old", text_content="[SIMULATED OUTPUT] x", status="SIMULATED", prompt="p")
        db.add(a)
        db.flush()
        a.lineage_id = a.id
        db.commit()
        aid = a.id
    d = client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["status"] == "SIMULATED" and d["version"] == 1 and d["has_file"] is False and d["versions"][0]["id"] == aid
    assert client.get(f"/api/assets/{aid}/download", headers=h).status_code == 200
