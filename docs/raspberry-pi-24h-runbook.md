# Raspberry Pi 24-hour Runbook

This runbook describes the first real 24-hour Altruist burn-in test on a
Raspberry Pi or another Linux host.

## Goals

- Keep one assembled device powered over USB-C for 24 hours.
- Capture raw UART logs for the whole run.
- Verify expected sensor data, value ranges, flatlines, update cadence, runtime
  counters, serial silence, and crash/error keywords.
- Preserve artifacts that are easy to inspect over SSH after the run.

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

If upload access was configured before the burn-in run, edit the profile or use
a copied profile with:

```toml
[uploads]
connectivity = "required"
datalog = "required"
```

Use `connectivity = "required"` only after the device address was added through
Robonomics `setDevices`. Use `datalog = "required"` only when there is an active
Robonomics subscription and the device address was added to it.

## Start a Multi-device Batch

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

The command prints live batch progress with elapsed time, worker counts,
per-slot state, and the batch artifact directory.

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
