from altruist_tester.parsers.upload_events import (
    UploadStatusStreamParser,
    parse_upload_event,
)


def test_parse_connectivity_upload_lines():
    event = parse_upload_event("[123] [INFO] [Map#42] Send attempt")

    assert event.as_event_payload() == {
        "channel": "connectivity",
        "status": "attempt",
        "sequence": 42,
        "target": None,
        "reason": None,
    }
    assert (
        parse_upload_event(
            "[123] [INFO] [Map#42] POST to 1.connectivity.robonomics.network:65/"
        ).target
        == "1.connectivity.robonomics.network:65/"
    )
    assert (
        parse_upload_event(
            "[123] [INFO] [Map#42] OK, POST succeeded -> "
            "1.connectivity.robonomics.network"
        ).status
        == "success"
    )
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


def test_parse_current_datalog_extrinsic_lines():
    attempt = parse_upload_event("Extrinsic Datalog: size 199")
    success = parse_upload_event(
        'Extrinsic result: "0x848cc48cd5d47200d08f3212976018e3e98eaf"'
    )

    assert attempt is not None
    assert attempt.as_event_payload() == {
        "channel": "datalog",
        "status": "attempt",
        "sequence": None,
        "target": None,
        "reason": None,
    }
    assert success is not None
    assert success.as_event_payload() == {
        "channel": "datalog",
        "status": "success",
        "sequence": None,
        "target": None,
        "reason": '"0x848cc48cd5d47200d08f3212976018e3e98eaf"',
    }


def test_parse_upload_event_ignores_unrelated_lines():
    assert parse_upload_event("Status: ALIVE") is None


def test_status_stream_parser_counts_datalog_send_increases():
    parser = UploadStatusStreamParser()

    first_snapshot = [
        "API Name: Robonomics Datalog",
        "  Count Sends: 0",
        "  Last Send Time: Thu Jan  1 00:00:00 1970",
        "  Is OK: Yes",
    ]
    for line in first_snapshot:
        assert parser.feed(line) == ()

    events = []
    for line in [
        "API Name: Robonomics Datalog",
        "  Count Sends: 2",
        "  Last Send Time: Mon Jun 22 10:20:31 2026",
        "  Is OK: Yes",
    ]:
        events.extend(parser.feed(line))

    assert [event.as_event_payload() for event in events] == [
        {
            "channel": "datalog",
            "status": "success",
            "sequence": 1,
            "target": None,
            "reason": "API status Count Sends=2, Is OK=Yes",
        },
        {
            "channel": "datalog",
            "status": "success",
            "sequence": 2,
            "target": None,
            "reason": "API status Count Sends=2, Is OK=Yes",
        },
    ]


def test_status_stream_parser_ignores_unchanged_datalog_count():
    parser = UploadStatusStreamParser()
    lines = [
        "API Name: Robonomics Datalog",
        "  Count Sends: 5",
        "  Is OK: Yes",
        "API Name: Robonomics Datalog",
        "  Count Sends: 5",
        "  Is OK: Yes",
    ]

    events = [event for line in lines for event in parser.feed(line)]

    assert events == []


def test_status_stream_parser_suppresses_explicit_datalog_outcome_duplicate():
    parser = UploadStatusStreamParser()
    for line in [
        "API Name: Robonomics Datalog",
        "  Count Sends: 0",
        "  Is OK: Yes",
    ]:
        parser.feed(line)

    explicit_event = parse_upload_event("[Datalog] OK, result: 0x123")
    parser.record_explicit_event(explicit_event)
    events = []
    for line in [
        "API Name: Robonomics Datalog",
        "  Count Sends: 1",
        "  Is OK: Yes",
    ]:
        events.extend(parser.feed(line))

    assert events == []


def test_status_stream_parser_ignores_map_status_blocks():
    parser = UploadStatusStreamParser()
    events = []
    for line in [
        "API Name: Robonomics Map",
        "  Count Sends: 1",
        "  Is OK: No",
    ]:
        events.extend(parser.feed(line))

    assert events == []
