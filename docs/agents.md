# Pi agent deployment contract

These instructions do not replace or restart the existing agents automatically.
First confirm Pi identities, controller health, and that the selected device
has no job in progress. The two restored Pis originally shared a controller
UUID; retain the first controller and finish validating the fresh second
controller before deploying independent agents. Renaming a hostname alone
does not resolve cloned controller identities.

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
