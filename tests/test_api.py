"""API tests hit endpoints that don't require the LLM (no network)."""


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Oli" in r.text


def test_conversation_crud(client):
    created = client.post("/api/conversations").json()
    cid = created["id"]
    assert client.get(f"/api/conversations/{cid}").status_code == 200

    listing = client.get("/api/conversations").json()
    assert any(c["id"] == cid for c in listing)

    assert client.delete(f"/api/conversations/{cid}").json()["ok"] is True


def test_memory_add_dedupe_delete(client):
    a = client.post("/api/memories", json={"content": "The user lives in Bangalore"}).json()
    assert a["stored"] is True

    dup = client.post("/api/memories", json={"content": "The user resides in Bangalore"}).json()
    assert dup["stored"] is False

    items = client.get("/api/memories").json()
    assert len(items) == 1
    assert client.delete(f"/api/memories/{a['id']}").json()["ok"] is True
    assert client.get("/api/memories").json() == []


def test_scheduled_task_crud(client):
    created = client.post(
        "/api/tasks",
        json={
            "title": "Briefing",
            "prompt": "summarize news",
            "schedule_kind": "daily",
            "time_of_day": "07:00",
        },
    ).json()
    tid = created["id"]
    tasks = client.get("/api/tasks").json()
    assert any(t["id"] == tid and t["schedule_kind"] == "daily" for t in tasks)

    client.post(f"/api/tasks/{tid}/toggle?enabled=false")
    tasks = client.get("/api/tasks").json()
    assert next(t for t in tasks if t["id"] == tid)["enabled"] == 0

    assert client.delete(f"/api/tasks/{tid}").json()["ok"] is True


def test_chat_empty_message_rejected(client):
    # 400 before any LLM call — keeps the test offline.
    r = client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400


def test_invalid_schedule_kind_rejected(client):
    r = client.post(
        "/api/tasks",
        json={"title": "x", "prompt": "y", "schedule_kind": "hourly"},
    )
    assert r.status_code == 400
