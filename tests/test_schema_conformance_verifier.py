"""Unit-level: exercise SchemaConformanceVerifier directly. Unlike every
other verifier here, this one takes its ground truth (the schema map) as a
constructor argument rather than deriving it from disk/git/db -- proxy.py
populates that map from real tools/list responses; these tests populate it
by hand to isolate the check itself."""
from verimcp.verifiers.schema_conformance import SchemaConformanceVerifier

_USER_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    "required": ["id", "name"],
}


def _call_request(tool: str) -> dict:
    return {"params": {"name": tool, "arguments": {}}}


def _response(structured: dict | None, is_error: bool = False) -> dict:
    result: dict = {"isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return {"result": result}


def test_applies_only_to_tools_with_a_known_schema():
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    assert verifier.applies_to("make_user") is True
    assert verifier.applies_to("some_other_tool") is False


def test_passes_through_conforming_structured_content():
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    result = verifier.verify(_call_request("make_user"), _response({"id": 1, "name": "rudra"}))

    assert result["result"]["isError"] is False


def test_catches_a_type_mismatch():
    """The real-world lie this verifier exists for: a tool declares its own
    outputSchema, then returns something that violates it -- id as a string
    instead of the integer it promised."""
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    result = verifier.verify(_call_request("make_user"), _response({"id": "not-a-number", "name": "rudra"}))

    assert result["result"]["isError"] is True
    assert "does not conform" in result["result"]["content"][0]["text"]


def test_catches_a_missing_required_field():
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    result = verifier.verify(_call_request("make_user"), _response({"id": 1}))

    assert result["result"]["isError"] is True


def test_catches_missing_structured_content_when_schema_declared():
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    result = verifier.verify(_call_request("make_user"), _response(None))

    assert result["result"]["isError"] is True
    assert "no structuredContent" in result["result"]["content"][0]["text"]


def test_skips_tools_with_no_declared_schema():
    verifier = SchemaConformanceVerifier({})

    result = verifier.verify(_call_request("write_file"), _response({"anything": "goes"}))

    assert result["result"]["isError"] is False


def test_skips_already_failed_responses():
    verifier = SchemaConformanceVerifier({"make_user": _USER_SCHEMA})

    result = verifier.verify(_call_request("make_user"), _response(None, is_error=True))

    assert result["result"]["isError"] is True  # unchanged, not re-diagnosed
