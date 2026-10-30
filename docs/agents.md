# Pi agent deployment contract

These instructions do not replace or restart the existing agents automatically.
First confirm Pi identities, controller health, and that the selected device
has no job in progress. The restored Pis shared a controller UUID. On
2026-10-30, the user authorized removal of Pi 2's cloned controller and models;
its replacement is `hilaas-pi2`. Pi 1 retains `localhost-controller`.
Renaming a hostname alone does not resolve cloned controller identities.

## Registered-device deployment configuration

Pi 1 is `bem22@10.146.21.22`; Pi 2 is `bem22@10.146.21.243`.
Both run Ubuntu 24.04 ARM64. The agent-host charm artifact targets Ubuntu
22.04 ARM64 guests. Deploy one application and guest per row, with the
corresponding `agent/live/<directory>` as `config-dir`:

| Directory | Agent ID | Queue | Observed device identity |
| --- | --- | --- | --- |
| pi1-c6 | hilaas-pi1-c6 | hilaas-esp32c6-pi1 | ESP32-C6, base MAC f0:f5:bd:01:83:1c, native USB Serial/JTAG |
| pi1-microbit | hilaas-pi1-microbit | hilaas-microbit-pi1 | micro:bit CMSIS-DAP, serial 9906360200052820e6a4fc0d0218ebb9000000006e052820 |
| pi1-nucleo | hilaas-pi1-nucleo | hilaas-nucleo-pi1 | STLINK-V3, serial 000F00304142500720353451; user-confirmed NUCLEO-H753ZI |
| pi2-esp32 | hilaas-pi2-esp32 | hilaas-esp32-pi2 | ESP32-D0WD-V3, MAC 08:3a:8d:14:8e:28; CP2102N serial 54cc2b064155ed119c7d93131d62bc44 |
| pi2-c3 | hilaas-pi2-c3 | hilaas-esp32c3-pi2 | ESP32-C3, MAC 70:af:09:57:bf:38; CP2102N serial de2a2a9800bff0118a94bf7948e9de0f |

The chip probes read identity and reset the Espressif boards without writing
flash. The C6 connection is not the UART bridge required by the existing
hardware adapter below. Do not enable that adapter merely because the chip
model matches.

These live configurations allow only a **fixed non-flashing smoke test**.
Setup validates a data-only token and rejects attachments or firmware phases.
The fixed test checks its source commit, ARM64 execution, and the assigned
serial node's type and permissions without opening the node. Provision and
cleanup are no-ops. Submitted commands are never executed. Firmware execution
and raw USB debugger access remain disabled; board-specific adapters must be
reviewed separately. An online agent is not proof of working MCU firmware.

`scripts/register-pi-agents.py` runs inside the AWS server workload. Its
`--check` mode is read-only. Registration accepts the `client-id` and
`secret-key` JSON fields on stdin, creates the `testflinger-agent` login with
agent role and exactly these five queues, and restricts those queues. It
refuses existing accounts or restrictions rather than replacing them. It
does not manufacture agent heartbeat records or grant contributor access.

Each guest receives only its own stable serial interface at `/dev/hilaas-mcu`,
with access limited to the `dialout` group. Raw USB debug interfaces are not
passed through by this registration setup.

Each guest needs its own root-readable SSH tunnel key and the pinned AWS host
key at `/etc/hilaas/known_hosts`. Install the service from
`config/pi-agents/hilaas-api-tunnel.service`; it binds only guest loopback and
forwards to the private API over SSH. Install `supervisor-api.conf` as
`/etc/systemd/system/supervisor.service.d/hilaas-api.conf` so Supervisor starts
after the tunnel's API-readiness check. Limit each public key on AWS to API port
forwarding; do not copy the AWS administrator private key into the guests.
The public browser OAuth endpoint is not an agent API endpoint.

The user made `https://github.com/bem22/HILAAS-Demo.git` public for these
configuration clones. Keep that HTTPS URL as `config-repo`; no GitHub token or
deploy key is installed in the guests. Verify anonymous `git ls-remote` from a
guest before activation. If cloning failed before access was available, use
the charm's `update-configs` action after fixing access. A successful action
return or later active status can mask an earlier clone failure, so confirm
that the expected Supervisor process and server heartbeat actually exist.

Grant the credentials through a model-owned Juju secret, never Git. Start the
tunnel before authentication. Once authentication succeeds, restart an idle
agent if it cached a failed initial token refresh. Verify real heartbeat
records and restricted queue ownership, not only Supervisor or Juju status.

On 2026-10-30 all five applications reached active/idle, all five Supervisor
processes were running, and the AWS API reported each agent as waiting on its
own queue, restricted to `testflinger-agent`. Pi 1 uses guests
`juju-6b2265-0` (C6), `juju-6b2265-1` (micro:bit), and `juju-6b2265-2` (Nucleo).
Pi 2 uses `juju-e49df4-0` (ESP32) and `juju-e49df4-1` (C3). No firmware job was
submitted. This is registration evidence, not a hardware test result.

## Non-flashing smoke jobs

The five-agent smoke run below is historical verification. The Pi 1 ESP32-C6
queue has since been assigned to the [two-test firmware demo](firmware-demo.md)
and now uses its fixed flashing harness. The other four agents remain smoke-only.
Do not submit smoke tokens to the C6 firmware queue.

Run the server-side registration helper with `--smoke-submitter --check` first,
then `--smoke-submitter --output <fresh-private-path>` to create
`hilaas-pi-smoke-submitter`. This contributor can use only the five device queues
and has no elevated priority or reservation allowance. Its generated refresh
token expires after one hour. Transfer it privately to the client with mode 600;
never add it to Git. Queue ownership now includes this contributor and the agent
login. Existing accounts and unexpected ownership are never overwritten.

Reload each idle unit with its `update-configs` charm action after publishing
the handler. Then submit one job, using the exact commit loaded in that guest:

```shell
python -m hilaas.pi_smoke_client \
   --server http://127.0.0.1:15000 \
   --token-file "$HOME/.local/state/HILAAS-Demo/pi-agents/smoke-submitter.json" \
   --agent hilaas-pi2-c3 --run-id pi-smoke-example \
   --commit "$(git rev-parse HEAD)" \
   --output artifacts/pi-smoke-example/hilaas-pi2-c3
```

The client records submission intent before its single POST, checks the queue
and agent are idle, verifies setup/test/cleanup status, downloads bounded
artifacts without extracting paths, and requires exact agent/job/nonce/source
correlation. It also checks the agent returns to waiting. Existing output paths
block resubmission; timeouts never resubmit or cancel jobs automatically.
`artifacts/pi-smoke-result.json` explicitly records that no device was opened,
no flash was written, and no MCU firmware was executed. This validates job
dispatch and host-side execution, not the MCU's firmware behavior.

### Verified smoke run

Run `pi-smoke-47057a6` passed on all five agents on 2026-10-30, using source
commit `47057a616e7a8f2d356ff2f98136f70de84a73f3` in every guest. Exactly one job
was submitted per agent, with no retries. Setup, test, and cleanup succeeded;
each correlated artifact passed verification and each agent returned to waiting.

| Agent | Testflinger job | Result |
| --- | --- | --- |
| `hilaas-pi1-c6` | `318dfc7b-485a-4878-bfa6-226baf3f067a` | Passed |
| `hilaas-pi1-microbit` | `2fc44d13-9dd1-4e6f-8b3f-77c3eb5801c7` | Passed |
| `hilaas-pi1-nucleo` | `1566af38-6aba-468c-8041-b6583e6fa4c0` | Passed |
| `hilaas-pi2-esp32` | `7726f1d1-4255-4d56-a0e3-1cd3c30dde35` | Passed |
| `hilaas-pi2-c3` | `6d0c28d1-cf6a-482f-81cc-46f74eeb2bbc` | Passed |

Local receipts, phase results, archives, and verified proof are retained under
`artifacts/pi-smoke-47057a6/<agent>/`. No serial device was opened, no firmware
was flashed, and no MCU firmware was executed by these jobs. This run does not
validate the GitHub runner/Spread/Workshop build path or MCU serial communication.
The temporary contributor token expires after one hour; agent credentials are
unchanged. Guests remain on the tested source commit until the next refresh.

## Prepare one ESP32-C6 agent

1. Install a reviewed, pinned copy of this repository at `/opt/hilaas-demo` in
   the Ubuntu ARM64 agent guest. Create `/opt/hilaas-demo/.venv` there and install
   `.[agent]`. Preinstall dependencies; the test job does not download packages.
2. Pass only the intended C6 UART bridge through LXD. Give the agent user serial
   permissions and choose a stable `/dev/serial/by-id/` path. This first firmware
   uses the UART console, not native USB Serial/JTAG. Confirm the DevKit revision
   and which USB connector exposes the UART bridge before flashing.
3. Read the **C6's** physical MAC with esptool. Do not use the Pi Wi-Fi MAC or
   the old ESP32-S3 MAC. Review `firmware/esp32c6/partitions.csv`, build once, and
   approve the resulting `partition-table.bin` SHA256 independently of job input.
4. Copy `agent/esp32c6.example.json` to `/etc/hilaas/esp32c6.json`. Fill the real
   MAC, port, queue and partition hash. Keep the file administrator-owned and
   readable by the agent. Set `enabled` to true only when approved. Do not allow
   submitted attachments or CI variables to replace this host configuration.
5. Use `agent/configs/pi1-c6/testflinger-agent.conf.example` as a template for the
   deployed config repository. Remove `.example` only in the deliberate deployment
   configuration, configure the reachable server, and retain `default.yaml`.
   The demo repository itself has no active agent configuration.
6. Set charm `config-repo`, `config-branch`, `config-dir`, `testflinger-server`,
   and `credentials-secret` through the existing agent-host charm. Use unique
   agent names and directories on each Pi. Supply credentials using a Juju secret,
   never in the repo. Config changes may restart all agents on an application;
   coordinate with other work before applying them.
7. Verify Supervisor, registration, queue restrictions, and empty outstanding
   work. Restrict `hilaas-esp32c6-pi1` to `hilaas-demo-submitter` using the server's
   administrative interface, and give that contributor the queue permission.
   Mirror the physical MAC and approved hash in the private client config.

## Execution and safety

The agent runs `agent/run-c6.sh` from the job execution directory. This sets
absolute attachment/job/evidence paths and invokes a fixed pytest module from
the trusted installation. The uploaded `test_cmds` value is an exact data-only
confirmation token; it is never executed as a shell command.

The pytest fixture validates the bundle and job before device access, requires
the host queue and physical MAC, checks the approved partition-table hash, takes
an exclusive device lock, and uses private copies of captured image bytes.
It reads the chip MAC, writes once, verifies flash, and collects three increasing
serial sequences carrying the build nonce and device MAC. All evidence is saved
under the job's `artifacts/`. The Testflinger client checks the returned evidence
against the submitted manifest and phase exit codes.

There is no separate provision flash; this adapter owns deployment and test
together in the test phase. Do not add another flasher in `provision_command`,
enable pytest retry plugins, or configure automatic hardware reruns. Failed
hardware tests require inspection of the existing job before another attempt.
The simple noprovision cleanup is `/bin/true` because the fixture closes serial
ports, releases its lock, and removes private temporary files itself. This does
not restore prior firmware after a successful or failed flash.

This is a trusted-lab demo, not a sandbox for untrusted firmware. Restricted
queues and reviewed source remain required even though arbitrary submitted shell
commands are rejected. A board-specific adapter must be reviewed separately for
each new MCU; never replace the target string and reuse the C6 validation blindly.
