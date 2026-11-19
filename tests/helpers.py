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


def uart_settings(sensor):
    return dict(
        fetch_wait_ms=100,
        measurement_interval_ms=None if sensor == "ICS43434" else 300000,
        publication="coalesced" if sensor == "ICS43434" else "on_update",
        publish_interval_ms=30000 if sensor == "ICS43434" else 0,
        state_min_interval_ms=1000,
        gas_enabled=True if sensor == "BME680" else None,
        sds_warmup_ms=15000 if sensor in {"SDS", "ICS43434"} else None,
        sds_collect_ms=5000 if sensor in {"SDS", "ICS43434"} else None,
        noise_window_active_ms=300000 if sensor == "ICS43434" else None,
    )


def uart_snapshot(sensor, values):
    """A complete synthetic v2 snapshot, including unmeasured required metrics."""
    from altruist_tester.sensor_contract import METRICS

    channel = dict(
        cycles_total=1,
        ok_total=1,
        failed_total=0,
        last_ok_ms=1000,
        last_failed_ms=None,
        last_recovered_ms=None,
        state="ok",
        reason="none",
    )
    return {
        "_observations": dict(
            version=2,
            device_id="1051DB010C70",
            boot_id="a-b",
            model="urban",
            sensor=sensor,
            snapshot_seq=1,
            time_ms=1000,
            cycle_seq=1,
            state="ok",
            reason="none",
            settings=uart_settings(sensor),
            channels={
                name: dict(channel)
                for name in (
                    ("environment", "gas") if sensor == "BME680" else ("main",)
                )
            },
            failures=dict(next_seq=1, recent=[]),
        ),
        sensor: {
            metric: dict(
                value=values.get(metric),
                observation=dict(
                    seq=1 if metric in values else 0,
                    measured_ms=1000 if metric in values else None,
                    state="ok" if metric in values else "never",
                ),
            )
            for metric in sorted(METRICS[sensor])
        },
    }
