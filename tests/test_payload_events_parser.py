from altruist_tester.parsers.payload_events import parse_payload_observation


def test_parse_payload_observation_metadata():
    observation = parse_payload_observation(
        "[123] [INFO] [PAYLOAD] channel=datalog encoding=cps encrypted=1 "
        "payload_len=324 sample_available=0"
    )

    assert observation is not None
    assert observation.as_event_payload() == {
        "channel": "datalog",
        "encoding": "cps",
        "encrypted": True,
        "payload_len": 324,
        "sample_available": False,
        "raw_fields": {
            "channel": "datalog",
            "encoding": "cps",
            "encrypted": "1",
            "payload_len": "324",
            "sample_available": "0",
        },
    }


def test_payload_observation_event_payload_does_not_store_sample():
    observation = parse_payload_observation(
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=49 "
        "sample_available=1 sample=h:65.15,t:25.84"
    )

    assert observation is not None
    assert observation.sample_available is True
    assert observation.as_event_payload()["raw_fields"] == {
        "channel": "datalog",
        "encoding": "plain",
        "encrypted": "0",
        "payload_len": "49",
        "sample_available": "1",
    }


def test_payload_observation_ignores_unrelated_lines():
    assert parse_payload_observation("[DATALOG] success response_len=66") is None
