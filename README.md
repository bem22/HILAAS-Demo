# HILAAS-Demo

GitHub -> self-hosted runner -> Canonical Spread -> Workshop build ->
Testflinger client -> AWS Testflinger server -> target agent -> laptop or MCU.

Spread owns the target execution. Each selected job builds, validates its bundle,
and optionally submits and waits for one Testflinger hardware job. The GitHub
workflow does not duplicate that matrix. Spread never provisions the Pi agent
hosts; they remain managed by their own Juju controllers.

## Target pipeline and live stages

Dispatch **HILAAS target pipeline showcase** and select `laptop` or
`esp32c6-devkitc-1`. The laptop is the verified native reference target; an MCU
selection does not imply its hardware integration has been validated. MCU mode
defaults to build-only. SDK downloads and physical flashing require separate
explicit permissions and the existing configured worker/device prerequisites.

For a preliminary Zephyr build, select `microbit`, `nucleo`, or `zephyr-all`,
enable `preliminary_build` and `allow_downloads`, and leave `flash_and_test`
off. `microbit` means **micro:bit V2 / nRF52833**; `nucleo` means
**NUCLEO-H753ZI / STM32H753**, PCB marking MB1364E. `zephyr-all` builds both.
Each target gets a fresh host Workshop from the shared Zephyr/ARM recipe;
Spread does not allocate a submitter worker or contact Testflinger in this mode.
Both builds use the portable self-test application and embed commit, run, board,
and nonce identities. Building the self-test does not mean it ran on hardware.

After all ELF/HEX/BIN files are validated and Workshops stopped, Spread fails
deliberately with `PRELIMINARY_BUILD_COMPLETE` and a message that the Testflinger
handoff was blocked. The workflow is intentionally red. Its evidence step must
still pass and its artifact must contain `preliminary.json`, both target bundles,
Workshop logs, and the report. An SDK/compiler failure or missing binary is
reported as **BUILD INCOMPLETE**, not as the expected stop. No credentials or
available Testflinger server are needed. Do not enable hardware until separate
identity, flashing, and runtime-proof adapters have been implemented.

Both paths use the same stage recorder and GitHub log renderer. Open the Spread
step for live collapsible transitions: Workshop launch/build, bundle validation,
worker allocation/tests, Testflinger preflight/submission/attachments/wait/evidence,
and cleanup where instrumented. The Summary includes per-target status, UTC
timestamps, and measured durations; the artifact includes the raw `progress/`
events and `showcase.md`. Missing events are marked unobserved, never passed.
The wait duration includes queueing and cleanup, not just device runtime.

The recorder writes only fixed-schema metadata to a dedicated per-run directory.
A worker can write only its progress subdirectory, not the host event directory.
The host streams those events while Spread runs; it does not depend on buffered
Spread command output. Events are operational diagnostics, not trusted device
proof. Unit-test subprocesses do not emit fake stages into a real run.

Proof remains adapter-specific: native ELF identity/digest and self-tests for
the laptop; firmware manifest, partition policy, device MAC, flash count, and
serial nonce/sequence proof for ESP32-C6. A build-only report explicitly says
hardware execution was not run. Adding another MCU requires an implemented
build/device/evidence adapter, not merely a new target name. micro:bit and Nucleo
remain hardware-disabled but have confirmed preliminary build profiles. The device registry remains a proposal.

Current MCU blockers are unchanged: the nested Workshop worker path has not
been validated, the queue-owner contract needs reconciliation with the deployed
API, and no physical build/flash/run has been certified. This reporting change
does not grant new device permissions or bypass those guards.

## Implementation status

- Laptop binary: the manual workflow connects GitHub -> runner -> Spread ->
  Workshop -> Testflinger -> laptop, executing the received native ELF in a
  sandbox. The full workflow and artifact upload passed on 2026-10-30 in
  [run 41026852865](https://github.com/bem22/HILAAS-Demo/actions/runs/41026852865).
  See [laptop pipeline](docs/mock.md) for verified binary and job identities.
- Implemented: ESP32-C6 DevKitC-1 ESP-IDF build recipe and proof firmware,
  immutable firmware bundle, Spread task and local disposable-VM backend,
  guarded Testflinger client adapter, and host-controlled pytest agent adapter.
- Unit tests use synthetic firmware and mocked network/device setup. They do
  not claim that a C6 build, VM allocation, or physical flash has succeeded.
- Runtime prerequisites: install Spread, prepare a worker image, register a
  runner for this repository, and configure a reachable server and Pi agent.
- micro:bit V2 and NUCLEO-H753ZI have build-only profiles in `targets.json`.
  Hardware execution remains disabled; their Twister/device adapters are not implemented yet.

The existing S3 workflow and runner registration are independent and unchanged.
Do not use an S3 binary, MAC, flash configuration, or queue for the C6.

## Local checks (no SDK downloads or hardware)

Use Python 3.10 or newer with pytest and PyYAML installed:

```sh
python -m unittest discover -s tests -v
python -m pytest -q
python -m pytest --collect-only -q agent/test_c6.py
python -m hilaas.cli targets
```

Optional dependencies are declared in `pyproject.toml`. Installing
`.[test]`, `.[client]`, or `.[agent]` needs network access unless cached.
The client uses `testflinger-cli` authentication with a no-retry requests
transport. Its compatibility must be checked when updating the client package.

For named offline failure demonstrations, supervised USB drills, and a proposed
agent-independent device registry, see [the demo runbook](docs/demo-scenarios.md).
Run `python scripts/demo-scenarios.py --list` to see the prepared scenarios.

## Portable binary self-test

`firmware/common/selftest.c` is the same SDK-independent C test on the laptop
and ESP32-C6. It checks CRC32 against known answers for empty, ASCII, and binary
data, incremental CRC processing, and unsigned 32-bit wraparound. The binary
test vector is independently verified against Python's standard `zlib.crc32`.

Build and execute a real native binary on the laptop:

```sh
cmake -S firmware/native -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native
ctest --test-dir build/native --output-on-failure
./build/native/hilaas-selftest
```

Success ends with `HILAAS_SELFTEST_V1 status=pass checks=5 failures=0` and exit
code zero. A failed check prints its expected and actual values and returns a
nonzero exit code. CTest treats a nonzero exit or a failure marker as failure.

The ESP-IDF component compiles the same source into the MCU firmware.
`app_main` runs it before emitting any normal C6 nonce/sequence proof; failure
stops that proof, so the hardware harness cannot pass. Results go to the serial
console. No OS-specific implementation or Python runtime is needed on the MCU.

The laptop binary is native to the laptop; the MCU firmware must be separately
cross-compiled. Passing the laptop test does not validate the MCU toolchain,
firmware packaging, peripherals, or flashing. The native binary was also built
and passed CTest inside Workshop 0.9.9 on AWS with LXD 6.10.1 and dedicated ZFS
storage; see [the host Workshop checkpoint](docs/worker.md#aws-host-workshop-checkpoint).
The old nested firmware worker remains blocked, and no MCU build or run is claimed.
The laptop workflow builds this executable in Workshop, transfers it through
Testflinger, and checks its digest, embedded commit/run/nonce, and runtime output.
No Testflinger job is submitted by the local build commands above.

## First build

1. Follow [the worker setup](docs/worker.md). No VM image, SDK installation,
   networking change, or runner registration is performed by cloning this repo.
2. Review and commit the source locally. Real runs reject dirty checkouts and
   embed the full source commit in the bundle. Push only when ready to publish.
3. Configure a cached, pre-provisioned VM image and explicitly permit downloads:

```sh
export HILAAS_WORKER_IMAGE=YOUR_LOCAL_64_HEX_VM_IMAGE_FINGERPRINT
export HILAAS_ALLOW_DOWNLOADS=yes
bash scripts/run-spread.sh build
```

This is an opt-in command. It allocates a disposable local VM and launches a
Workshop in it. It may download a base, SDK dependencies, and source modules;
do not run it on a metered hotspot. Existing host Workshop projects are not used.
The proof firmware uses the C6 DevKit's UART console at 115200 baud.

Artifacts land under `artifacts/<run-id>/` in Spread's per-job directories.
The bundle contains three firmware files and a manifest. The manifest is
written after a successful build and checked before submission. The build
uses ESP-IDF 5.5.3 and a reviewed 1 MiB factory partition, with no whole-chip erase.
ESP-IDF headers and the generated nonce header become available in Workshop;
host editor C diagnostics are expected until a build environment is configured.

## Hardware mode

Follow [the agent setup](docs/agents.md) first. Do not submit to the restored
prototype agents or duplicate cloned agent identities.

Keep the completed client configuration and authentication profile outside the
repository, for example under a private directory in your home directory. Set:

```sh
export HILAAS_CLIENT_CONFIG=/absolute/private/path/client.json
export HILAAS_AUTH_DIR=/absolute/private/path/testflinger-config
export XDG_CONFIG_HOME="$HILAAS_AUTH_DIR"
testflinger-cli --server YOUR_SERVER_URL login
```

Enter credentials locally, never in committed files or chat. Use a dedicated
contributor account with access to a queue restricted to that same submitter.
Authentication refresh tokens remain writable in the dedicated private profile;
only the selected client config/profile, submission ledger, and dedicated progress directory are mounted into
the worker. None of these paths is inside the Workshop project mount.

```sh
export HILAAS_ENABLE_HARDWARE=yes
export HILAAS_RUN_ID=YOUR_UNIQUE_RUN_ID
bash scripts/run-spread.sh hardware
```

The submitter checks device MAC and an administrator-approved partition-table
hash, records the job UUID before any POST, uploads once, then polls. The
durable ledger under `~/.local/state/HILAAS-Demo/submissions/` survives worker
deletion. Its key includes run ID and target. Do not delete it or change run IDs
to get around an uncertain submission: inspect the recorded job first. A timeout
does not cancel or resubmit a running job.

Reports include the submission receipt, phase results, and a bounded evidence
archive with JUnit, serial/flash logs, returned manifest, and identity/nonce proof.
All required phases and the evidence must pass for Spread to succeed. Failed
jobs preserve available evidence; missing artifacts are failures, not passes.
The nonce correlates a run to firmware; it is not firmware authorization or
hardware attestation. Trusted repository access and restricted queues matter.

## GitHub Actions

**HILAAS target pipeline showcase** is the single current workflow. It replaces
the old MCU workflow and supports the laptop and ESP32-C6 target selections.
The historical `mock.yml` filename is retained to preserve workflow history;
its display name, inputs, and reporting are target-neutral. The superseded
`demo.yml` is removed, not left as a second dispatch entry point.
For the laptop, Spread launches a fresh host Workshop build before creating the
isolated Testflinger submitter worker. See [laptop operation and evidence](docs/mock.md).

Use a separate runner registration for `bem22/HILAAS-Demo` with labels
`self-hosted`, `Linux`, `X64`, `hilaas-demo`. Leave `freertos-demo` registered to
its existing repository. Do not run two registrations concurrently against the
same hardware resources without deliberate coordination.

The workflow runs only from `main` using `workflow_dispatch`. It uses the
Actions-managed checkout pinned to the dispatched commit and checks that HEAD
matches it. Configure
repository variables `HILAAS_WORKER_IMAGE`, `HILAAS_CLIENT_CONFIG`, and
`HILAAS_AUTH_DIR` (paths, not passwords) for MCU operations. MCU build and hardware
operations both require the download checkbox; hardware also requires its
separate checkbox. The laptop does not enable physical hardware access.

Do not enable pull-request triggers on this persistent self-hosted runner.
Labels select runners; they are not a security boundary. The public repo must
not accept untrusted changes for privileged hardware execution without review.
GitHub hardware reruns are blocked; download artifacts and inspect the existing
job instead. Cancellation may leave an in-flight Testflinger job running.

## Zephyr expansion

See [the Zephyr contract](docs/zephyr.md). The requested SDK is Canonical's
`zephyr` track `26.04/stable`, mapped by its publishing workflow to upstream
Zephyr 4.4. It is not an upstream release named 26.04. No Zephyr sources or
toolchains are downloaded by the current C6 task.
