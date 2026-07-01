from pathlib import Path

from altruist_tester.parsers.sensor_values import parse_sensor_values

FIXTURE = Path(__file__).parent / "fixtures" / "dev_serial_with_sensor_values.log"


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


def test_parse_compact_datalog_line_from_fixture():
    line = next(
        line
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if "Datalog data:" in line
    )

    samples = parse_sensor_values(line)

    assert [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ] == [
        ("datalog", "humidity", 65.99, "%"),
        ("datalog", "temperature", 25.51, "°C"),
        ("datalog", "pressure", 101069.09, "Pa"),
        ("datalog", "noiseMax", 83.0, "dB"),
        ("datalog", "noiseAvg", 81.0, "dB"),
        ("datalog", "P1", 16.33, "ppm"),
        ("datalog", "P2", 7.33, "ppm"),
    ]
    assert {sample.source for sample in samples} == {"serial_datalog"}


def test_parse_datalog_line_supports_all_firmware_aliases():
    line = "Datalog data: : gc:1.5,co2:612,co:0.1,o3:0.2,no2:0.3,fa:42,ea:55"

    samples = parse_sensor_values(line)

    assert [(sample.metric, sample.value, sample.unit) for sample in samples] == [
        ("radiation", 1.5, "µR/h"),
        ("co2", 612.0, "ppm"),
        ("co", 0.1, "ppm"),
        ("o3", 0.2, "ppm"),
        ("no2", 0.3, "ppm"),
        ("fast_aqi", 42.0, None),
        ("epa_aqi", 55.0, None),
    ]


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
