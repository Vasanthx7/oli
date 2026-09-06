async def test_conversation_and_message_crud(storage):
    cid = await storage.create_conversation("Test chat")
    assert await storage.conversation_exists(cid)

    await storage.add_message(cid, "user", "hello")
    await storage.add_message(cid, "assistant", "hi there")
    msgs = await storage.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello"


async def test_rename_and_list(storage):
    cid = await storage.create_conversation()
    await storage.rename_conversation(cid, "Renamed")
    convos = await storage.list_conversations()
    assert any(c["id"] == cid and c["title"] == "Renamed" for c in convos)


async def test_delete_cascades_messages(storage):
    cid = await storage.create_conversation()
    await storage.add_message(cid, "user", "x")
    await storage.delete_conversation(cid)
    assert not await storage.conversation_exists(cid)
    assert await storage.get_messages(cid) == []


async def test_ensure_conversation_is_idempotent(storage):
    await storage.ensure_conversation("fixed-id", "Task thread")
    await storage.ensure_conversation("fixed-id", "Task thread")
    assert await storage.conversation_exists("fixed-id")


async def test_notifications_unread_flow(storage):
    await storage.add_notification("Briefing", "content", status="ok")
    assert await storage.unread_count() == 1
    await storage.mark_notifications_read()
    assert await storage.unread_count() == 0
