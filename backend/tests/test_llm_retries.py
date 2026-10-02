"""Transient Gemini failures (HTTP 429/500/502/503/504, timeouts, dropped connections) are retried inside ONE request with exponential backoff, and a
retry never costs extra allowance. Every provider response is mocked: no real Gemini call is made."""
import json
import logging

import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import GenerationJob, UsageRecord
from app.providers import text as text_module

from .helpers import generate, make_project, run_all
from .test_text_studio import KEY, SCRIPT_JSON, STORY_JSON, chat, gemini, script, story, used  # noqa: F401  (gemini is a fixture)

pytestmark = pytest.mark.usefixtures("all_features")
UNAVAILABLE = httpx.Response  # (alias for readability in the parametrisations below)


def res(status, **kw):
    return httpx.Response(status, **({"json": {"error": {"code": status, "message": "provider text", "status": "X"}}} | kw))


@pytest.fixture
def waits(monkeypatch):
    """Backoff enabled (base 1 s) but never actually sleeping: the requested delays are recorded instead."""
    delays: list[float] = []
    monkeypatch.setattr(text_module, "_sleep", delays.append)
    monkeypatch.setattr(get_settings(), "llm_retry_base_seconds", 1.0)
    return delays


def usage_rows(generator):
    with SessionLocal() as db:
        return [(r.status, r.request_count) for r in db.query(UsageRecord).filter_by(generator_type=generator).all()]


# ------------------------------------------------------------------ the retry loop
def test_success_on_the_first_try_makes_one_request_and_never_waits(client, make_user, gemini, waits):
    mock = gemini(chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200 and len(mock.requests) == 1 and waits == []


def test_a_503_then_success_is_retried_after_about_one_second(client, make_user, gemini, waits):
    mock = gemini(res(503), chat(STORY_JSON))
    h, _ = make_user()
    r = story(client, h)
    assert r.status_code == 200 and r.json()["title"] == "The Lantern Keeper" and len(mock.requests) == 2
    assert len(waits) == 1 and 0.8 <= waits[0] <= 1.2


def test_the_backoff_doubles_each_time(client, make_user, gemini, waits):
    mock = gemini(res(503), res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200 and len(mock.requests) == 3
    assert len(waits) == 2 and 0.8 <= waits[0] <= 1.2 and 1.6 <= waits[1] <= 2.4          # about 1 s, then about 2 s


def test_503_on_every_attempt_gives_up_after_three_with_a_clear_outage_message(client, make_user, gemini, waits, caplog):
    caplog.set_level(logging.DEBUG)
    mock = gemini(res(503))
    h, _ = make_user()
    r = story(client, h)
    assert len(mock.requests) == 3 and len(waits) == 2                                   # the maximum stays low: 3 attempts
    assert r.status_code == 503 and r.json()["error"]["code"] == "text_provider_unavailable"
    msg = r.json()["error"]["message"]
    assert "overloaded or unavailable" in msg and "retried automatically" in msg and "API key" not in msg and "rate-limiting" not in msg and "provider text" not in r.text
    assert used(client, h, "story") == 0 and usage_rows("story") == [("REFUNDED", 0)]    # nothing charged
    logs = "\n".join(rec.getMessage() for rec in caplog.records if not rec.name.startswith(("httpx", "asyncio")))
    assert "attempt 1 of 3" in logs and "HTTP 503" in logs and KEY not in logs and "Authorization" not in logs


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_every_transient_status_is_retried_once_then_succeeds(client, make_user, gemini, waits, status):
    mock = gemini(res(status), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200 and len(mock.requests) == 2 and used(client, h, "story") == 1


@pytest.mark.parametrize("status,code", [(401, "text_provider_auth"), (403, "text_provider_auth"), (404, "text_model_unavailable")])
def test_client_errors_are_never_retried(client, make_user, gemini, waits, status, code):
    mock = gemini(res(status))
    h, _ = make_user()
    r = story(client, h)
    assert len(mock.requests) == 1 and waits == [] and r.json()["error"]["code"] == code and used(client, h, "story") == 0
    assert KEY not in r.text and "provider text" not in r.text


def test_an_invalid_model_name_says_so(client, make_user, gemini):
    gemini(res(404))
    h, _ = make_user()
    r = story(client, h)
    assert r.status_code == 502 and "text model isn't available" in r.json()["error"]["message"] and "LLM_MODEL" in r.json()["error"]["message"]


def test_a_rejected_optional_setting_is_not_a_retry_and_does_not_hide_a_real_outage(client, make_user, gemini, waits):
    """400 -> resend without reasoning_effort/response_format (same attempt); a 503 after that is then retried like any other."""
    mock = gemini(res(400), res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200
    bodies = [json.loads(r.content) for r in mock.requests]
    assert "response_format" in bodies[0] and "response_format" not in bodies[1] and "response_format" not in bodies[2] and len(waits) == 1


def test_a_persistent_429_reports_a_rate_limit_not_an_outage(client, make_user, gemini, waits):
    mock = gemini(res(429))
    h, _ = make_user()
    r = story(client, h)
    assert len(mock.requests) == 3 and r.status_code == 429 and r.json()["error"]["code"] == "text_provider_rate_limited" and "rate-limiting" in r.json()["error"]["message"]
    assert used(client, h, "story") == 0


def test_retry_after_from_the_provider_is_respected_but_capped(client, make_user, gemini, waits):
    gemini(res(429, headers={"retry-after": "5"}), res(503, headers={"retry-after": "999"}), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200
    assert waits[0] >= 5 and 8 <= waits[1] <= 8.0


@pytest.mark.parametrize("failure,code,fragment", [(httpx.ReadTimeout("t"), "text_provider_timeout", "took too long"),
                                                   (httpx.ConnectError("refused"), "text_provider_unreachable", "could not be reached"),
                                                   (httpx.RemoteProtocolError("dropped"), "text_provider_unreachable", "could not be reached")])
def test_timeouts_and_dropped_connections_are_retried_and_reported_precisely(client, make_user, gemini, waits, failure, code, fragment):
    mock = gemini(failure, chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200 and len(mock.requests) == 2                 # one blip: recovered
    mock = gemini(failure)
    r = story(client, h)
    assert len(mock.requests) == 3 and r.json()["error"]["code"] == code and fragment in r.json()["error"]["message"]
    assert KEY not in r.text


def test_the_retry_count_can_be_lowered_or_switched_off(client, make_user, gemini, waits, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_attempts", 1)
    mock = gemini(res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 503 and len(mock.requests) == 1 and waits == []
    monkeypatch.setattr(get_settings(), "llm_max_attempts", 2)
    mock = gemini(res(503), res(503), chat(STORY_JSON))
    assert story(client, h).status_code == 503 and len(mock.requests) == 2


def test_every_attempt_sends_the_same_request_and_the_key_only_in_the_header(client, make_user, gemini, waits):
    mock = gemini(res(503), res(503), chat(STORY_JSON))
    h, _ = make_user()
    story(client, h)
    assert len({r.content for r in mock.requests}) == 1 and {str(r.url) for r in mock.requests} == {"https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"}
    assert all(r.headers["authorization"] == f"Bearer {KEY}" and KEY.encode() not in r.content for r in mock.requests)


def test_an_overall_time_cap_stops_retries_from_turning_into_a_hang(client, make_user, gemini, waits, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(text_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(text_module, "_sleep", lambda d: now.__setitem__(0, now[0] + 500))   # each failed attempt "takes" far longer than the budget
    mock = gemini(res(503))
    h, _ = make_user()
    assert story(client, h).status_code == 503 and len(mock.requests) == 1


# ------------------------------------------------------------------ allowance: one request = one unit, whatever the retries
def test_retries_cost_one_allowance_unit_for_the_whole_request(client, make_user, gemini, waits):
    gemini(res(503), res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200
    assert used(client, h, "story") == 1 and usage_rows("story") == [("SUCCEEDED", 1)]


def test_a_transient_failure_plus_a_malformed_answer_still_cost_one_unit(client, make_user, gemini, waits):
    mock = gemini(res(503), chat("not json at all"), res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert story(client, h).status_code == 200 and len(mock.requests) == 4
    assert usage_rows("story") == [("SUCCEEDED", 1)]


def test_retries_never_let_a_user_exceed_the_monthly_limit(client, make_user, gemini, waits):
    gemini(res(503), chat(STORY_JSON), res(503), chat(STORY_JSON))
    h, _ = make_user()
    assert [story(client, h).status_code for _ in range(20)] == [200] * 20
    assert story(client, h).status_code == 429 and used(client, h, "story") == 20          # exactly the 20 a month, not 40


def test_script_requests_retry_the_same_way(client, make_user, gemini, waits):
    mock = gemini(res(503), res(502), chat(SCRIPT_JSON))
    h, _ = make_user()
    r = script(client, h)
    assert r.status_code == 200 and r.json()["scene_count"] == 3 and len(mock.requests) == 3 and usage_rows("script") == [("SUCCEEDED", 1)]


def test_the_queued_create_path_retries_inside_one_attempt_and_one_unit(client, make_user, gemini, waits):
    """Create -> Story/Script (background job): the provider's retries happen within one job attempt, so the job doesn't need its own retry."""
    mock = gemini(res(503), res(503), chat("TITLE: A Tale\nLOGLINE: x\nGENRE: Drama\nSETTING: a harbour\nMAIN CHARACTERS:\n- Mara: a keeper's daughter\n"
                                          "STORY OUTLINE\nACT 1: She begins.\nACT 2: She struggles.\nACT 3: She wins.\nENDING: Safe."))
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "story", prompt="A lighthouse story.", project_id=pid)
    assert r.status_code == 201
    run_all()
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["status"] == "COMPLETED" and job["attempts"] == 1 and len(mock.requests) == 3
    assert used(client, h, "story") == 1


def test_the_queued_create_path_reports_an_outage_precisely_and_refunds(client, make_user, gemini, waits):
    gemini(res(503))
    h, _ = make_user()
    pid = make_project(client, h)
    r = generate(client, h, "script", prompt="A lighthouse script.", project_id=pid)
    run_all()
    run_all()                                                                              # the job's own single automatic retry
    job = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert job["status"] == "FAILED" and job["error_code"] == "PROVIDER_UNAVAILABLE"
    assert job["error_message"] == ("Script generation failed because the text provider is temporarily overloaded or unavailable. "
                                    "DreamCast already retried automatically; please try again in a minute.")
    assert used(client, h, "script") == 0 and KEY not in json.dumps(job)


def test_the_queued_create_path_words_timeouts_and_bad_answers_differently(client, make_user, gemini, waits):
    h, _ = make_user()
    pid = make_project(client, h)
    gemini(httpx.ReadTimeout("t"))
    r = generate(client, h, "story", prompt="A lighthouse story.", project_id=pid)
    run_all()
    run_all()
    assert "took too long to answer" in client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()["error_message"]
    gemini(chat(""))
    r = generate(client, h, "story", prompt="Another lighthouse story.", project_id=pid)
    run_all()
    j = client.get(f"/api/jobs/{r.json()['job_id']}", headers=h).json()
    assert "unusable answer" in j["error_message"]


# ------------------------------------------------------------------ prompt refinement shares the same retrying call
def test_prompt_refinement_retries_then_uses_the_llm(client, make_user, gemini, waits):
    mock = gemini(res(503), chat("A refined, detailed prompt about a lighthouse at dusk."))
    h, _ = make_user()
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "story", "prompt": "a lighthouse story", "options": {}})
    assert r.json()["metadata"]["method"] == "llm" and len(mock.requests) == 2
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter_by(generator_type="refinement").count() == 1


def test_prompt_refinement_still_falls_back_to_the_template_when_the_outage_lasts(client, make_user, gemini, waits):
    mock = gemini(res(503))
    h, _ = make_user()
    r = client.post("/api/generate/refine", headers=h, json={"generator_type": "story", "prompt": "a lighthouse story", "options": {}})
    assert r.status_code == 200 and r.json()["metadata"]["method"] == "template" and len(mock.requests) == 3
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter_by(generator_type="refinement").count() == 0


# ------------------------------------------------------------------ the diagnosis, as regression tests
def test_the_gemini_request_matches_googles_openai_compatibility_format(client, make_user, gemini):
    mock = gemini(chat(STORY_JSON))
    get_settings().llm_model = "gemini-3.8-flash"
    try:
        h, _ = make_user()
        story(client, h)
        req, body = mock.requests[0], mock.body
        assert req.method == "POST" and str(req.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert req.headers["authorization"] == f"Bearer {KEY}" and req.headers["content-type"] == "application/json"
        assert body["model"] == "gemini-3.8-flash" and body["reasoning_effort"] == "low" and body["response_format"] == {"type": "json_object"}
        assert set(body) == {"model", "temperature", "max_tokens", "messages", "reasoning_effort", "response_format"} and [m["role"] for m in body["messages"]] == ["system", "user"]
    finally:
        get_settings().llm_model = ""


def test_no_gemini_specific_key_variables_exist():
    """There is one LLM configuration: LLM_PROVIDER / LLM_API_KEY / LLM_MODEL. GEMINI_API_KEY / GEMINI_MODEL are not read anywhere."""
    fields = set(type(get_settings()).model_fields)
    assert {"llm_provider", "llm_api_key", "llm_model"} <= fields and not {f for f in fields if f.startswith("gemini")}
