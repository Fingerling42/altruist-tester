# Raspberry Pi 24-hour Runbook

This runbook describes a 24-hour Altruist burn-in test on a
Raspberry Pi or another Linux host.

Use `_debug` firmware for USB sensor acceptance and diagnostic burn-in.
Detailed sensor/protocol diagnostics and diagnostic SD files are debug-only.
Release, including Testing/release, retains a bounded operational USB/web log
with named Off / Errors only / Events modes, BUILD identity and an explicit
status snapshot that preserves the mode. Release has no acceptance sensor JSON
or periodic HEALTH. Mode changes are live; Save and restart persists the current
mode and reboots. There is no separate numeric input. Debug exposes only modes
allowed by its forced minimum (normally Detailed and Most detailed).

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

Keep the exact commit, target, language, channel and profile with each capture.
A successful debug run does not validate release firmware.

The [maintained diagnostic procedures](diagnostic-procedures.md) provide safe
summaries, explicit startup/cadence windows, bounded HTTP checks and durable
per-attempt checkpoints. Raw startup logs may contain credentials; use safe
extraction instead of printing matching configuration lines.

## Goals

- Keep one assembled device powered over USB-C for 24 hours.
- Capture raw UART logs for the whole run.
- Verify expected sensor data, value ranges, flatlines, update cadence, runtime
  counters, serial silence, and crash/error keywords.
- Preserve artifacts that are easy to inspect over SSH after the run.

Exact EPD maintenance messages do not trigger WATCHDOG alerts. Real watchdog
and other runtime keyword failures produce FAIL/exit 1 in summary/report;
see the [keyword contract](sensor-log-patterns.md#runtime-keyword-alerts).
Keep serial.log for the original evidence.

## Hardware Setup

Use:

- Raspberry Pi 4/5 or a small Linux mini-PC with reliable power supply;
- stable network access for SSH;
- enough free disk space for run artifacts;
- Altruist device connected over a USB-C data cable;
- firmware already configured for Wi-Fi.

For a multi-device stand, also use:

- powered USB hub with enough ports for the batch;
- one USB-C data cable per device;
- visible physical labels on hub ports, for example `slot-01`, `slot-02`;
- stable power for both Raspberry Pi and hub.

Avoid unpowered hubs for overnight runs. Several ESP devices can draw enough
current during boot, Wi-Fi activity, or reconnects to make an unpowered hub look
like a flaky serial problem.

Before starting a burn-in run, make sure the device is not in Wi-Fi AP/config
portal mode. Current firmware may produce no useful serial logs while waiting
for Wi-Fi provisioning, and the tester will correctly report serial silence.

## Pi Setup

Install project dependencies:

```bash
cd ~/altruist-tester
uv sync --locked --dev
```

Make sure the current user can access serial devices. On Raspberry Pi OS this
usually means being in the `dialout` group:

```bash
groups
sudo usermod -aG dialout "$USER"
```

Log out and back in if the group was just added.

## Find the Device Port

List detected ports:

```bash
uv run altruist-tester ports
```

For a one-device setup, `--auto` is acceptable. For a real 24-hour run, prefer a
stable path from `/dev/serial/by-id` when available:

```bash
ls -l /dev/serial/by-id/
```

Example:

```bash
/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_123456-if00
```

For a multi-device hub, inspect both stable serial namespaces:

```bash
ls -l /dev/serial/by-id/
ls -l /dev/serial/by-path/
```

Use them differently:

- `/dev/serial/by-id/...` follows the concrete USB serial identity of the
  device. It is useful for confirming which physical Altruist board is present.
- `/dev/serial/by-path/...` follows the physical USB topology. It is better for
  batch configs because it maps naturally to a labeled hub slot.

To prepare a stand, plug one device into one labeled hub port at a time, run
`uv run altruist-tester ports`, and write down which `by-path` entry belongs to
that label. Then repeat for the next slot. When all devices are connected, run
`uv run altruist-tester ports` again and check that every expected port is
listed.

## Start a 24-hour Run

Create a dedicated artifact directory:

```bash
mkdir -p ~/altruist-runs
```

Start the run inside `tmux` so it survives SSH disconnects:

```bash
tmux new -s altruist-24h
```

Urban device:

```bash
uv run altruist-tester run \
  --port /dev/serial/by-id/<device-id> \
  --duration 24h \
  --config configs/urban.example.toml \
  --output-dir ~/altruist-runs
```

Insight device:

```bash
uv run altruist-tester run \
  --port /dev/serial/by-id/<device-id> \
  --duration 24h \
  --config configs/insight.example.toml \
  --output-dir ~/altruist-runs
```

Map health GET probes select a server and do not count as upload attempts or
failures. `health_checks` and `health_check_reasons` preserve them separately
in summaries/reports; real POST/local failures retain their configured severity.
See the [health-probe and upload classification](sensor-log-patterns.md#map-server-selection-and-upload-outcomes).

If upload access was configured before the burn-in run, edit the profile or use
a copied profile with:

```toml
[uploads]
connectivity = "required"
datalog = "required"
```

For legacy delivery, use `connectivity = "required"` only after the device
address was added through Robonomics `setDevices`. Use `datalog = "required"`
only when there is an active Robonomics subscription and the device address
was added to it.

C6 Map sending prefers protobuf; an empty custom protobuf URL uses a
built-in endpoint. Success skips legacy JSON and its `[PAYLOAD] sample=`;
both Map toggles mean JSON fallback, not unconditional dual-send. Confirm
debug sensor JSON and HEALTH during a preflight run before starting 24 hours,
including Insight VOC/gas when applicable. BME680 and
SCD4x have a minimum 300-second fetch interval; cover the configured interval
with a margin; for example, allow at least 330 seconds for a 300-second interval.
Datalog samples depend on configuration/encryption. Release intentionally lacks
this diagnostic contract. Missing readings still fail acceptance, regardless
of upload success.

The tester combines legacy/protobuf CONNECTIVITY results. Treat a successful
required connectivity check as aggregate firmware HTTP evidence. It does not
prove legacy delivery, CPS finalization, or map visibility; cloud `202` is
Kafka ingress acceptance. CPS endpoint authorization and NodeId/anchor
provisioning differ from legacy `setDevices`. Route-specific acceptance is
not supported. C3 uses legacy uplink only.

For observed Insight BME680 JSON, constant `voc_ppb`, `voc_index`, `voc_level`,
and `voc_spikes_today` are allowed; do not provoke pollution to make them change.
Default ranges require integer `voc_ppb` 0–10000, index 0–100, level 0–3, and
daily count 0–65535. Bounds use the existing `sensor_ranges` configuration.
`voc_ppb` (JSON unit `ppb`) is a relative MOS/ABC scale, not laboratory TVOC.
Diagnostic procedures require all six VOC/gas fields, including `voc_ppb`.
The ordinary Insight presence preset requires temperature, humidity and pressure.
Level -1 gives WARN for unavailable VOC (invalid gas reading), including a
transient occurrence; inspect raw evidence and gas resistance/baseline.
Late UART capture cannot prove warmup completion or ABC readiness. The daily count may reset
at the device's local midnight/time synchronization or survive a reboot;
no monotonicity is required. Runtime reboot failures and gas diagnostic
flatline/cadence checks still apply. These rules do not add readable release
VOC samples; confirm the available measurement source during preflight.

## Start a Multi-device Batch

The same CLI workflow works over SSH on other supported hosts and ordinary
powered USB hubs. The host model and hub model are not configuration requirements.
Keep device profiles on the host and generate each new batch there, without
copying TOML device sections or transferring a new batch from another computer:

```bash
uv run altruist-tester batch-generate --output batch.toml \
  --output-dir /home/pi/altruist-runs --baud 115200 \
  --urban-profile configs/urban.example.toml \
  --insight-profile configs/insight.example.toml \
  --urban-mac 10:51:DB:01:0D:54 --urban-mac 10:51:DB:01:0D:55 \
  --insight-mac 10:51:DB:01:0C:70 --insight-mac 10:51:DB:01:0C:71 \
  --insight-mac 10:51:DB:01:0C:72 --insight-mac 10:51:DB:01:0C:73 \
  --insight-mac 10:51:DB:01:0C:74 --insight-mac 10:51:DB:01:0C:75
```

Replace these sample MACs with the actual devices. For six Urban or three Insight,
repeat only that model's MAC option and supply only that model's profile.
Generation matches connected USB identities and rejects missing/ambiguous matches,
duplicate MACs, and existing output files. Add `--dry-run` to review the TOML
without writing it. Neither generation nor preview opens serial ports.

`--output` names the new configuration file; `--output-dir` names the directory
for the whole batch, including reports, worker logs and device evidence. The
generator always writes `output_dir` and `baud` into `[batch]`; omitted options
use `runs` and `115200`. `--baud` accepts positive integers and applies to every
slot. A relative `output_dir` is resolved from the working directory where you
launch `batch`, whereas profile paths are relative to the TOML directory. Prefer
an absolute output directory when launching from different SSH/tmux directories.
Generation and preview do not create the artifact directory. Check the loaded
values with `batch --config batch.toml --dry-run` before starting capture.

To prepare before connecting devices, add an explicit `--port-template` matching
your stand, for example
`--port-template '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_{mac}-if00'`.
This particular example applies only when actual USB/by-id metadata has that
format. Use `ports` and `/dev/serial/by-id` to verify it. `{mac_lower}`, `{id}`
and `{id_lower}` are also supported. Review `UNVERIFIED` pending paths before
saving. The generated `expected_mac` requires real matching USB serial metadata
at startup; a path string alone is insufficient. Bridges with an unrelated USB
serial need a manual configuration instead. Relative profile paths are generated
from the output TOML directory. The destination directory must already exist.

Start and observe a generated batch in the SSH `tmux` session:

```bash
uv run altruist-tester batch --config batch.toml --require-expected-mac --dry-run
uv run altruist-tester batch --config batch.toml --require-expected-mac \
  --wait-port --wait-port-timeout infinite
```

`--require-expected-mac` rejects the whole batch with code `2` if any slot lacks
`expected_mac`, listing every missing slot before any worker or run artifact is
created. Preview works while devices are absent; a declared MAC is not a physical
USB verification. Real workers still check USB/UART identity at capture time.
Use TOML for this mode; explicit `--port` slots have no expected MACs. Omit the
flag for the existing manual workflow with bridges that cannot expose a MAC.

Plug expected devices in at any pace. Each slot waits independently and receives
the full configured capture duration after its port opens. Other USB devices are
not added. An absent expected device keeps the batch unfinished; Ctrl+C/SIGTERM
stops waiting and capturing workers and retains completed workers' results.
Interrupted raw captures remain on disk but have not completed acceptance.
No automatic reset or Wi-Fi preparation/retry is enabled. Existing finite wait
timeouts remain available, with two minutes as the default.

Create a batch config that maps each physical hub slot to a `by-path` port and
to the correct tester profile:

```toml
[batch]
duration = "24h"
baud = 115200
output_dir = "/home/pi/altruist-runs"

[[devices]]
slot = "slot-01"
model = "urban"
port = "/dev/serial/by-path/<hub-port-for-slot-01>"
config = "/home/pi/altruist-tester/configs/urban.example.toml"

[[devices]]
slot = "slot-02"
model = "insight"
port = "/dev/serial/by-path/<hub-port-for-slot-02>"
config = "/home/pi/altruist-tester/configs/insight.example.toml"
```

Do not rely on USB metadata to select Urban or Insight rules. The USB identity
answers which concrete board is connected; `model` and `config` answer how that
slot should be tested.

Preview the setup before opening serial ports:

```bash
uv run altruist-tester batch --config /home/pi/batch.toml --dry-run
```

The dry-run should show:

- every slot from the config;
- every expected `by-path` port;
- the effective Urban or Insight profile for each slot;
- detected USB identity when the port is present.

Start the batch inside `tmux`:

```bash
tmux new -s altruist-batch
uv run altruist-tester batch --config /home/pi/batch.toml
```

In a TTY the command updates a screen about once a second, showing total
elapsed time, worker counts, the artifact directory and compact aligned rows
for slots. Devices appear consecutively below shared column headings; narrow
windows split metric columns into groups with repeated slot/state columns.
Color distinguishes states, errors and observations; use `NO_COLOR=1` to
disable it. Green capture/telemetry/Wi-Fi cells do not establish acceptance.
It works with the terminal provided by SSH/tmux. Enlarge the terminal if a
clipping notice appears. On completion or interruption the screen/cursor are
restored and a compact results table is printed: slot, verdict, worker exit
code and short finding codes/error reason. Final rows keep configuration order;
serial errors show the cause without the long port prefix. The full message
remains in reports. Completed live rows show `—` for STATUS AGE.
Check WARN even for worker code 0;
ERROR identifies infrastructure failures and INTERRUPTED cancelled workers.
Long reasons are abbreviated; use the printed `Report:` path for full details.
The `Artifacts:` path contains worker logs; full commands are retained in
`batch_summary.json` instead of long startup lines. `TERM=dumb` uses
sequential output. With redirected output, counters are printed about every
ten seconds; state changes, first bytes and first telemetry are polled every
second and printed when observed.
`WAIT_PORT` shows elapsed waiting
and remaining time or `unlimited`; `OPENING` identifies an attempted open;
`CAPTURE open=yes` shows an open port, capture duration/percentage, received
bytes/first-byte receipt, UART silence, parsed telemetry and HEALTH/sample counts.
The last Wi-Fi HEALTH state includes its age; `unknown` means no observation,
and `OK` does not prove IP readiness or sustained stability. A silent open port
does not establish AP mode or a Wi-Fi failure. Worker failures include the exit
code and diagnostics on this console. Snapshots have `status_age` so stale status
is visible. Total batch time has no percentage because capture starts differ.

Output remains line-oriented when redirected to a file. Each slot's
`worker.progress.json` is a bounded atomic status snapshot, separate from the
run's UART/evidence artifacts; it is not a resume checkpoint. USB MAC mismatch,
unavailable required USB identity, or conflicting observed UART identity stops
that slot with infrastructure code `2`; neighboring slots continue.
`batch_report.txt` includes worker exit codes and available error reasons,
including expected/observed MAC. The reason comes from the failed run summary,
then an ERROR snapshot or a bounded stderr tail if no usable summary exists;
worker launch errors are also retained. Full worker stdout/stderr stay in the
slot directory. `batch_summary.json` exposes `worker_returncode` and
`worker_error` in `devices` and `device_results`.

Detach from `tmux` with `Ctrl-b`, then `d`.

Reattach later:

```bash
tmux attach -t altruist-24h
```

or, for the multi-device session:

```bash
tmux attach -t altruist-batch
```

## Live Checks

The CLI progress line should show increasing elapsed time, serial line and byte
counters, parsed samples, and current serial silence.

In another SSH session, inspect the latest run directory:

```bash
latest_run="$(ls -td ~/altruist-runs/* | head -n 1)"
echo "$latest_run"
tail -n 50 "$latest_run/serial.log"
tail -n 20 "$latest_run/events.jsonl"
tail -n 20 "$latest_run/samples.jsonl"
```

For a batch run, inspect the latest batch directory:

```bash
latest_batch="$(ls -td ~/altruist-runs/batch_* | head -n 1)"
echo "$latest_batch"
cat "$latest_batch/batch_report.txt"
python -m json.tool "$latest_batch/batch_summary.json" | less
```

Per-device worker logs are under:

```text
<batch-dir>/devices/<slot>/worker.stdout.log
<batch-dir>/devices/<slot>/worker.stderr.log
```

Each worker then writes the normal single-device artifacts inside its slot
directory.

If `serial.log` stays empty for several minutes, check:

- USB-C cable and power;
- selected serial port;
- device Wi-Fi provisioning state;
- whether another process has the serial port open.

## After the Run

The command exits with:

- `0` when the run completed without `FAIL` findings;
- `1` when health checks produced `FAIL`;
- `2` for CLI/config/serial-open errors.

Inspect the latest artifacts:

```bash
latest_run="$(ls -td ~/altruist-runs/* | head -n 1)"
cat "$latest_run/report.txt"
python -m json.tool "$latest_run/summary.json" | less
```

Start with `report.txt` for the human summary, then use `summary.json` for the
full verdict and per-rule payloads. Keep `serial.log` as the source of truth for
firmware/parser investigations.

For batch runs, start with `batch_report.txt`. It shows the overall verdict,
per-slot verdicts, device identity, model/profile, short findings, and links to
per-device reports. If identity is missing or conflicting, the report shows a
warning for that slot without failing the batch by identity alone.

## Expected First-run Outcomes

For a healthy configured device:

- `serial.log` grows throughout the run;
- `samples.jsonl` contains all expected sensor metrics;
- `summary.json` has `samples_seen: true`;
- `sensor_presence.status` is `ok`;
- `serial_silence.status` is `ok`;
- `runtime_counters.status` is `ok`;
- upload channels are either `disabled`, or have enough successful sends when
  explicitly configured as `required`;
- final `rules.verdict` is either `PASS_CANDIDATE` or `WARN`.

`WARN` is acceptable for the first real long run when it points to tuning needs,
for example a threshold that is too strict for the current firmware cadence.
`FAIL` should be investigated before accepting the device.

## What to Save

For a production record or bug report, keep the whole run directory:

```text
serial.log
events.jsonl
samples.jsonl
summary.json
report.txt
```

If sending a compact result to chat or an issue, start with `report.txt` and add
the relevant `summary.json` section.

## Inspect malformed PAYLOAD diagnostics

Check the report's malformed observation count, then
`summary.json` → `payload_observations` for counts/reasons and the first five
bounded examples. Use each example's one-based `line_number` to locate the
original bytes in `serial.log`; do not edit run artifacts. Further malformed
records still increment counters. Missing/unknown channels are excluded from
normal payload statistics and readings; this diagnostic alone adds no severity.
See
[the parser contract](sensor-log-patterns.md#malformed-payload-diagnostics)
for the exact fields and bounds.

Physical presence requires each preset's metrics from its named JSON sensor,
with aliases normalized. PAYLOAD samples are excluded from measurements. Imported Urban readings cannot
confirm a physical module; missing pairs fail and are listed in summary/report.
Use `expect.metrics` alone for checks that intentionally accept any source.
See [presence semantics](sensor-log-patterns.md#physical-sensor-presence).

## Check measurement freshness and deduplication

Use the existing Urban/Insight profiles and expected-MAC guard. The first
versioned UART stream is required throughout capture; inspect
`summary.sensor_observations.source` for `serial_observation_v1` and require no
contract issues. Presence must come from each expected physical sensor. Compare
duplicate counts with accepted samples: heartbeat cache repeats should not
increase measurement counts. Freshness follows `[cadence]` thresholds, not the
30-second UART heartbeat. Error states fail; recovered cumulative errors warn.
See the [schema and artifact semantics](sensor-log-patterns.md#versioned-debug-uart-observations).

Choose a capture window that covers the configured sensor intervals with a
margin. Keep measurement intervals and network/encryption settings consistent
with the intended operating conditions. Short checks do not replace full burn-in
or long-duration VOC observation.
