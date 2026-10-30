# Two-Test Firmware Demo

Status: ESP32-C6 is the verified firmware demo target. Both tests passed through
the complete pipeline in [run 41094006781, attempt 1](https://github.com/bem22/HILAAS-Demo/actions/runs/41094006781/attempts/1),
using source commit `f6b6df934d1f8688fdd1d199faddc1d455a559b9`.

| Test | Result | Testflinger Job |
| --- | --- | --- |
| `crc32` | PASS | `bf4bc7ca-1241-4079-bbf2-a5c0f39aeb85` |
| `uint32-wrap` | PASS | `2028759c-71a8-4ebc-98fb-71d4945f3561` |

Each job flashed once, verified the image, and passed setup, test, and cleanup.
Both used the approved board MAC `f0:f5:bd:01:83:1c` and the same freshly built
firmware. The C6 agent returned to `waiting` after both jobs.

A later attempt 2 was blocked by the anti-reflash guard, so the run's latest
status is failure; select attempt 1 for the successful demo. Its artifact was
published successfully, but that original artifact now returns HTTP 404.
The original Testflinger jobs retain their phase results and evidence archives.
Both archives were recovered and independently revalidated locally under
`artifacts/github-41094006781-recovered/`, with one directory per test.

Earlier verification runs exposed and fixed a worker Python-path mismatch
(`41091669386`) and native-USB reset selection (`41092675994`). Neither wrote
firmware. The adapter explicitly selects USB reset and matches complete
six-byte base MAC lines instead of prefixes of the C6's eight-byte MAC display.

The first hardware target is the ESP32-C6 on Pi 1, connected through its native
USB Serial/JTAG port. Firmware runs two tests from `firmware/common/selftest.c`:

| Test | Check |
| --- | --- |
| `crc32` | CRC32 of `123456789` equals `cbf43926` |
| `uint32-wrap` | Adding one to the maximum unsigned 32-bit value gives zero |

Both results are emitted repeatedly with the build nonce. Each Testflinger job
selects one test and verifies the real serial output, device MAC, and increasing
heartbeat sequence. Selecting both submits two sequential jobs from one build;
each job flashes and verifies the image once. The second job is not submitted
after a failed or ambiguous first job. Other MCU agents remain smoke-only.

## Trigger During the Demo

Open the repository's **Actions** page, select **HILAAS target pipeline showcase**,
and choose **Run workflow** on `main`:

- Target: `esp32c6-devkitc-1`.
- Preliminary build: off.
- Flash and test: on. This authorizes overwriting the C6 firmware.
- Test suite: `both`, `crc32`, or `uint32-wrap`.
- Allow downloads: on.

The runner invokes Spread, which builds in Workshop and transfers the validated
bundle to a disposable unprivileged submission worker. No SDK or compiler runs
on the Pi. The workflow summary lists each test and Testflinger job ID. Download
the workflow artifact for `targets/esp32c6-devkitc-1/hardware/report.md`,
`summary.json`, `junit.xml`, and each suite's receipt and evidence archive.

Use a new workflow dispatch for a deliberate new demo. Do not use **Re-run jobs**
to recover an uncertain submission: the ledger blocks automatic reflashing.

## Deployment Prerequisites

The runner needs Workshop 0.9.9, the ESP-IDF v5.5.3 SDK imported with sdkcraft,
and enough free space in its Workshop pool. The verified cached SDK filename for
the import is `freertos-esp-idf_amd64_ubuntu@24.04.sdk`. The AWS Workshop pool was
expanded from 8 GiB to 24 GiB after its health check reported 95% utilization.

Repository variables `HILAAS_CLIENT_CONFIG` and `HILAAS_AUTH_DIR` identify private
runner paths. The client config directory must contain only `client.json`.
`config/pi-agents/client.json` contains policy, not credentials. The create-only
server helper's `--c6-demo --check`, then `--c6-demo --output <private-path>` mode
creates a contributor scoped to the C6 queue and a refresh token valid for 24
hours. Token renewal after expiry requires an administrator; never commit tokens.

Install the reviewed source and agent extras under `/opt/hilaas-demo` in the C6
guest, then install `config/pi-agents/esp32c6.json` as root-owned
`/etc/hilaas/esp32c6.json`. Refresh only the C6 charm config after its harness and
policy are ready. Keep the hardware adapter source pinned to the tested commit.

After a prolonged hotspot outage, check both the API tunnel and Supervisor:
Supervisor may remain stopped after its required tunnel recovers. Restore the
agent service only after the tunnel is healthy and inspect existing jobs first.
