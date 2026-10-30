# HILAAS-Demo

GitHub -> self-hosted runner -> Canonical Spread -> Workshop build ->
Testflinger client -> AWS Testflinger server -> Pi agent -> MCU.

Spread owns the board matrix. Each selected job builds, validates its bundle,
and optionally submits and waits for one Testflinger hardware job. The GitHub
workflow does not duplicate that matrix. Spread never provisions the Pi agent
hosts; they remain managed by their own Juju controllers.

## Implementation status

- Laptop mock: the source-level tests and AWS Spread -> Testflinger -> laptop
  round trip passed on 2026-10-30. No hardware was used. See [mock pipeline](docs/mock.md).
- Implemented: ESP32-C6 DevKitC-1 ESP-IDF build recipe and proof firmware,
  immutable firmware bundle, Spread task and local disposable-VM backend,
  guarded Testflinger client adapter, and host-controlled pytest agent adapter.
- Unit tests use synthetic firmware and mocked network/device setup. They do
  not claim that a C6 build, VM allocation, or physical flash has succeeded.
- Runtime prerequisites: install Spread, prepare a worker image, register a
  runner for this repository, and configure a reachable server and Pi agent.
- micro:bit and Nucleo are deliberately disabled in `targets.json` until exact
  boards are identified. Their Twister adapter is not implemented yet.

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
only the selected client config/profile and submission ledger are mounted into
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

The separate **HILAAS laptop mock** workflow checks out the dispatched commit
and runs only `mock-container:ubuntu-24.04:spread/mock:laptop`. It does not
require Workshop or firmware builds. See [mock operation and evidence](docs/mock.md).

Use a separate runner registration for `bem22/HILAAS-Demo` with labels
`self-hosted`, `Linux`, `X64`, `hilaas-demo`. Leave `freertos-demo` registered to
its existing repository. Do not run two registrations concurrently against the
same hardware resources without deliberate coordination.

The workflow runs only from `main` using `workflow_dispatch`. It uses the
pre-existing clean checkout at `$HOME/code/HILAAS-Demo`, checks that HEAD matches
the dispatched commit, and never resets the user's working tree. Configure
repository variables `HILAAS_WORKER_IMAGE`, `HILAAS_CLIENT_CONFIG`, and
`HILAAS_AUTH_DIR` (paths, not passwords). Build and hardware operations both
require the download checkbox; hardware also requires its separate checkbox.

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
