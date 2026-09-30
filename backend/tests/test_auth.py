from app.security import sanitize_filename


def test_register_login_me(client, make_user):
    h, user = make_user()
    assert user["role"] == "USER"
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "password123"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).json()["email"] == "user@example.com"


def test_duplicate_and_bad_password(client, make_user):
    make_user()
    assert client.post("/api/auth/register", json={"email": "user@example.com", "password": "password123"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrongwrong"}).status_code == 401
    assert client.post("/api/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422


def test_protected_routes_require_token(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_admin_email_gets_admin_role(client, make_user):
    _, user = make_user("boss@example.com")
    assert user["role"] == "ADMIN"


def test_password_reset_flow(client, make_user):
    from app.security import create_token
    from app.db import SessionLocal
    from app.models import User
    make_user()
    with SessionLocal() as db:
        u = db.query(User).one()
        token = create_token(u.id, "reset", minutes=5, extra={"h": u.password_hash[-12:]})
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "newpassword1"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "user@example.com", "password": "newpassword1"}).status_code == 200
    # token is single-use because the hash changed
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "another-pass1"}).status_code == 400


def test_access_token_cannot_be_used_as_reset_token(client, make_user):
    h, _ = make_user()
    tok = h["Authorization"].split()[1]
    assert client.post("/api/auth/reset-password", json={"token": tok, "password": "newpassword1"}).status_code == 400


def test_disabled_user_is_locked_out(client, make_user):
    admin_h, _ = make_user("boss@example.com")
    h, u = make_user()
    assert client.patch(f"/api/admin/users/{u['id']}", json={"is_active": False}, headers=admin_h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("my pic (1).png") == "my_pic_1_.png"
    assert sanitize_filename("") == "file"
