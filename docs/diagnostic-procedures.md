# Firmware diagnostic procedures

Maintained replacements for the one-off L2a helpers live in
`altruist_tester/diagnostics.py` and `diagnostic_checks.py`. Historical archives
and captured attempts are unchanged. These procedures supplement normal tester
acceptance; their PASS does not imply acceptance of a device, delivery route or
all firmware targets.

The procedures have software tests with synthetic UART/HTTP and fake transports.
They still require a separately approved hardware run. The earlier hardware
PASS on firmware `7d961c4` does not validate this replacement implementation.

## Safe extraction without devices

From the tester Git root, using the existing environment:

```sh
.venv/bin/python -B -m altruist_tester.diagnostics extract \
  --input tests/fixtures/diagnostic_uart.log
```

This example uses a deliberately synthetic fixture. `extract` performs no port
discovery, serial open, HTTP request or configuration change. For a real input,
use only an explicitly approved log file. Do not search for an IP and print its
entire matching line: a single config JSON can contain credentials and addresses.

Only validated device ID, IP, commit, enumerated model/profile/channel/target/
language, numeric boot/uptime, tag counts and fixed sensor/metric names can reach
summaries. Sensor values, arbitrary JSON keys, BUILD raw_fields, page text, URLs
from exceptions and exception messages are excluded. UART JSON uses the existing
`parse_sensor_values`; API `sensordatavalues`, ciphertext and PROTO are not UART
sensor snapshots. Imported Urban fields are excluded from the diagnostic sensor
allowlist, so these counts cannot serve as physical inventory.

The boundary is an allowlist, not a detector for arbitrary secrets. Do not place
credentials in command arguments, filenames, paths or environment overrides.
The tool accepts no authentication credential options. Real incident analysis
and credential rotation are separate work; never use real secrets in tests.

## Evidence and replay

The default permanent output root is `runs/diagnostics/` in this source checkout.
Each command creates a new random attempt ID under it; existing attempts are
never reused or overwritten. Live commands reject an output root under the OS
temporary directory. Operators must still choose persistent storage: this check
is not a filesystem/mount durability audit.

- `attempt.json`: exact procedure arguments, replay command, resolved inputs,
  hashes of both procedure modules and the reused sensor parser, start/end,
  exit code and explicitly allowed environment override.
- `state.json`: compact safe progress and latest completed phase, atomically
  replaced only within this new attempt; `phases.json` retains completed results.
- `http.json`: separate readiness, baseline reboot, mode/snapshot, restart request,
  reboot, persistence, volatile setting, restoration and browser results.
- `serial.log`: complete private UART, including startup; it can contain secrets.
  `startup.json` and `uart.json` are safe summaries. UART summary records the
  exact observation byte offsets; attempt metadata records the raw SHA-256.
- `extracted.json`: safe offline extraction; input SHA-256 is recorded as well.

Directories are mode 0700 and files 0600. Raw data is not copied to stdout or
stderr and must not be committed. These permissions do not isolate processes
running as the same user. Keep an attempt directory whole. Read safe summaries
before considering any separately authorized raw inspection.

Use the `replay_command` of the chosen attempt, with the recorded module hashes.
It preserves explicit duration/startup/cadence options and creates a fresh attempt.
Replay of a live command still requires device authorization. It is not an
automatic resume: after interruption inspect `state.json`, `phases.json` and any
unverified restoration before deciding what to do. A missing end/exit remains
incomplete, not PASS. Do not launch a duplicate while an old process may be active.

## HTTP: readiness and optional device actions

The following are **live examples for a separately approved hardware stage**.
The ID, address and commit below are documentation placeholders, not selected
real devices. Replace them with the agreed identity/model/profile/version.

```sh
.venv/bin/python -B -m altruist_tester.diagnostics http-check \
  --host 192.0.2.10 --device-id 001122334455 \
  --firmware-commit abc1234 --model insight --profile debug \
  --timeout-seconds 240
```

With no action flag this checks identity/version, a fresh BUILD/status snapshot
and the advanced page. Snapshot requests emit support records but do not save
configuration. HTTP redirects are not followed; hosts must be literal IPs.
Auth-required responses are NOT_CHECKED, with no attempt to bypass or change auth.

To change only the live log mode, explicitly add `--set-level 3` (0–5 accepted).
Release clamps high values and maps legacy 2 to Errors; the standard debug
force=4 build ignores lower requests. The result records the prior runtime level.
This operation does not claim persistence or restoration. Keep its before/after
results when bracketing a passive UART window; do not run mode changes during
that observation window. Custom debug forced minima other than 4 are not covered.

To exercise modes, snapshot and persistence, explicitly add:

```sh
--exercise --timeout-seconds 240 --restore-reserve-seconds 60
```

`--exercise` and `--set-level` are mutually exclusive. Exercise is a device-changing
operation: in the successful standard path it makes **four restart/save cycles**:

1. Restart without saving and verify a fresh boot, to load the persisted baseline.
   The initial form field alone is only RAM state. Without a verified baseline,
   no configuration is saved and persistence remains NOT_CHECKED.
2. Exercise named modes and mode-preserving snapshot, then save a test level and
   verify a new boot with that level.
3. Select an unsaved live level, restart, and verify return to the saved test level.
4. Save the original loaded baseline and verify boot and its restored level.

Each reboot result retains numeric before/after witnesses. A strictly increased
boot counter is preferred. If boot counters are unavailable, a decreased uptime
can prove a new boot, except near the firmware millis-wrap boundary. Equal boot
counters do not become success because uptime decreased. Changed identity/version
fails; missing witnesses yield NOT_CHECKED. Counters must be trustworthy for the
selected firmware; saturation/reset or a new status format requires review.

A rejected HTTP restart is FAIL. A lost connection/response is an unverified
request, never unconditional success: only an independently observed new boot
can verify the resulting reboot/persistence. HTTP results never imply browser
rendering, JavaScript execution or authenticated access; `browser` stays
NOT_CHECKED. Current mode predicates target the standard force=4 debug builds.

One monotonic end bounds the whole invocation, including request bodies and
retries. Individual requests are also bounded to at most five seconds, with a
body size limit and connection close on timeout/interrupt. Exercise stops its
main work before the reserved restoration interval; that interval does not
extend the overall deadline. If restoration cannot be verified before the end,
it is NOT_CHECKED/FAIL and needs an explicit follow-up. Closing a connection
cannot undo a request the device already accepted.

## UART: startup and cadence are explicit

After separate approval, start a passive capture on the exact agreed USB serial
and port (discovery must find exactly one matching identity). Model is explicit;
USB metadata is not used to infer Urban versus Insight.

```sh
.venv/bin/python -B -m altruist_tester.diagnostics uart-capture \
  --port /dev/serial/by-id/REPLACE-WITH-AGREED-PORT \
  --device-id 001122334455 --firmware-commit abc1234 \
  --model insight --profile debug \
  --startup-seconds 30 --cadence-seconds 300 --margin-seconds 30 \
  --duration-seconds 330
```

No reset command is sent; DTR/RTS are set false before opening. This does not
prove that opening CDC cannot reset a particular device. A serial exclusive open
is requested. The tool does not flash, erase or reconfigure a device.

Startup data is kept separately and excluded from the observation predicate.
For debug, duration must cover declared cadence plus margin. Supply the actual
configured cadence; minimum values are not proof of the current setting. A
300-second Insight setup needs the explicit 330-second observation plus startup,
not a 150-second total. BUILD should be observed during startup or the window;
a separately approved concurrent HTTP snapshot can emit it. Missing BUILD is
NOT_CHECKED; contradictory build/identity fields fail rather than overwrite.

UART windows use host read-completion times, not device sample timestamps;
boundary uncertainty is up to the serial read timeout (0.2 s). Raw byte offsets
allow re-extraction of exactly the evaluated window. Progress is emitted about
every ten seconds and durable checkpoints precede later phases.

For release add `--mode off`, `--mode errors` or `--mode events`; these describe
an externally established mode and **do not select or automatically confirm it**.
`mode_confirmation` remains NOT_CHECKED. Keep separate HTTP mode-change results
before and after the window, using the same identity/version; a UART PASS alone
cannot prove the selected mode. Do not change modes while a window is running.
Use `--cadence-seconds 0 --margin-seconds 0` for release windows.

Off requires zero bytes. Errors/Events require structurally valid LOG/BUILD and
no detailed tags/sensor JSON; Errors forbids non-error automatic events. An
empty Errors/Events window is NOT_CHECKED, not a vacuous successful test of event
filtering. Debug requires HEALTH and recognized full sensor JSON; Insight also
requires all six BME680 VOC/gas fields, including `voc_ppb` (current firmware).
Older firmware without `voc_ppb` remains supported by ordinary acceptance, but
does not satisfy this current diagnostic predicate. These checks do not run
the normal flatline, upload, freshness or full sensor acceptance rules.

## Exit codes and software validation

For this separate diagnostic command, 0 means requested predicates passed,
1 means a predicate failed, and 2 means invalid input, infrastructure failure,
interruption or an unverified requested check. Nonrequested checks (including
browser, and passive capture's mode confirmation) remain explicit but do not
turn a narrower requested check into full-device acceptance. The ordinary
`altruist-tester` CLI and its code meanings are unchanged.

Run the existing local tools without syncing/installing dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_diagnostics.py
.venv/bin/ruff check --no-cache altruist_tester tests
.venv/bin/ruff format --no-cache --check altruist_tester tests
```

Tests use synthetic credentials, fake firmware, fake clocks and mocked HTTP/serial.
They block real sockets, USB discovery and serial opens. Software checks do not
establish hardware cadence, reboot counter behavior, restoration, browser UI or
USB side effects for the replacement procedures.
