# Altruist Tester

Python burn-in tester for assembled Altruist devices.

The single-device workflow tests one device connected over USB-C serial to a
Linux host, Raspberry Pi, or development machine. Batch mode runs the same
single-device worker for several USB-connected devices at once. The tester
captures raw firmware logs, parses health, build, payload, upload, and sensor
observations, evaluates health rules, and writes machine-readable and
human-readable artifacts.

USB sensor acceptance runs target **debug firmware**. Detailed UART tags,
sensor JSON and diagnostic SD files require `ALTRUIST_BUILD_DEBUG`. Release
retains a bounded operational log for local web support and the webflasher USB
console: BUILD identity, LOG status and curated errors/events, without sensor
values, payloads, response bodies or credentials.

The release log UI offers named Off / Errors only / Events modes; higher
runtime log levels cannot enable debug output. Debug also has Detailed / Most
detailed and hides modes below its forced minimum. The active mode is highlighted,
without a duplicate numeric input. Live changes do not reboot. Save and restart
persists the current mode and deliberately reboots. Status snapshot preserves
the mode and is available explicitly even with automatic logs off.

Testing is a publication channel, not a debug profile. Operational support logs do not satisfy the HEALTH/measurement requirements
of the `log_contract` rule. PASS on debug does not validate
release. See the firmware [profile policy](../altruist-firmware/DIAGNOSTICS.md).

USB acceptance checks the firmware contract after the complete capture, before
sensor rules. A BUILD `profile=release` exits **2** (unsuitable configuration), with no sensor verdict.
Debug with missing signals remains **FAIL / exit 1**. Missing
BUILD does not imply release: normal strict rules and the existing missing-BUILD
warning apply. Missing profile metadata also warns; unknown explicit profiles
or conflicting BUILD identity fields exit 2. Stable/Testing never selects a
profile. Late BUILD is used for this decision and retains the startup warning
when normal rules run. Prepare debug firmware before a new acceptance capture;
use the diagnostic procedures for release logging checks. Batch isolates workers,
continues other slots, and exits 2 if any worker has a configuration/serial error
(otherwise 1 for health FAIL, 0 without FAIL). A failed batch aggregate is an
execution result; consult each slot's worker error before diagnosing hardware.

Debug firmware builds with health telemetry emit a compact snapshot once per minute:

```text
[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 reset_reason=power_on_reset reset_code=1 crash_valid=0 prev_uptime=0 prev_heap=0 last_section_id=0 last_section=Idle/MainLoop
```

The tester parses the base runtime fields even if reset context is absent, but
the UART log contract warns when neither `[BOOT]` nor `[HEALTH]` exposes
reset context.

For safe extraction, bounded UART capture and separate HTTP/reboot checks, see
[diagnostic procedures](docs/diagnostic-procedures.md).

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

To catch boot-time `[BOOT]` and `[BUILD]` lines, start the tester before
plugging in or powering the device:

```bash
uv run altruist-tester run --auto --wait-port --duration 24h \
  --config configs/urban.example.toml \
  --device-model urban
```

`--wait-port` waits up to 2 minutes by default. Change that with
`--wait-port-timeout`, for example `--wait-port-timeout 5m`.
Use `--wait-port --wait-port-timeout infinite` to wait without a deadline;
Ctrl+C cancels the wait. Capture duration starts after the port opens.

Use `--output-dir` to write artifacts somewhere other than `runs/`:

```bash
uv run altruist-tester run --auto --duration 24h --output-dir /data/altruist-runs
```

For a 24-hour Raspberry Pi burn-in run, use the operational
checklist in [docs/raspberry-pi-24h-runbook.md](docs/raspberry-pi-24h-runbook.md).
It covers serial-port selection, `tmux`, live checks, and post-run inspection.

## Device Profiles

Use a profile when testing a known Altruist build. Profiles define expected
sensors and rule thresholds.

Urban:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/urban.example.toml \
  --device-model urban
```

The Urban profile expects:

- `bme280`: temperature, humidity, pressure;
- `sds`: PM10 and PM2.5;
- `ics-43434`: average and max noise.

Insight:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/insight.example.toml \
  --device-model insight
```

The Insight profile expects:

- `scd41`: CO2, temperature, humidity;
- `bme680`: temperature, humidity, pressure.

The firmware currently prints the SCD sensor as `SCD4x`; the tester accepts
`scd41`, `scd40`, and `scd4x` as the same expected sensor preset.

Physical sensor presence checks require every preset metric from that sensor's
own JSON object (with sensor/metric aliases normalized). For example, SCD4x
temperature/humidity and imported `altruist_urban` pressure cannot confirm BME680;
imported or aggregated readings cannot confirm Urban BME280. Missing pairs fail
acceptance and appear as `missing_sensor_metrics` in `sensor_presence` and in
the report message. `missing_metrics` lists globally absent metric names;
`missing_sensor_metrics` lists every unconfirmed physical sensor/metric pair.
Versioned `_observations` records are the only acceptance measurement source.
Explicit `expect.metrics` alone accepts measurements from any physical sensor;
an empty expectation set warns. Source labels do not prove hardware initialization.
Freshness and deduplication are checked separately.

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

`--device-model` is an explicit hint for model-specific checks, for example
Urban particulate-matter warnings. Profiles define expected sensors and
thresholds; the model hint says which physical Altruist variant is under test.

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
- flatlines lasting `flatline.fail_after` fail the run (selected all-zero
  environmental metrics warn);
- BME680 `voc_index`, `voc_level`, and `voc_spikes_today` do not require
  variation; their validity is checked by sensor ranges.

Update cadence:

- uses each sensor's explicit UART v2 profile interval T;
- slow BME/SCD/SDS measurements warn at gaps/ages >=T+60 seconds and fail at
  >=2T+300 seconds; first success warns at >=T+60 and fails at >=T+300;
- requires three distinct successful measurements per mandatory slow metric;
  one or two give coverage WARN, while explicit failures retain their severity;
- checks microphone blocks over complete 300-second active windows and checks
  audio gaps independently of average frequency.

Runtime counters:

- checks that parsed health/runtime uptime does not decrease;
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
  fault patterns;
- excludes only the two exact EPD maintenance messages documented in the
  [keyword contract](docs/sensor-log-patterns.md#runtime-keyword-alerts);
- includes real runtime keyword failures in summary/report findings and the
  final FAIL verdict (CLI exit 1), while preserving raw UART.

Map health GET probes select a server and do not count as upload attempts or
failures. `health_checks` and `health_check_reasons` preserve them separately
in summaries/reports; real POST/local failures retain their configured severity.
See the [health-probe and upload classification](docs/sensor-log-patterns.md#map-server-selection-and-upload-outcomes).

Upload delivery:

- parses stable `[CONNECTIVITY]` upload attempts, successes, failures, targets,
  and failure reasons;
- parses stable `[DATALOG]` upload attempts, successes, failures, payload
  metadata, response lengths, and local formatting/encryption failure reasons;
- checks upload delivery only when the config marks a channel as `optional` or
  `required`;
- keeps channels disabled unless the selected config enables them, because many
  devices are tested before `setDevices` or Robonomics subscription access is
  configured. The example Urban and Insight profiles set both channels to
  `optional`, so upload problems are reported as warnings.

Payload observations:

- parses stable `[PAYLOAD]` lines with `channel`, `encoding`, `encrypted`,
  `payload_len` metadata;
- records transport metadata without sensor measurement values.

Sensor measurements:

- require debug UART snapshots with `_observations` version 2;
- use successful measurement identity/time for freshness and deduplication;
- ignore ordinary sensor JSON and PAYLOAD samples during acceptance;
- fail with `SENSOR_OBSERVATION_MISSING` if the versioned stream is absent.

Full sensor JSON remains available to diagnostic extraction; see
[freshness and deduplication](#measurement-freshness-and-deduplication).

C6 Map defaults prefer protobuf using a built-in endpoint when the
custom URL is empty. A successful protobuf POST skips legacy JSON formatting
and its `[PAYLOAD]` metadata output, even with both Map toggles enabled.
Datalog observations depend on the configured route; local sensor UART
measurements require debug firmware. A release run with BUILD but
without readable measurements exits 2 as an unsuitable USB acceptance
configuration. `[PROTO]` is envelope-construction metadata and cannot substitute
for readings.

Current CONNECTIVITY statistics combine legacy JSON and protobuf HTTP results.
They cannot prove which route succeeded, CPS finalization, or frontend
visibility. Cloud HTTP `202` confirms ingress after Kafka acknowledgment.
Route-specific acceptance is not supported. C3 uses legacy uplink only.

Insight BME680 JSON includes `voc_ppb`, VOC index, level, daily spike count,
and gas resistance/baseline. Numeric values are preserved, while text
`voc_phrase` is ignored. VOC is a qualitative device-relative indicator, not calibrated
laboratory TVOC. The firmware uses a relative MOS/ABC scale:
`voc_ppb` is an integer 0–10000 with JSON unit `ppb`; index/level are derived
from that scale.

- `voc_ppb`: integer 0–10000; `voc_index`: integer 0–100; `voc_level`: integer
  0–3; `voc_spikes_today`: integer 0–65535. Out-of-range or fractional values fail range checks with
  the default profile. Range bounds can be overridden through `sensor_ranges`.
- Constant zero or nonzero values in these four BME680 series pass flatline
  checks once enough samples are present. Pollution events are not required.
- Zero values are valid. UART snapshots cannot establish ABC baseline readiness
  or warmup completion because capture may start late or miss readings.
- `voc_level=-1` means unavailable VOC due to an invalid gas reading, rather
  than warmup. It produces a range WARN, even if later readings recover.
- The daily counter can remain unchanged, saturate, increase, or decrease.
  Firmware resets it by its local calendar date and persists it on SPIFFS;
  a reboot may restore it. The tester does not enforce monotonicity or infer
  day boundaries from the host clock. Reboots still fail runtime checks.
- `gas_resistance` and `gas_baseline` remain diagnostic values in Ohm under
  existing range/flatline/cadence rules; they do not prove calibrated VOC
  concentration. Other measurements and cadence rules are unchanged.

This applies to observed JSON samples. The release driver does not
print those snapshots; the policy does not add a release measurement source
or make VOC fields mandatory in the Insight presence preset.

UART log contract:

- verifies that firmware logs contain enough stable, machine-readable telemetry
  for acceptance testing;
- fails when `[HEALTH]` telemetry or readable sensor samples are missing;
- warns when boot/reset context is missing from `[BOOT]` or `[HEALTH]`;
- warns when `[BUILD]` firmware identity is missing;
- records firmware `channel` and `profile` as context, without treating
  Testing as a debug mode;
- requires upload telemetry only for channels enabled in `[uploads]`.

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
  - `log_contract`;
  - `subsystem_health`;
  - `upload_health`;
  - `device_identity`.

- `report.txt` is the human-readable run report for quick inspection over SSH or
for pasting into an issue or chat.

- `samples.jsonl` contains parsed sensor samples with tester-side timestamps. It
is useful for graphs, cadence analysis, flatline debugging, and parser checks.

- `events.jsonl` contains chronological tester milestones, parsed health
observations, build metadata, payload observations, keyword alerts, upload
observations, and identity observations. HEALTH fields follow the
[health contract](docs/sensor-log-patterns.md#health-contract); device model
belongs to build/identity observations.

While a run is active, the CLI prints live progress with elapsed time, serial
line and byte counters, current serial silence, parsed health records, parsed
sensor samples, and keyword alert count.

## Config Files

`--config` loads a TOML tester profile. Supported sections:

- `[expect]` for required `sensors` and `metrics`;
- `[sensor_ranges.<metric>]` for sane min/max values;
- `[range_checks]` for unknown metric behavior;
- `[flatline]` for stuck-value thresholds;
- `[observations.settings.SENSOR]` for effective per-sensor firmware intervals;
- `[serial]` for serial silence thresholds;
- `[log_contract]` for release-log sufficiency checks;
- `[uploads]` for sensors-connectivity and Robonomics Datalog delivery checks.

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

[serial]
silence_warn_after = "2m"
silence_fail_after = "10m"

[log_contract]
startup_window = "10m"

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

- legacy connectivity requires the device address to be added through
  Robonomics `setDevices`; current C6 firmware can use protobuf instead, and
  the combined connectivity check cannot enforce legacy-specific delivery;
- datalog requires an active Robonomics subscription and the device address to
  be added to it.

Use `optional` when you want upload statistics and warnings without failing the
whole burn-in run.

For CPS/protobuf, readiness involves the chosen endpoint's authorization and
NodeId/anchor provisioning, rather than legacy membership alone. Until route
attribution is implemented, `connectivity = "required"` checks only aggregate
firmware-reported HTTP outcomes; it cannot require a particular transport or
chain publication.

## Batch Config

`configs/batch.usb.example.toml` describes several USB-connected devices for a
batch run. The format is intentionally separate from the single-device tester
profile: the batch config maps physical USB slots to ports, models, and
profiles, while `configs/urban.example.toml` and
`configs/insight.example.toml` keep the actual health rules for each device
type.

For a Raspberry Pi stand with several devices, use a powered USB hub, USB-C
data cables, and visible labels for every physical hub port. Prefer
`/dev/serial/by-path/...` in the batch config because it follows the physical
hub port. `/dev/serial/by-id/...` is useful for identifying a concrete device
and is recorded in reports, but it is less convenient for saying "slot 3 on the
hub". See `docs/raspberry-pi-24h-runbook.md` for the full Pi + USB hub setup.

Example:

```toml
[batch]
duration = "24h"
baud = 115200
output_dir = "runs"
wait_port = true
wait_port_timeout = "5m"

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
- duplicate `port` values or paths resolving to the same port;
- invalid or duplicate `expected_mac` values;
- missing referenced tester profile files;
- devices without an effective profile config;
- unknown `model` values;
- mixed Urban and Insight batches that rely on one shared
  `[batch].device_config`.

Generate a batch directly on the test host (locally or over SSH), reusing the
existing Urban and Insight profiles:

```bash
uv run altruist-tester batch-generate --output batch.toml \
  --output-dir /home/pi/altruist-runs --baud 115200 \
  --urban-profile configs/urban.example.toml \
  --insight-profile configs/insight.example.toml \
  --urban-mac 10:51:DB:01:0D:54 \
  --insight-mac 10:51:DB:01:0C:70
```

Repeat `--urban-mac` and `--insight-mac` for more devices; omit a model and its
profile when that model is absent. MACs accept colon, hyphen, or 12-hex forms.
Slots are reproducible names based on model and MAC, sorted within each model.
Connected devices are matched against USB serial/by-id metadata, with an error
for missing or ambiguous matches. Profiles are validated, and their paths are
written relative to the generated TOML. Existing output files are never replaced.
The destination directory must already exist. `batch-generate --dry-run` prints
the proposed TOML without writing it; generation never opens serial ports.

`--output` selects the new TOML file. `--output-dir` selects the directory for
the entire batch, including batch reports, worker logs, and per-device evidence;
it defaults to `runs`. `--baud` sets the serial rate for every slot and must be
a positive integer (default `115200`). Both values are always written into
`[batch]`, including defaults. Relative `output_dir` paths are interpreted from
the working directory when `batch` is launched, while relative profile paths
are interpreted from the TOML directory. Use an absolute `--output-dir` when
launching from different directories. Generation and preview do not create the
artifact directory; `batch --config batch.toml --dry-run` shows the loaded values.

For devices that are not yet connected, explicitly provide the expected path
format for your stand, for example:

```bash
uv run altruist-tester batch-generate --output batch.toml \
  --urban-profile configs/urban.example.toml \
  --urban-mac 10:51:DB:01:0D:54 \
  --port-template '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_{mac}-if00' \
  --dry-run
```

This is an example for one USB interface, not an automatic MAC-to-port rule.
Check the actual `ports` output and `/dev/serial/by-id` entries on your host first.
Templates support `{mac}` (uppercase with colons), `{mac_lower}`, `{id}` (12
uppercase hex digits), and `{id_lower}`. Pending paths are marked `UNVERIFIED`
in generated TOML comments and preview. Remove `--dry-run` to save the file.
See [configs/batch.mac.example.toml](configs/batch.mac.example.toml).

Generated slots include `expected_mac`. The worker requires matching USB serial
metadata before and immediately after opening the port; a missing or different
USB MAC exits with code `2`. If later `ChipId`/`sensor_id` identifies another
device, capture stops with code `2`, preserving the UART received so far.
An expected path name alone does not verify identity. USB bridges whose serial
number is not the device MAC cannot use this check; existing manual TOML and
`by-path` workflows without `expected_mac` retain their behavior. This validates
initial identity and subsequent observed UART IDs; reconnect/resume is separate
future work. The single-device equivalent is `run --expected-mac <MAC>`.

To require an expected MAC for every slot, opt in when previewing or running:

```bash
uv run altruist-tester batch --config batch.toml --require-expected-mac --dry-run
uv run altruist-tester batch --config batch.toml --require-expected-mac
```

If any slot lacks `expected_mac`, the command exits with code `2` and lists all
such slots in configuration order before USB discovery, worker startup, or
creation of batch artifacts. Dry-run validates the declared expectations even
when devices are absent; it does not verify a physical USB match. Real workers
still perform the USB/UART checks above. Explicit `batch --port` mode has no
per-slot expected MACs, so this flag requires a TOML batch config. Without the
flag, manual configurations and explicit port mode retain their behavior.

Preview a batch setup before running real burn-in tests:

```bash
uv run altruist-tester batch --config configs/batch.usb.example.toml --dry-run
```

Dry-run validates the config and prints the duration, output directory, slots,
ports, models, effective tester profiles, port presence, and USB identity when
metadata is available. It does not open serial ports and does not create run
artifacts.

Run the batch:

```bash
uv run altruist-tester batch --config configs/batch.usb.example.toml
```

With `wait_port = true`, each worker waits for its configured port before
opening UART. This lets you start the batch first, then plug in or power
devices. Early `[BOOT]` and `[BUILD]` lines can still be missed before the host
opens the port. The same behavior
can be enabled from CLI:

```bash
uv run altruist-tester batch --config configs/batch.usb.example.toml \
  --wait-port --wait-port-timeout 5m
```

To connect expected devices at any pace, explicitly request unlimited waiting:

```bash
uv run altruist-tester batch --config batch.toml \
  --wait-port --wait-port-timeout infinite
```

The TOML equivalent is `wait_port = true` and `wait_port_timeout = "infinite"`.
The default timeout remains two minutes; existing finite settings are unchanged.
Each slot starts its full capture duration independently after opening its port.
Absent slots remain in `WAIT_PORT`; unrelated devices are never added. The batch
remains unfinished while any expected device is absent. Ctrl+C/SIGTERM stops
waiting and capturing workers; results of already completed workers are retained.
Interrupted captures keep their raw files but are not resumed or reported as a
completed full-duration run. No reset or Wi-Fi preparation phase is performed by
this workflow yet; capture begins immediately on opening the port.

For a quick homogeneous batch, pass ports explicitly and use one shared tester
profile:

```bash
uv run altruist-tester batch \
  --port /dev/serial/by-path/pci-0000:00:14.0-usb-0:1.1:1.0 \
  --port /dev/serial/by-path/pci-0000:00:14.0-usb-0:1.2:1.0 \
  --duration 2h \
  --device-config configs/urban.example.toml \
  --wait-port
```

Explicit port mode generates slot names as `device-01`, `device-02`, and so on.
Use TOML config for mixed Urban and Insight batches where devices need different
profiles.

USB metadata does not tell the tester whether a device is Urban or Insight.
For mixed batches, set `model` and `config` for every slot explicitly. The
identity fields answer which concrete device was connected; the profile answers
how that device was tested.

The batch runner starts one `altruist-tester run` subprocess per device and
waits for all workers to finish. Each batch run creates a top-level directory
that separates batch files from per-device worker output:

```text
runs/batch_<timestamp>/
  batch_summary.json
  batch_report.txt
  devices/
    slot-01/
      worker.stdout.log
      worker.stderr.log
      worker.progress.json
    slot-02/
      worker.stdout.log
      worker.stderr.log
      worker.progress.json
```

Each slot directory is reserved as the output directory for its single-device
worker. The worker will then create the usual `serial.log`, `events.jsonl`,
`samples.jsonl`, `summary.json`, and `report.txt` inside that slot-specific
area. After every worker has finished, batch exits `2` for configuration/serial or
worker-start errors, otherwise `1` for health-check failures, or `0` without
failures.

In an interactive TTY (including SSH/tmux), batch uses an updating terminal
screen with total elapsed time, worker counts and a compact table of slots.
Devices occupy consecutive rows with aligned status/metric columns. In narrow
terminals, columns split into groups that repeat the slot name and state.
States, errors and telemetry/Wi-Fi observations use color; set `NO_COLOR=1`
to disable colors. Colors indicate activity/observations, not acceptance verdicts.
It refreshes about once a second and restores the screen/cursor on completion
or interruption. Tables adapt to terminal size; a clipping notice asks you to
enlarge a small terminal. On exit, a compact results table replaces the live
status: slot, verdict, worker exit code and short finding codes/error reason.
Final rows retain configuration order, regardless of worker completion order.
Serial errors show the cause first; the full port/message remains in reports.
Finished slots show `—` for STATUS AGE, since their snapshots are no longer live.
WARN remains visible even when the worker exits with code 0. Infrastructure
failures are labeled ERROR and cancelled workers INTERRUPTED. Long reasons
are abbreviated; `Report:` points to the full batch report and `Artifacts:`
to the batch directory. Startup lines show slot names; full worker commands
remain in `batch_summary.json`. `TERM=dumb` and redirected output use ordinary sequential
lines without screen control codes: periodic counters about every ten seconds,
with state changes, first bytes and first telemetry polled every second and
printed when the next snapshot makes them visible.
`WAIT_PORT` shows elapsed wait and remaining time, or `unlimited`. `OPENING`
identifies the port-opening phase; `CAPTURE open=yes` confirms that it opened,
then shows that slot's elapsed capture/percentage, byte count, first-byte receipt,
UART silence, parsed telemetry presence, HEALTH/sample counts, and the last
Wi-Fi state from HEALTH with its age. Before HEALTH, Wi-Fi is `unknown`;
`wifi=OK` means the last HEALTH reported an associated station, not verified
IP readiness, sustained stability, or delivery. UART silence does not diagnose
AP mode or Wi-Fi failure. Worker exit codes and error diagnostics appear on the
parent CLI without opening report files.

There is no overall time percentage: late devices receive their own full
capture window. Workers publish bounded, atomically replaced
`worker.progress.json` snapshots independently of stdout buffering. The parent
reads these snapshots, not raw UART. `status_age` shows snapshot age; missing or
invalid status is explicit. Snapshots are operator progress, not acceptance
evidence or resumable checkpoints. `serial.log` and existing run artifacts
remain the evidence used for verdicts.

If the batch command receives Ctrl+C or SIGTERM, it asks running workers to
terminate, waits briefly, kills any remaining workers, and writes
`batch_summary.json`/`batch_report.txt` with status `interrupted`. Summaries
from devices that already finished are still included when available.

`batch_report.txt` includes each worker's exit code, failure kind and available
error reason, including expected/observed USB MAC on identity mismatch. The
same `worker_returncode` and `worker_error` fields appear in `devices` and
`device_results` in `batch_summary.json`. Infrastructure diagnostics use the
failed run summary's message, falling back to an ERROR snapshot or the last
4096 bytes of worker stderr when no usable summary is available. A worker
that cannot start retains its launch error. Full stdout/stderr logs remain in
the slot directory. These diagnostics do not change device health verdicts.

Worker failures are recorded per slot in `batch_summary.json`:

- exit code `1` is treated as a device health-check failure;
- exit code `2` is treated as an infrastructure or config failure, for example
  a missing or unopenable serial port;
- worker start errors are recorded for that slot and do not stop the remaining
  slots.

After workers finish, the batch runner reads each per-device `summary.json` and
adds `device_results` to `batch_summary.json`. These results include the slot,
model, port, profile, run directory, full device identity, status, verdict,
finding count, failed checks, upload health, and sensor presence summary. The
compact top-level device list also includes `device_id`, `mac`, `usb_serial`,
`by_id`, `by_path`, and identity conflicts when the worker observed them. Batch
logic does not parse raw serial logs.

`batch_summary.json` also contains a top-level batch `verdict` and device
counters: `devices_total`, `devices_passed`, `devices_warned`, and
`devices_failed`. A batch verdict is `FAIL` when any device fails or a worker
cannot provide a valid summary, `WARN` when at least one device warns and none
fail, and `PASS_CANDIDATE` only when all devices pass.

`batch_report.txt` is the SSH-friendly companion report. It shows the batch
verdict, device counters, and one compact section per slot with model, profile,
device id or MAC, USB identity details, port, verdict, short findings, failed
checks, and the path to the per-device `report.txt`. Missing identity and
conflicting identity sources are shown as warnings in the report, but they do
not fail the batch by themselves. Explicit `expected_mac` mismatches are
infrastructure failures as described above.

## Firmware Notes

- The default baud rate is `115200`.
- Configure Wi-Fi before using a run as a burn-in signal.
- If a device stays in the Wi-Fi config portal/AP flow, the tester may report
  missing release-log telemetry instead of useful sensor health.
- Connectivity or datalog HTTP failures can appear in `serial.log`. Stable
  `[CONNECTIVITY]` and `[DATALOG]` lines are parsed as upload observations and
  checked according to the `[uploads]` config, but they are not treated as
  keyword-alert runtime failures.

## Exit Codes

- `0`: run completed without `FAIL` findings;
- `1`: run completed and health checks produced `FAIL`;
- `2`: CLI usage, missing port, config error, or serial-open error.

## Development

```bash
uv sync
uv run altruist-tester --help
uv run altruist-tester run --help
uv run pytest
```

## Malformed PAYLOAD diagnostics

Missing, unknown or truncated PAYLOAD channels are counted separately from valid
observations and produce no sensor readings. The report shows the malformed
count; summary and events retain up to five bounded, sanitized examples with
UART line numbers. Raw serial bytes and existing verdict/exit-code policies are
unchanged. See [the parser contract](docs/sensor-log-patterns.md#malformed-payload-diagnostics)
for fields and limits.

## Measurement freshness and deduplication

Debug firmware supplies versioned UART sensor snapshots with successful
measurement sequence/time and boot identity. Repeated cached values do not
count as new measurements; new measurements with equal values still count.
The capture uses this source from the start; malformed or absent observations
cannot fall back to PAYLOAD or ordinary sensor JSON. Missing telemetry produces
FAIL/exit 1. Full JSON extraction remains available through diagnostic commands.

For slow sensors, cadence uses the explicit profile's measurement interval T:
WARN at gap/age >=T+60 seconds, FAIL at >=2T+300 seconds. The first successful
measurement has a separate wait window: WARN at >=T+60, FAIL at >=T+300.
With T=300 seconds these are 360/900 seconds and 360/600 seconds respectively.
Device measurement times measure gaps; host time since receipt extends the
last successful age even while independent HEALTH lines continue. A new equal
value refreshes freshness; cached identities do not. A recovered excessive gap
still affects the final verdict. Global `[cadence]` multipliers are rejected;
set effective firmware intervals in the per-sensor profile before capture.

Cadence needs at least three distinct successful capture measurements per
mandatory slow metric, covering two intervals. One or two give coverage WARN.
Historical cached readings do not count. Missing metrics/telemetry and explicit
failures retain their severity. Missing intermediate measurement identities
give coverage WARN; their time span is not proof that the sensor stopped.

Microphone frequency uses complete non-overlapping windows of 300 seconds of
active time, wholly after the first accepted microphone snapshot. WARN is <2 Hz;
FAIL is <1 Hz. Partial or historical windows cannot establish a frequency PASS.
A successful full audio block counts once for noiseAvg/noiseMax together.
Independent active gaps warn at >=60 seconds and fail at >=300 seconds,
including an unfinished gap during UART silence. Only a confirmed SDS pause
can exclude time, capped at the profile warmup+collection budget (20 seconds)
per cycle. Repeated flags or the same cycle cannot renew that allowance;
overruns, network work and other worker delays count toward audio gaps.

The error budget counts failed external cycles separately by sensor/channel,
including separate BME680 environment and gas channels. The first recovered
failure gives WARN; the second within any rolling 24-hour window gives FAIL
for the rest of capture. Midnight and later recovery do not reset it. Internal
retries succeeding in the same cycle do not spend the budget. An unrecovered
error at capture end fails even before a hard-gap deadline. Confirmed
pre-capture errors are historical WARN outside the current budget; a continuing
old error still fails. Uncertain start attribution or evicted failure times
produce coverage WARN unless retained evidence/counter deltas already prove a
budget breach. No error rate is inferred from UART line counts.

Known sensor read/init SUBSYSTEM reasons corroborated by the v2 ledger are
reported as warnings, with failure/recovery decided by that ledger. Unmatched
subsystem errors, overflow/contract errors, crashes and runtime/identity failures
keep their failure behavior. The sensor first-wait windows replace the common
startup deadline for sensor data; HEALTH/BUILD/BOOT startup checks remain.

Cadence and flatline retain measurement time and provenance.
`summary.sensor_observations.acceptance` and the UART report section retain
capture baselines, distinct measurement counts, maximum gaps, failure counters,
rolling-budget evidence and complete noise-window counts. The observation summary
also retains repeat/loss counters, bounded firmware failure history, noise
statistics, sensor states and last successful ages. See the
[versioned UART schema and limits](docs/sensor-log-patterns.md#versioned-debug-uart-observations).

Choose a capture window that covers the configured sensor intervals with a
margin. Short checks do not establish long-term flatline behavior or VOC stability.


UART v2 requires explicit expected settings for each observed physical sensor in
`[observations.settings.SENSOR]`, using the canonical names in the example
Urban/Insight profiles. `[observations]` also records `firmware_source` and
`settings_rule`; these describe the operator-selected firmware/config contract.
Review effective send/SDS intervals, gas enablement and noise pause settings
before capture. TOML omits inapplicable null fields. A missing expectation,
settings mismatch or settings change stops capture with exit 2, preserving raw
UART, the expected/declared values, rule provenance and observed BUILD in the
summary/report. This does not declare a sensor defect. The device declaration
never changes the expectation automatically. Unknown BUILD remains unknown.
Malformed required fields or contradictory counters/history are contract
FAIL/exit 1; v1 has no acceptance fallback.
