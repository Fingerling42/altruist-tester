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

- Raspberry Pi with reliable power supply;
- stable network access for SSH;
- enough free disk space for run artifacts;
- Altruist device connected over USB-C;
- firmware already configured for Wi-Fi.

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

Detach from `tmux` with `Ctrl-b`, then `d`.

Reattach later:

```bash
tmux attach -t altruist-24h
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
