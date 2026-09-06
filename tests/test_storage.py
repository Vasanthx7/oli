def test_conversation_and_message_crud(storage):
    cid = storage.create_conversation("Test chat")
    assert storage.conversation_exists(cid)

    storage.add_message(cid, "user", "hello")
    storage.add_message(cid, "assistant", "hi there")
    msgs = storage.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello"


def test_rename_and_list(storage):
    cid = storage.create_conversation()
    storage.rename_conversation(cid, "Renamed")
    convos = storage.list_conversations()
    assert any(c["id"] == cid and c["title"] == "Renamed" for c in convos)


def test_delete_cascades_messages(storage):
    cid = storage.create_conversation()
    storage.add_message(cid, "user", "x")
    storage.delete_conversation(cid)
    assert not storage.conversation_exists(cid)
    assert storage.get_messages(cid) == []


def test_notifications_unread_flow(storage):
    storage.add_notification("Briefing", "content", status="ok")
    assert storage.unread_count() == 1
    storage.mark_notifications_read()
    assert storage.unread_count() == 0
