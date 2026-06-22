# Altruist Tester

Python burn-in tester for assembled Altruist devices.

The current workflow tests one device connected over USB-C serial to a Linux
host, Raspberry Pi, or development machine. The tester captures raw firmware
logs, parses development metrics and sensor values, evaluates health rules, and
writes machine-readable and human-readable run artifacts.

## Quick Start

Install dependencies:

```bash
uv sync
```

List detected USB serial ports:

```bash
uv run altruist-tester ports
```

When USB metadata is available, the command also prints the USB serial number
and normalized `device_id`. For ESP32-C6 Altruist devices this usually matches
the firmware `ChipId`/MAC without separators.

Run a short test using the only detected serial port:

```bash
uv run altruist-tester run --auto --duration 10m
```

Run against an explicit port:

```bash
uv run altruist-tester run --port /dev/ttyACM0 --duration 10m
```

Use `--output-dir` to write artifacts somewhere other than `runs/`:

```bash
uv run altruist-tester run --auto --duration 24h --output-dir /data/altruist-runs
```

For the first real 24-hour Raspberry Pi burn-in run, use the operational
checklist in [docs/raspberry-pi-24h-runbook.md](docs/raspberry-pi-24h-runbook.md).
It covers serial-port selection, `tmux`, live checks, and post-run inspection.

## Device Profiles

Use a profile when testing a known Altruist build. Profiles define expected
sensors and rule thresholds.

Urban:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/urban.example.toml
```

The Urban profile expects:

- `bme280`: temperature, humidity, pressure;
- `sds`: PM10 and PM2.5;
- `ics-43434`: average and max noise.

Insight:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/insight.example.toml
```

The Insight profile expects:

- `scd41`: CO2, temperature, humidity;
- `bme680`: temperature, humidity, pressure.

The firmware currently prints the SCD sensor as `SCD4x`; the tester accepts
`scd41`, `scd40`, and `scd4x` as the same expected sensor preset.

For ad-hoc checks, pass expectations directly:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-sensor bme280 \
  --expect-sensor sds \
  --expect-sensor ics-43434
```

You can also require individual metrics:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-metric temperature \
  --expect-metric humidity \
  --expect-metric pressure
```

`--expect-sensor` and `--expect-metric` can be repeated and can be combined with
`--config`. CLI expectations are added to expectations from the config file.

If no expectations are configured, the run can still complete, but
`summary.json` records a warning because the tester cannot know which sensor
metrics are mandatory for that device.

## Device Identity

The tester records device identity even for a single-device run. It uses several
sources when available:

- USB metadata from pyserial, especially the USB serial number;
- `/dev/serial/by-id/...` names, which often include the ESP MAC;
- firmware serial lines such as `ChipId: ...`;
- JSON payload lines with `sensor_id`, when they appear in UART logs.

The final `summary.json` contains a `device_identity` object with the normalized
`device_id`, colon-formatted `mac`, source values, stable `by-id`/`by-path`
links, and any conflicts between sources. `report.txt` includes the same
identity in a compact `Device` section.

For multi-device stands, use `/dev/serial/by-path/...` for physical slot mapping
and let the tester derive the device identity from USB metadata and firmware
logs. Manual MAC lists should be a fallback, not the main workflow.

## Health Checks

The tester evaluates all health checks through one rules engine. The final
verdict is written as:

- `PASS_CANDIDATE`: no warnings or failures;
- `WARN`: diagnostics need attention, but the command exits successfully;
- `FAIL`: one or more checks failed, and the command exits with code `1`.

Presence:

- checks that all expected metrics were observed at least once;
- normalizes firmware aliases for presence checks, for example `P1` as `pm10`,
  `P2` as `pm25`, `noiseAvg` as `noise_avg`, and `noiseMax` as `noise_max`.

Sensor ranges:

- checks parsed values against sane default or configured ranges;
- normalizes pressure emitted as `Pa` or Pa-like `hPa` into `hPa` before range
  checks.

Flatline detection:

- checks each `(sensor, metric)` series for enough value variation;
- short or inconclusive flatlines are warnings;
- flatlines lasting `flatline.fail_after` fail the run.

Update cadence:

- checks whether each parsed metric updates regularly;
- by default, the expected interval is 5 minutes;
- warnings and failures are configured as missed-interval multipliers.

Runtime counters:

- checks that development-metrics uptime does not decrease;
- checks that the boot counter does not increase during the run;
- records an initial boot counter greater than `1`, but does not fail the run
  for that alone.

Serial silence:

- warns when serial output is silent for too long;
- fails when silence reaches the configured failure threshold;
- reports a dedicated finding when no serial lines appear at all.

Keyword alerts:

- watches raw serial lines for panic, watchdog, brownout, unexpected restart,
  CPU lock-up, power glitch, eFuse error, assertion, stack, heap, and access
  fault patterns.

Upload delivery:

- parses Robonomics Map/connectivity upload attempts, successes, failures,
  skipped sends, targets, and failure reasons;
- parses Robonomics Datalog success and failure lines when that firmware API is
  enabled and logs them;
- also reads Datalog delivery from development `Device Status` blocks by
  tracking `Robonomics Datalog` `Count Sends` increases and `Is OK`;
- checks upload delivery only when the config marks a channel as `optional` or
  `required`;
- keeps channels `disabled` by default because many devices are tested before
  `setDevices` or Robonomics subscription access is configured.

## Run Artifacts

Each run creates a directory under `runs/` by default:

```text
runs/<timestamp>_<port>/
  serial.log
  events.jsonl
  samples.jsonl
  summary.json
  report.txt
```

- `serial.log` is the raw UART capture. Keep it as the source of truth when
firmware output or parsers need investigation.

- `summary.json` is the main machine-readable result. It contains the verdict,
top-level findings, counters, and per-rule sections:
  - `rules`;
  - `sensor_presence`;
  - `sensor_ranges`;
  - `sensor_flatlines`;
  - `sensor_cadence`;
  - `runtime_counters`;
  - `serial_silence`;
  - `upload_health`;
  - `device_identity`.

- `report.txt` is the human-readable run report for quick inspection over SSH or
for pasting into an issue or chat.

- `samples.jsonl` contains parsed sensor samples with tester-side timestamps. It
is useful for graphs, cadence analysis, flatline debugging, and parser checks.

- `events.jsonl` contains chronological tester events, parsed development metrics,
keyword alerts, upload observations, and identity observations.

While a run is active, the CLI prints live progress with elapsed time, serial
line and byte counters, current serial silence, parsed dev metrics, parsed
sensor samples, and keyword alert count.

## Config Files

`--config` loads a TOML tester profile. Supported sections:

- `[expect]` for required `sensors` and `metrics`;
- `[sensor_ranges.<metric>]` for sane min/max values;
- `[range_checks]` for unknown metric behavior;
- `[flatline]` for stuck-value thresholds;
- `[cadence]` for update interval thresholds;
- `[serial]` for serial silence thresholds;
- `[uploads]` for Robonomics Map/connectivity and Datalog delivery checks.

Durations in config files use the same format as CLI durations: `30s`, `10m`,
`2h`, or raw seconds as a positive integer.

Example:

```toml
[expect]
sensors = ["bme280", "sds", "ics-43434"]
metrics = []

[flatline]
window = "30m"
fail_after = "1h"
min_distinct_values = 2

[cadence]
expected_interval = "5m"
warn_after_missed = 2
fail_after_missed = 4

[serial]
silence_warn_after = "2m"
silence_fail_after = "10m"

[uploads]
connectivity = "disabled" # disabled | optional | required
datalog = "disabled"      # disabled | optional | required

[uploads.connectivity_thresholds]
min_successes = 1
min_success_rate = 0.8
max_consecutive_failures = 5

[uploads.datalog_thresholds]
min_successes = 1
min_success_rate = 0.8
max_consecutive_failures = 3
```

Use `required` only when the device was provisioned for that channel:

- connectivity requires the device address to be added through Robonomics
  `setDevices`;
- datalog requires an active Robonomics subscription and the device address to
  be added to it.

Use `optional` when you want upload statistics and warnings without failing the
whole burn-in run.

## Batch Config

`configs/batch.usb.example.toml` describes several USB-connected devices for a
future batch run. The format is intentionally separate from the single-device
tester profile: the batch config maps physical USB slots to ports and profiles,
while `configs/urban.example.toml` and `configs/insight.example.toml` keep the
actual health rules for each device type.

Example:

```toml
[batch]
duration = "24h"
baud = 115200
output_dir = "runs"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/pci-0000:00:14.0-usb-0:1.2:1.0"
config = "urban.example.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/pci-0000:00:14.0-usb-0:1.1:1.0"
config = "insight.example.toml"
```

Relative device `config` paths are resolved from the directory that contains the
batch TOML file. For mixed batches, such as Urban and Insight devices connected
to the same Raspberry Pi, set `config` explicitly for every device. The shared
`[batch].device_config` field is only a fallback for homogeneous batches where
all slots use the same tester profile.

Batch config validation rejects:

- empty or duplicate `slot` values;
- duplicate `port` values;
- missing referenced tester profile files;
- devices without an effective profile config;
- unknown `model` values;
- mixed Urban and Insight batches that rely on one shared
  `[batch].device_config`.

Preview a batch setup before running real burn-in tests:

```bash
uv run altruist-tester batch --config configs/batch.usb.example.toml --dry-run
```

Dry-run validates the config and prints the duration, output directory, slots,
ports, models, effective tester profiles, port presence, and USB identity when
metadata is available. It does not open serial ports and does not create run
artifacts.

## Firmware Notes

- The default baud rate is `115200`.
- Configure Wi-Fi before using a run as a burn-in signal.
- Map or datalog HTTP failures can appear in `serial.log`. They are parsed as
  upload observations and checked according to the `[uploads]` config, but they
  are not treated as keyword-alert runtime failures.

## Exit Codes

- `0`: run completed without `FAIL` findings;
- `1`: run completed and health checks produced `FAIL`;
- `2`: CLI usage, missing port, config error, or serial-open error.

## Development

```bash
uv sync
uv run altruist-tester --help
uv run altruist-tester run --help
uv run python -m pytest
```
