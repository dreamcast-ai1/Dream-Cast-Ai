"""What the frontend needs to show a finished generation by itself (the job carries its asset; media is private to its owner), plus the way
fal-backed providers treat malformed answers. All provider HTTP is mocked: no real call is made."""
import httpx
import pytest

from .helpers import FakeFal, generate, make_project, run_all, use_fal

pytestmark = pytest.mark.usefixtures("all_features")
PROMPT = "A lone warrior at the gate of a ruined castle at sunrise."


def job(client, h, jid):
    return client.get(f"/api/jobs/{jid}", headers=h).json()


@pytest.mark.parametrize("kind,gen,opts", [("image", "image", {"aspect_ratio": "1:1"}), ("video", "video", {"duration_seconds": 10, "aspect_ratio": "16:9"})])
def test_a_finished_generation_hands_the_ui_its_asset_so_no_second_click_is_needed(client, make_user, monkeypatch, kind, gen, opts):
    use_fal(monkeypatch, FakeFal(result_kind=kind))
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, gen, prompt=PROMPT, options=opts, project_id=pid)
    assert r.status_code == 201
    run_all()
    j = job(client, h, r.json()["job_id"])
    assert j["status"] == "COMPLETED" and j["assets"] and j["assets"][0]["id"] and j["assets"][0]["url"]
    asset = j["assets"][0]
    media = client.get(asset["url"], headers=h)                                   # the URL the result panel displays
    assert media.status_code == 200 and media.content
    if kind == "video":
        s = client.post("/api/media/stream-url", headers=h, json={"kind": "asset", "id": asset["id"]})
        assert s.status_code == 200 and client.get(s.json()["url"], headers={"Range": "bytes=0-9"}).status_code in (200, 206)
    # still there after a "refresh": the same data comes back from the API, not from browser memory
    assert job(client, h, j["id"])["assets"][0]["id"] == asset["id"]


def test_another_user_cannot_reach_the_generated_media_or_the_job(client, make_user, monkeypatch):
    use_fal(monkeypatch, FakeFal(result_kind="image"))
    a, _ = make_user("a@example.com")
    b, _ = make_user("b@example.com")
    pid = make_project(client, a)
    jid = generate(client, a, "image", prompt=PROMPT, options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    run_all()
    asset = job(client, a, jid)["assets"][0]
    assert client.get(asset["url"], headers=b).status_code == 404
    assert client.post("/api/media/stream-url", headers=b, json={"kind": "asset", "id": asset["id"]}).status_code == 404
    assert client.get(f"/api/jobs/{jid}", headers=b).status_code == 404
    assert client.get(asset["url"]).status_code == 401
    assert client.post(f"/api/jobs/{jid}/regenerate", headers=b).status_code == 404


def test_a_failed_generation_reports_at_once_and_retry_makes_exactly_one_new_job(client, make_user, monkeypatch):
    fake = use_fal(monkeypatch, FakeFal(result_kind="image", result_status=422))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "image", prompt=PROMPT, options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(2):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and j["error_message"] and "Traceback" not in j["error_message"]
    submits_before = len(fake.submits)
    fake.result_status = 200
    r = client.post(f"/api/jobs/{jid}/regenerate", headers=h)
    assert r.status_code == 201 and r.json()["job_id"] != jid
    run_all()
    assert job(client, h, r.json()["job_id"])["status"] == "COMPLETED" and len(fake.submits) == submits_before + 1       # one new provider request, no duplicates
    usage = {i["generator"]: i for i in client.get("/api/usage", headers=h).json()["items"]}
    assert usage["image"]["used"] == 2        # documented billing model: an attempt that reached the provider (and failed there) stays counted; the retry is one more, never more


@pytest.mark.parametrize("stage", ["submit", "status", "result"])
def test_a_malformed_provider_answer_fails_cleanly_instead_of_crashing(client, make_user, monkeypatch, stage):
    class Broken(FakeFal):
        def handler(self, request):
            url, method = str(request.url), request.method
            if stage == "submit" and method == "POST":
                return httpx.Response(200, content=b"<html>not json</html>", headers={"content-type": "text/html"})
            if stage == "status" and url.endswith("/status"):
                return httpx.Response(200, content=b"not json")
            if stage == "result" and "/requests/" in url and not url.endswith("/status") and method == "GET" and not url.startswith(self.FILES):
                return httpx.Response(200, content=b"[1, 2, 3]")
            return super().handler(request)
    use_fal(monkeypatch, Broken(result_kind="image"))
    h, _ = make_user()
    pid = make_project(client, h)
    jid = generate(client, h, "image", prompt=PROMPT, options={"aspect_ratio": "1:1"}, project_id=pid).json()["job_id"]
    for _ in range(3):
        run_all()
    j = job(client, h, jid)
    assert j["status"] == "FAILED" and "unreadable answer" in (j["error_message"] or "")
    used = next(i["used"] for i in client.get("/api/usage", headers=h).json()["items"] if i["generator"] == "image")
    assert used == (0 if stage == "submit" else 1)          # refused at submit = nothing happened = refunded; failed after the provider took the job = counted once, not per attempt
