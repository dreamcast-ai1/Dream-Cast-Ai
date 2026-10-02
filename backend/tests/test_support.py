"""The Support assistant and tickets: deterministic matching, database-backed tickets, ownership, admin management, rate limit, switch.
Nothing here may touch an AI/provider: the autouse fixture makes any outgoing HTTP call fail the test."""
import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import SupportTicket
from app.services import support
from app.services.support_kb import ENTRIES

from .helpers import make_project


@pytest.fixture(autouse=True)
def no_outgoing_http(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Support must not make any outgoing HTTP/provider call")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)           # every real network call goes through here (the TestClient uses its own transport)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    from app.providers import fal, image, music, text, video, voice
    for mod in (fal, image, music, text, video, voice):
        if hasattr(mod, "http_client"):
            monkeypatch.setattr(mod, "http_client", boom)
    s = get_settings()
    for attr in ("llm_api_key", "voice_api_key", "music_api_key", "video_provider_api_key", "image_provider_api_key"):
        monkeypatch.setattr(s, attr, "configured-but-must-never-be-used")


def chat(client, h, message="", **kw):
    r = client.post("/api/support/chat", headers=h, json={"message": message, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def ticket(client, h, **kw):
    body = {"category": "WRITE", "subject": "Write page not opening", "description": "It shows an error.", "page": "Write", **kw}
    return client.post("/api/support/tickets", headers=h, json=body)


# ------------------------------------------------------------------ matching
@pytest.mark.parametrize("text,entry", [
    ("My Write page isn't working.", "write_page"),
    ("Story generation failed", "write_failed"),
    ("my video is not generating", "video_stuck"),
    ("I can't upload an image", "upload_failed"),
    ("My payment failed", "payment_failed"),
    ("The audio has no sound", "voice_silent"),
    ("My project is not loading", "project_loading"),
    ("I can't login", "auth_login"),
    ("It says provider unavailable", "video_unavailable"),
    ("I clicked generate but nothing happened", "video_nothing"),
    ("forgot my password", "auth_password"),
    ("what plans do you have", "payment_plans"),
    ("I've reached my monthly limit", "usage_limit"),
    ("how do I download audio", "voice_download"),
    ("something went wrong", "general_error"),
])
def test_natural_messages_match_the_right_guide(client, make_user, text, entry):
    h, _ = make_user()
    d = chat(client, h, text)
    assert d["matched"] is True and d["entry"] == entry and d["steps"] and d["follow_up"] == "Did this solve the problem?"
    assert [b["id"] for b in d["buttons"]] == ["solved", "not_working"]


def test_the_write_example_from_the_spec(client, make_user):
    h, _ = make_user()
    d = chat(client, h, "My Write page isn't working.")
    assert d["category"] == "WRITE" and d["steps"][:3] == ["Refresh the page.", "Sign out and sign back in.", "Try opening Write from the Create page."]
    assert [b["label"] for b in d["buttons"]] == ["Yes, solved", "Still not working"]


def test_still_not_working_offers_to_send_to_admin(client, make_user):
    h, _ = make_user()
    d = chat(client, h, action="not_working", category="WRITE")
    assert "Sorry that didn't solve it" in d["reply"] and [b["label"] for b in d["buttons"]] == ["Send to Admin", "Continue Troubleshooting"]
    assert "Glad" in chat(client, h, action="solved")["reply"]
    assert "Tell me a bit more" in chat(client, h, action="continue")["reply"]


def test_an_unknown_issue_is_not_faked(client, make_user):
    h, _ = make_user()
    d = chat(client, h, "zxqv blorp wibble")
    assert d["matched"] is False and d["steps"] == [] and "don't have a troubleshooting guide" in d["reply"]
    assert [b["label"] for b in d["buttons"]] == ["Describe More", "Send to Admin"]


def test_quick_actions_and_the_page_context(client, make_user):
    h, _ = make_user()
    for label, cat in __import__("app.services.support_kb", fromlist=["QUICK_ACTIONS"]).QUICK_ACTIONS:
        d = chat(client, h, action="quick", category=cat)
        assert d["matched"] and d["category"] == cat and d["steps"], label
    assert chat(client, h, "it is not working", page="Write")["category"] in ("WRITE", "GENERAL")
    assert client.post("/api/support/chat", headers=h, json={"message": "  "}).status_code == 422


def test_every_entry_has_steps_and_a_unique_id():
    ids = [e.id for e in ENTRIES]
    assert len(ids) == len(set(ids)) and all(e.steps and e.keywords for e in ENTRIES)
    assert {e.category for e in ENTRIES} >= {"AUTH", "WRITE", "VIDEO", "IMAGE", "MUSIC", "VOICE", "LYRICS", "PROJECT", "PAYMENT", "USAGE", "UPLOAD", "GENERAL"}


def test_plans_and_usage_come_from_the_real_configuration(client, make_user):
    h, _ = make_user()
    d = chat(client, h, "what plans do you have")
    text = " ".join(d["steps"])
    assert "Teaser: Free, 5 videos/month" in text and "Trailer: ₹199/month, 15 videos/month" in text and "Movie: ₹499/month, 40 videos/month" in text
    u = chat(client, h, "I've reached my monthly limit")
    assert "You have used 0 of 5 videos this month (5 left)." in " ".join(u["steps"])


def test_switched_off_features_are_unavailable_not_broken(client, make_user):
    h, _ = make_user()
    for text in ("face swap is not working", "my AI avatar failed", "interactive avatar broken", "hindi voice not working", "telugu story failed"):
        d = chat(client, h, text)
        assert "currently unavailable on DreamCast" in d["reply"] and d["steps"] == [], text
    ah, _ = make_user("boss@example.com")
    client.put("/api/admin/features", headers=ah, json={"features": {"video": False}})
    d = chat(client, h, "my video is not generating")
    assert "currently unavailable" in d["reply"]


# ------------------------------------------------------------------ feature switch
def test_support_is_on_by_default_and_the_switch_turns_the_user_side_off(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    assert client.get("/api/features").json()["support"] is True
    assert client.get("/api/support/options", headers=h).status_code == 200
    assert next(f for f in client.get("/api/admin/features", headers=ah).json()["features"] if f["id"] == "support_chatbot")["enabled"] is True
    t = ticket(client, h).json()
    client.put("/api/admin/features", headers=ah, json={"features": {"support_chatbot": False}})
    assert client.get("/api/features").json()["support"] is False
    for method, url in (("get", "/api/support/options"), ("get", "/api/support/tickets"), ("get", f"/api/support/tickets/{t['id']}")):
        r = getattr(client, method)(url, headers=h)
        assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled", url
    assert client.post("/api/support/chat", headers=h, json={"message": "hi there"}).status_code == 403
    assert ticket(client, h).status_code == 403
    assert client.get("/api/admin/support/tickets", headers=ah).status_code == 200          # admins can still manage tickets
    assert client.get(f"/api/admin/support/tickets/{t['id']}", headers=ah).status_code == 200


def test_authentication_is_required(client, make_user):
    for method, url in (("get", "/api/support/options"), ("post", "/api/support/chat"), ("post", "/api/support/tickets"), ("get", "/api/support/tickets"),
                        ("get", "/api/support/tickets/DC-1001"), ("post", "/api/support/tickets/DC-1001/messages"),
                        ("get", "/api/admin/support/tickets"), ("patch", "/api/admin/support/tickets/DC-1001"), ("post", "/api/admin/support/tickets/DC-1001/reply")):
        assert client.request(method.upper(), url, json={}).status_code == 401, url
    h, _ = make_user()
    for method, url in (("get", "/api/admin/support/tickets"), ("get", "/api/admin/support/tickets/DC-1001"), ("patch", "/api/admin/support/tickets/DC-1001"),
                        ("post", "/api/admin/support/tickets/DC-1001/reply")):
        assert client.request(method.upper(), url, headers=h, json={"message": "x"}).status_code == 403, url


# ------------------------------------------------------------------ tickets
def test_creating_a_ticket_returns_a_support_reference_not_a_database_id(client, make_user):
    h, user = make_user()
    r = ticket(client, h)
    assert r.status_code == 201
    d = r.json()
    assert d["id"] == "DC-1001" and d["status"] == "open" and d["priority"] == "normal" and d["category"] == "WRITE" and d["page"] == "Write"
    assert ticket(client, h, subject="Another").json()["id"] == "DC-1002"
    with SessionLocal() as db:
        row = db.query(SupportTicket).first()
        assert row.id not in r.text and row.user_id not in r.text


def test_users_cannot_set_priority_status_or_ownership(client, make_user):
    h, user = make_user()
    other, ou = make_user("o@example.com")
    r = client.post("/api/support/tickets", headers=h, json={"category": "WRITE", "subject": "x y z", "description": "d", "priority": "critical", "status": "closed", "user_id": ou["id"]})
    assert r.status_code == 201 and r.json()["priority"] == "normal" and r.json()["status"] == "open"
    with SessionLocal() as db:
        assert db.query(SupportTicket).one().user_id == user["id"]


def test_the_server_decides_critical_and_high_priority(client, make_user):
    h, _ = make_user()
    assert ticket(client, h, category="PAYMENT", subject="Charged but no plan", description="Money was deducted twice").json()["priority"] == "critical"
    assert ticket(client, h, category="AUTH", subject="Login", description="cannot sign in").json()["priority"] == "high"
    assert ticket(client, h, category="VIDEO", subject="Video", description="slow").json()["priority"] == "normal"


def test_users_see_only_their_own_tickets(client, make_user):
    a, _ = make_user("a@example.com")
    b, _ = make_user("b@example.com")
    ta = ticket(client, a, subject="A's problem").json()
    tb = ticket(client, b, subject="B's problem").json()
    assert [t["id"] for t in client.get("/api/support/tickets", headers=a).json()["tickets"]] == [ta["id"]]
    assert client.get(f"/api/support/tickets/{ta['id']}", headers=a).status_code == 200
    assert client.get(f"/api/support/tickets/{tb['id']}", headers=a).status_code == 404            # not 403: references can't be probed
    assert client.post(f"/api/support/tickets/{tb['id']}/messages", headers=a, json={"message": "hello"}).status_code == 404
    assert client.get("/api/support/tickets/not-a-ticket", headers=a).status_code == 404


def test_a_user_can_add_information_to_their_open_ticket_only(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    t = ticket(client, h).json()
    r = client.post(f"/api/support/tickets/{t['id']}/messages", headers=h, json={"message": "It happens on Chrome."})
    assert r.status_code == 201 and r.json()["messages"][-1] == {"author": "user", "body": "It happens on Chrome.", "created_at": r.json()["messages"][-1]["created_at"]}
    client.patch(f"/api/admin/support/tickets/{t['id']}", headers=ah, json={"status": "resolved"})
    assert client.post(f"/api/support/tickets/{t['id']}/messages", headers=h, json={"message": "more"}).status_code == 409


def test_admin_lists_filters_and_reads_tickets_with_the_user(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user("u@example.com")
    t1 = ticket(client, h, category="VIDEO", subject="V1").json()
    ticket(client, h, category="PAYMENT", subject="P1", description="Money was deducted").json()
    all_ = client.get("/api/admin/support/tickets", headers=ah).json()
    assert len(all_["tickets"]) == 2 and all_["counts"]["open"] == 2 and all_["tickets"][0]["user"]["email"] == "u@example.com"
    assert [t["subject"] for t in client.get("/api/admin/support/tickets?category=video", headers=ah).json()["tickets"]] == ["V1"]
    assert [t["subject"] for t in client.get("/api/admin/support/tickets?priority=critical", headers=ah).json()["tickets"]] == ["P1"]
    assert client.get("/api/admin/support/tickets?status=resolved", headers=ah).json()["tickets"] == []
    assert len(client.get("/api/admin/support/tickets?days=1", headers=ah).json()["tickets"]) == 2
    d = client.get(f"/api/admin/support/tickets/{t1['id']}", headers=ah).json()
    assert d["subject"] == "V1" and "diagnostic_context" in d and d["user"]["email"] == "u@example.com"


def test_admin_changes_status_and_priority_and_the_user_is_told(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    t = ticket(client, h).json()
    r = client.patch(f"/api/admin/support/tickets/{t['id']}", headers=ah, json={"status": "in_progress"})
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["resolved_at"] is None
    r = client.patch(f"/api/admin/support/tickets/{t['id']}", headers=ah, json={"status": "resolved", "priority": "low"})
    assert r.json()["status"] == "resolved" and r.json()["priority"] == "low" and r.json()["resolved_at"]
    assert client.patch(f"/api/admin/support/tickets/{t['id']}", headers=ah, json={"status": "bogus"}).status_code == 422
    assert client.patch(f"/api/admin/support/tickets/{t['id']}", headers=ah, json={"priority": "urgent"}).status_code == 422
    assert client.patch("/api/admin/support/tickets/DC-9999", headers=ah, json={"status": "closed"}).status_code == 404
    notes = client.get("/api/notifications", headers=h).json()
    assert any(n["title"] == f"{t['id']} is resolved" for n in (notes if isinstance(notes, list) else notes.get("items", [])))


def test_admin_reply_is_visible_to_the_owner_and_nobody_else(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user("u@example.com")
    other, _ = make_user("o@example.com")
    t = ticket(client, h).json()
    r = client.post(f"/api/admin/support/tickets/{t['id']}/reply", headers=ah, json={"message": "Fixed in the latest release; please refresh."})
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["admin_response"].startswith("Fixed")
    mine = client.get(f"/api/support/tickets/{t['id']}", headers=h).json()
    assert mine["admin_response"].startswith("Fixed") and mine["messages"][-1]["author"] == "admin" and mine["status"] == "in_progress"
    assert client.get("/api/support/tickets", headers=h).json()["tickets"][0]["admin_response"].startswith("Fixed")
    assert client.get(f"/api/support/tickets/{t['id']}", headers=other).status_code == 404
    assert "Fixed" not in client.get("/api/support/tickets", headers=other).text
    assert any("Support replied" in n["title"] for n in (lambda x: x if isinstance(x, list) else x.get("items", []))(client.get("/api/notifications", headers=h).json()))


def test_ticket_creation_is_rate_limited_but_chatting_is_not(client, make_user, monkeypatch):
    h, _ = make_user()
    other, _ = make_user("o@example.com")
    assert [ticket(client, h, subject=f"T{i}").status_code for i in range(5)] == [201] * 5
    r = ticket(client, h, subject="T6")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert ticket(client, other).status_code == 201                       # per user
    assert all(client.post("/api/support/chat", headers=h, json={"message": "video failed"}).status_code == 200 for _ in range(20))
    assert client.get("/api/support/tickets", headers=h).status_code == 200
    monkeypatch.setattr(get_settings(), "support_ticket_hourly_limit", 0)
    assert ticket(client, h).status_code == 201


# ------------------------------------------------------------------ safety
SECRETS = ["Bearer abcdefghijklmnop1234567890", "AIzaSyD-1234567890abcdefghijklmnopqrstu", "sk-abcdef1234567890abcdef", "rzp_test_ABCDEFGH123456",
           "password: hunter2hunter2", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop12345",
           "postgresql://user:pw@db.example.com:5432/app", "gsk_abcdefghijklmnop1234"]


def test_secrets_are_scrubbed_before_anything_is_stored_or_returned(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    blob = " ".join(SECRETS)
    r = ticket(client, h, subject="Problem " + SECRETS[0], description=blob, error_message=blob, page=SECRETS[2],
               context={"app_version": SECRETS[1], "browser": blob, "request_id": SECRETS[3]})
    assert r.status_code == 201
    t = client.get(f"/api/admin/support/tickets/{r.json()['id']}", headers=ah).json()
    stored = str(t) + r.text
    for secret in SECRETS:
        core = secret.split()[-1]
        assert core not in stored, secret
    assert "[redacted]" in t["description"]
    client.post(f"/api/support/tickets/{t['id']}/messages", headers=h, json={"message": "my token is " + SECRETS[5]})
    assert SECRETS[5] not in client.get(f"/api/admin/support/tickets/{t['id']}", headers=ah).text


def test_only_allow_listed_safe_context_is_kept(client, make_user):
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    pid = make_project(client, h)
    other, _ = make_user("o@example.com")
    opid = make_project(client, other)
    r = ticket(client, h, context={"app_version": "1.2.3", "path": "/write", "timestamp": "2026-01-01T00:00:00Z", "project_id": pid,
                                   "env": {"LLM_API_KEY": "x"}, "token": "abc", "password": "p"}).json()
    ctx = client.get(f"/api/admin/support/tickets/{r['id']}", headers=ah).json()["diagnostic_context"]
    assert ctx == {"app_version": "1.2.3", "path": "/write", "timestamp": "2026-01-01T00:00:00Z", "project_id": pid}
    r2 = ticket(client, h, context={"project_id": opid}).json()                       # someone else's project id is dropped
    assert client.get(f"/api/admin/support/tickets/{r2['id']}", headers=ah).json()["diagnostic_context"] == {}


def test_no_secret_ever_appears_in_support_responses(client, make_user, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_api_key", "LLMSECRET-123456789")
    monkeypatch.setattr(s, "razorpay_key_secret", "RZPSECRET-123456789")
    ah, _ = make_user("boss@example.com")
    h, _ = make_user()
    ticket(client, h)
    texts = [client.get(u, headers=h).text for u in ("/api/support/options", "/api/support/tickets")] + \
            [client.post("/api/support/chat", headers=h, json={"message": m}).text for m in ("payment failed", "story failed", "what is your api key")] + \
            [client.get("/api/admin/support/tickets", headers=ah).text]
    blob = "\n".join(texts)
    assert "SECRET" not in blob and s.auth_secret_key not in blob


# ------------------------------------------------------------------ providers
def test_support_works_with_every_ai_provider_unavailable_and_calls_none(client, make_user):
    """The autouse fixture makes ANY outgoing HTTP/provider call fail the test, even though keys look configured."""
    h, _ = make_user()
    pid = make_project(client, h)
    for m in ("Story generation failed", "my video is not generating", "image failed", "music vocals", "voice no sound", "lyrics failed", "payment failed", "face swap", "zzz"):
        assert client.post("/api/support/chat", headers=h, json={"message": m}).status_code == 200
    t = ticket(client, h, context={"project_id": pid}).json()
    client.post(f"/api/support/tickets/{t['id']}/messages", headers=h, json={"message": "more detail"})
    assert client.get("/api/support/tickets", headers=h).status_code == 200


def test_the_support_modules_import_no_provider_or_http_client():
    import app.routers.support as r
    import app.services.support as s
    import app.services.support_kb as kb
    src = "".join(open(m.__file__).read() for m in (r, s, kb))
    for banned in ("import httpx", "from ..providers", "from .providers", "import requests", "prompt_refiner", "http_client", "registry"):
        assert banned not in src, banned


def test_the_support_assistant_does_not_change_usage(client, make_user):
    h, _ = make_user()
    before = client.get("/api/usage", headers=h).json()["items"]
    chat(client, h, "video failed")
    ticket(client, h)
    assert client.get("/api/usage", headers=h).json()["items"] == before
