from pathlib import Path

from altruist_tester.parsers.payload_events import parse_payload_metadata
from altruist_tester.parsers.sensor_values import parse_sensor_values

FIXTURE = Path(__file__).parent / "fixtures" / "sensor_json_snapshots.log"


def test_parse_sensor_json_snapshot_from_fixture():
    line = next(line for line in FIXTURE.read_text(encoding="utf-8").splitlines())

    samples = parse_sensor_values(line)

    assert [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ] == [
        ("BME280", "humidity", 65.98633, "%"),
        ("BME280", "temperature", 25.51055, "°C"),
        ("BME280", "pressure", 101069.1, "hPa"),
        ("ICS43434", "noiseMax", 84.0, "dB"),
        ("ICS43434", "noiseAvg", 81.0, "dB"),
        ("SDS", "P1", 16.33125, "ppm"),
        ("SDS", "P2", 7.33125, "ppm"),
    ]
    assert {sample.source for sample in samples} == {"serial"}


def test_payload_aliases_do_not_create_measurements():
    line = (
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=48 "
        "sample=gc:1.5,co2:612,co:0.1,o3:0.2,no2:0.3,"
        "fa:42,ea:55"
    )

    assert parse_sensor_values(line) == []
    assert parse_payload_metadata(line)["channel"] in {
        "datalog",
        "sensors-connectivity",
    }


def test_parse_payload_metadata_after_uart_log_prefix():
    line = (
        "[123] [INFO] [PAYLOAD] channel=datalog encoding=plain encrypted=0 "
        "payload_len=15 sample=h:65.15,t:25.84"
    )

    fields = parse_payload_metadata(line)

    assert fields == {
        "channel": "datalog",
        "encoding": "plain",
        "encrypted": "0",
        "payload_len": "15",
        "sample": "h:65.15,t:25.84",
    }


def test_plain_payload_keeps_metadata_without_measurements():
    line = (
        "[PAYLOAD] channel=sensors-connectivity encoding=plain encrypted=0 "
        "payload_len=26 sample=h:65.15,t:25.84,p:99860.91"
    )

    assert parse_sensor_values(line) == []
    assert parse_payload_metadata(line)["channel"] in {
        "datalog",
        "sensors-connectivity",
    }


def test_parse_payload_ignores_encrypted_payload_without_sample():
    line = "[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324 "

    assert parse_sensor_values(line) == []


def test_encrypted_payload_with_plain_sample_is_not_a_measurement():
    line = (
        "[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324 "
        "sample=h:65.15,t:25.84,p1:eJxEncrypted,p2:7.37"
    )

    assert parse_sensor_values(line) == []
    assert parse_payload_metadata(line)["channel"] in {
        "datalog",
        "sensors-connectivity",
    }


def test_payload_with_service_time_is_not_a_measurement():
    line = (
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=38 "
        "sample=h:65.15,t:25.84,time:17833545,p1:16.33"
    )

    assert parse_sensor_values(line) == []
    assert parse_payload_metadata(line)["channel"] in {
        "datalog",
        "sensors-connectivity",
    }


def test_datalog_payload_is_not_a_measurement_fallback():
    line = (
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=15 "
        "sample=h:65.15,t:25.84"
    )

    assert parse_sensor_values(line) == []
    assert parse_payload_metadata(line)["channel"] in {
        "datalog",
        "sensors-connectivity",
    }


def test_parse_truncated_plain_payload_sample_is_ignored():
    line = (
        "[PAYLOAD] channel=sensors-connectivity encoding=plain encrypted=0 "
        "payload_len=54 "
        "sample=h:56.17,t:27.66,p:9967,nm:44,na:42,p1:5.80,p2:4.00"
    )

    assert parse_sensor_values(line) == []


def test_parse_non_contract_payload_channels_do_not_create_sensor_samples():
    line = (
        "[PAYLOAD] channel=robonomics-map encoding=mixed encrypted=1 "
        "payload_len=280 sample=h:65.15,t:25.84"
    )
    custom_http_line = (
        "[PAYLOAD] channel=custom-http encoding=plain encrypted=0 "
        "payload_len=28 sample=h:65.15,t:25.84"
    )

    assert parse_sensor_values(line) == []
    assert parse_sensor_values(custom_http_line) == []


def test_parse_non_contract_payload_lines_do_not_create_sensor_samples():
    non_contract_lines = [
        "Message to sign: h:59.46,t:25.32,p1:0.00,p2:0.00,time:17833545",
        "Datalog data: : h:65.99,t:25.51,p1:16.33",
    ]

    assert [parse_sensor_values(line) for line in non_contract_lines] == [[]] * len(
        non_contract_lines
    )


def test_parse_sensor_json_snapshot_with_uart_text_around_it():
    line = (
        '[123] [INFO] prefix {"SCD4x":{"co2":{"value":612,'
        '"intl_name":"CO2","units":"ppm"}}}[124] [INFO] suffix'
    )

    samples = parse_sensor_values(line)

    parsed = [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ]
    assert parsed == [
        ("SCD4x", "co2", 612.0, "ppm"),
    ]


def test_parse_concatenated_sensor_json_snapshots():
    line = (
        '{"SCD4x":{"co2":{"value":612,"units":"ppm"}}}'
        '{"BME680":{"pressure":{"value":101325,"units":"Pa"}}}'
    )

    samples = parse_sensor_values(line)

    parsed = [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ]
    assert parsed == [
        ("SCD4x", "co2", 612.0, "ppm"),
        ("BME680", "pressure", 101325.0, "Pa"),
    ]


def test_parse_sensor_values_ignores_non_sensor_lines_and_bad_json():
    assert parse_sensor_values("=== [URBAN] METRICS ===") == []
    assert parse_sensor_values('{"service_data":{"signal_strength":-40}}') == []
    assert parse_sensor_values('{"BME280":{"temperature":{"value":"bad"}}}') == []
    assert parse_sensor_values('{"BME280":{"temperature":{"value":true}}}') == []
    assert parse_sensor_values("{not json") == []
