def _create(client, h, title="The Lost Kingdom"):
    r = client.post("/api/projects", json={"title": title, "genre": "Fantasy"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_project_crud(client, make_user):
    h, _ = make_user()
    p = _create(client, h)
    assert p["counts"]["characters"] == 0
    assert client.patch(f"/api/projects/{p['id']}", json={"title": "Renamed"}, headers=h).json()["title"] == "Renamed"
    assert [x["title"] for x in client.get("/api/projects", headers=h).json()] == ["Renamed"]
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 204
    assert client.get(f"/api/projects/{p['id']}", headers=h).status_code == 404


def test_blank_title_rejected(client, make_user):
    h, _ = make_user()
    assert client.post("/api/projects", json={"title": "   "}, headers=h).status_code == 422
    assert client.post("/api/projects", json={"title": ""}, headers=h).status_code == 422


def test_recent_projects_ordered_by_update(client, make_user):
    h, _ = make_user()
    a = _create(client, h, "A")
    _create(client, h, "B")
    client.patch(f"/api/projects/{a['id']}", json={"description": "touch"}, headers=h)
    assert [p["title"] for p in client.get("/api/projects", headers=h).json()] == ["A", "B"]


def test_user_isolation(client, make_user):
    h1, _ = make_user("a@example.com")
    h2, _ = make_user("b@example.com")
    p = _create(client, h1)
    pid = p["id"]
    assert client.get("/api/projects", headers=h2).json() == []
    for method, url in [("get", f"/api/projects/{pid}"), ("delete", f"/api/projects/{pid}"),
                        ("get", f"/api/projects/{pid}/characters"), ("get", f"/api/projects/{pid}/references"),
                        ("get", f"/api/projects/{pid}/assets")]:
        assert getattr(client, method)(url, headers=h2).status_code == 404, url
    assert client.patch(f"/api/projects/{pid}", json={"title": "hax"}, headers=h2).status_code == 404
    assert client.post(f"/api/projects/{pid}/characters", json={"name": "X"}, headers=h2).status_code == 404
    assert client.get(f"/api/projects/{pid}", headers=h1).json()["title"] == "The Lost Kingdom"


def test_characters_crud(client, make_user):
    h, _ = make_user()
    pid = _create(client, h)["id"]
    c = client.post(f"/api/projects/{pid}/characters", json={"name": "Aria", "age": "24", "appearance": "tall"}, headers=h).json()
    assert client.put(f"/api/projects/{pid}/characters/{c['id']}", json={"name": "Aria B"}, headers=h).json()["name"] == "Aria B"
    assert len(client.get(f"/api/projects/{pid}/characters", headers=h).json()) == 1
    assert client.get(f"/api/projects/{pid}", headers=h).json()["counts"]["characters"] == 1
    assert client.delete(f"/api/projects/{pid}/characters/{c['id']}", headers=h).status_code == 204
    assert client.get(f"/api/projects/{pid}/characters", headers=h).json() == []


def test_timestamps_are_timezone_aware_utc(client, make_user):
    h, _ = make_user()
    p = _create(client, h)
    assert p["created_at"].endswith("Z") or p["created_at"].endswith("+00:00")
