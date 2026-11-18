from datetime import UTC, datetime, timedelta

from altruist_tester.samples import SensorSampleRecord, SensorSampleSeries


def sample_record(
    sensor: str,
    metric: str,
    value: float,
    offset_seconds: int,
) -> SensorSampleRecord:
    ts = datetime(2026, 6, 5, 12, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    return SensorSampleRecord(
        ts=ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        sensor=sensor,
        metric=metric,
        value=value,
        unit=None,
        source="serial",
    )


def sample_series(*records: SensorSampleRecord) -> SensorSampleSeries:
    series = SensorSampleSeries()
    for record in records:
        series.append(record)
    return series


def uart_snapshot(sensor, values):
    """A current sensor UART snapshot with successful per-metric observations."""
    return {
        "_observations": {
            "version": 1,
            "device_id": "1051DB010C70",
            "boot_id": "a-b",
            "model": "urban",
            "sensor": sensor,
            "snapshot_seq": "1",
            "time_ms": "1000",
            "state": "ok",
            "errors": 0,
            "reason": "none",
        },
        sensor: {
            metric: {
                "value": value,
                "observation": {"seq": "1", "measured_ms": "1000", "state": "ok"},
            }
            for metric, value in values.items()
        },
    }
