import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="dreamcast-test-")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "STORAGE_DIR": f"{_tmp}/storage",
    "AUTH_PROVIDER": "local",
    "AUTH_SECRET_KEY": "test-secret-key-test-secret-key-1234",
    "ADMIN_EMAILS": "boss@example.com",
    "RATE_LIMIT_AUTH_PER_MINUTE": "0",
    "REFINE_RATE_LIMIT_PER_MINUTE": "0",
    "WORKER_ENABLED": "false",          # tests drive the runner directly (deterministic, no threads)
    "ENABLE_DEV_SIMULATOR": "true",
    "DEV_SIMULATOR_STEP_SECONDS": "0",
    "PROVIDER_POLL_SECONDS": "0",
    "JOB_RETRY_DELAY_SECONDS": "0",
    "LLM_API_KEY": "",
    "LLM_PROVIDER": "groq",
    "VIDEO_MAX_SECONDS": "30",        # default provider limit for tests; tests override it to check provider caps
    "VIDEO_PROVIDER_API_KEY": "",
    "FACE_PROVIDER_API_KEY": "",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

# 1x1 PNG
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082")


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_user(client):
    def _make(email="user@example.com", password="password123", name="Test"):
        r = client.post("/api/auth/register", json={"email": email, "password": password, "name": name})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]
    return _make
