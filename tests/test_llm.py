from oli.llm import parse_arguments


def test_parse_valid_json():
    assert parse_arguments('{"query": "hello"}') == {"query": "hello"}


def test_parse_empty_returns_empty_dict():
    assert parse_arguments("") == {}


def test_parse_malformed_returns_empty_dict():
    assert parse_arguments("not json") == {}
