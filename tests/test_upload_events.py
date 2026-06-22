from altruist_tester.parsers.upload_events import parse_upload_event


def test_parse_connectivity_upload_lines():
    event = parse_upload_event("[123] [INFO] [Map#42] Send attempt")

    assert event.as_event_payload() == {
        "channel": "connectivity",
        "status": "attempt",
        "sequence": 42,
        "target": None,
        "reason": None,
    }
    assert parse_upload_event(
        "[123] [INFO] [Map#42] POST to 1.connectivity.robonomics.network:65/"
    ).target == "1.connectivity.robonomics.network:65/"
    assert parse_upload_event(
        "[123] [INFO] [Map#42] OK, POST succeeded -> 1.connectivity.robonomics.network"
    ).status == "success"
    assert (
        parse_upload_event("[ERROR] [Map] FAILED: server returned HTTP error").reason
        == "server returned HTTP error"
    )


def test_parse_datalog_upload_lines():
    assert parse_upload_event("[Datalog] Sending: h:45,t:24").status == "attempt"
    assert parse_upload_event("[Datalog] OK, result: 0x123").status == "success"
    assert parse_upload_event("[Datalog] FAILED").status == "failure"
    assert (
        parse_upload_event("[Datalog] WARNING: data string is empty").reason
        == "data string is empty"
    )


def test_parse_upload_event_ignores_unrelated_lines():
    assert parse_upload_event("Status: ALIVE") is None
