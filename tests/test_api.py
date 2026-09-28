"""API tests hit endpoints that don't require the LLM (no network)."""


async def test_index_served(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Oli" in r.text


async def test_conversation_crud(client):
    created = (await client.post("/api/conversations")).json()
    cid = created["id"]
    assert (await client.get(f"/api/conversations/{cid}")).status_code == 200

    listing = (await client.get("/api/conversations")).json()
    assert any(c["id"] == cid for c in listing)

    assert (await client.delete(f"/api/conversations/{cid}")).json()["ok"] is True


async def test_memory_add_dedupe_delete(client):
    a = (await client.post("/api/memories", json={"content": "The user lives in Bangalore"})).json()
    assert a["stored"] is True

    dup = (
        await client.post("/api/memories", json={"content": "The user resides in Bangalore"})
    ).json()
    assert dup["stored"] is False

    items = (await client.get("/api/memories")).json()
    assert len(items) == 1
    assert (await client.delete(f"/api/memories/{a['id']}")).json()["ok"] is True
    assert (await client.get("/api/memories")).json() == []


async def test_chat_empty_message_rejected(client):
    # 400 before any LLM call — keeps the test offline.
    r = await client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400
