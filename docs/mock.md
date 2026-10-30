# Laptop binary pipeline

GitHub manual workflow -> AWS runner -> Spread -> host Workshop native build ->
disposable submitter container -> Testflinger -> laptop binary execution.

The shared C test also compiles into the MCU firmware; see
[portable binary self-test](../README.md#portable-binary-self-test).
This path never connects to a Pi, provisions hardware, or flashes an MCU.
Its native x86-64 success does not validate a cross-compiler or MCU execution.

## Target and source test

Spread selection: `mock-container:ubuntu-24.04:spread/mock:laptop`.
The target is `hilaas-laptop-mock`; it has no board, RTOS, or firmware architecture.
Hardware targets remain in `targets.json`; this independent mock task does not
enable micro:bit, Nucleo, flashing, or the unfinished nested firmware worker.

Spread's host-side allocation first checks the clean dispatched commit, then
stages the native recipe and C sources in a fresh, run-specific Workshop project
under `~/.local/state/HILAAS-Demo/native-builds/<run-id>`. Workshop compiles with
GCC and runs CTest before the ELF and its SHA-256 manifest become available.
The executable embeds the commit, run ID, and fresh nonce. Failed builds cannot
reach submission. Workshops stop after building; project directories and logs
are retained for inspection and are never silently reused.

`tests/test_mock.py` runs from the dispatched source inside the Spread worker.
It covers the fixed laptop handler, correlation checks, invalid submissions,
and duplicate-submission prevention. The handler in `hilaas/mock_agent.py`
runs on the laptop from its installed package, not from uploaded shell commands.
After changing it, reinstall the project in the laptop mock venv before testing.

Jobs use `name` for the run ID, three `tags` entries for nonce, SHA-256, and
commit, and literal `test_cmds: HILAAS_NATIVE_V1`. The handler never executes
that string. Test attachments carry `hilaas-selftest` and `native-manifest.json`.
Provisioning, firmware update, allocation, reservation, and arbitrary commands
are rejected. The laptop validates metadata, ELF architecture, and digest,
then executes the received bytes without recompiling. Bubblewrap isolates the
network, processes, home directory, and credentials; only system files and the
read-only executable are exposed. Runtime is limited to 20 seconds, CPU to 10
seconds, and output to 64 KiB. Bubblewrap and util-linux `prlimit` are required.
Results must correlate job UUID, run ID, nonce, commit, digest, and laptop
identity; require exit zero and all five checks; and report `hardware: false`
and `flashed: false`. These checks establish correlation, not cryptographic
attestation: only trusted maintainers may dispatch this privileged runner.
The old data-only token remains for regression compatibility, but the CLI and
workflow require a binary bundle.

## Worker and credentials

The approved AWS mock image is
`3c9090e9657b2f4bca98345178c44718c1e0776d0ffe2d230b13a5f0d39b2845`.
It was provisioned using `scripts/provision-mock-worker.sh` from the cached
Ubuntu 24.04 base and published as `hilaas-mock-worker-20261030`.
The dedicated template `hilaas-mock-template-20261030` is stopped, without autostart.

Each run gets a new unprivileged, non-nested 512 MiB LXD container, with the
`mock-v1` marker, Python, pytest, requests, and private-bridge SSH. It cannot
serve as an approved firmware build image. Spread checks ownership before
stopping and deleting its own instance.

AWS keeps the submitter token at `~/.config/hilaas-mock/submitter.json`.
Only that file is copied into the temporary worker. No agent credential,
host LXD socket, serial device, or hardware client configuration is mounted.
The mock queue owners are exactly `hilaas-laptop-mock-agent` and
`hilaas-laptop-mock-submitter`; unexpected owners abort submission.

The laptop agent uses `~/.local/state/HILAAS-Demo/mock/agent-token.json`.
Both refresh tokens were issued for seven days on 2026-10-30; renew them
privately before expiry. Do not put token values in GitHub variables or logs.

The worker reaches `http://10.152.183.194:5000` over the trusted AWS private
network. The laptop uses an SSH tunnel bound only to `127.0.0.1:15000`.
The public browser URL remains behind GitHub OAuth and is not the machine API.

## Running

On AWS, from a clean committed checkout with Workshop, Spread, and PyYAML installed:

```sh
export HILAAS_MOCK_WORKER_IMAGE=3c9090e9657b2f4bca98345178c44718c1e0776d0ffe2d230b13a5f0d39b2845
export HILAAS_RUN_ID=YOUR_UNIQUE_MOCK_RUN_ID
export HILAAS_COMMIT=$(git rev-parse HEAD)
bash scripts/run-mock-spread.sh
```

The script forces mock mode and disables hardware. Dirty or uncommitted
staged-source builds are rejected. The GitHub workflow checks out the exact
dispatched commit and uses `github-<run-id>` as the run identity.
Publish `.github/workflows/mock.yml` and the project to `main`, then manually
dispatch **HILAAS Workshop laptop binary**. It uses the registered `hilaas-demo` runner;
no pull-request or push trigger is enabled. The optional repository variable
`HILAAS_MOCK_WORKER_IMAGE` overrides the pinned deployment fingerprint.

Keep the laptop awake with its user services running:

```sh
systemctl --user status hilaas-mock-tunnel.service hilaas-mock-agent.service
```

If the agent starts before its tunnel is usable, its current Testflinger
client can cache a failed token refresh. After checking the tunnel is ready,
restart only `hilaas-mock-agent.service`. The server must report the agent as
`waiting` before another run. No automatic readiness fix is installed yet.

## Evidence and recovery

The AWS staged-source run `mock-20261030-aws-03` passed on 2026-10-30:
Spread ran the source tests, submitted one accepted mock job, retrieved the
proof and phase logs, and deleted its worker. The preceding attempts failed
preflight/schema checks; they did not execute on the laptop.
That historical run validates transport only, not a binary build or the full
GitHub-triggered binary workflow.

The complete binary workflow passed on 2026-10-30 in
[GitHub run 41026852865](https://github.com/bem22/HILAAS-Demo/actions/runs/41026852865):

- Source commit: `a2ca4c4d48dbbc11c1f37d8539d1ea8ac4b1c918`.
- Testflinger job: `4b67376d-aba8-4ee2-8df2-a793e1c69c74`, assigned to `hilaas-laptop-mock`.
- Binary SHA-256: `56f319e860f7ebcd5ce58236840b8d4a15bb96fd8d85ba7ded66ee12b4a235f4`.
- Workshop compilation and CTest passed; laptop setup, test, and cleanup returned zero.
- The downloaded GitHub artifact contained the same ELF digest and matching runtime commit/run/nonce, exit zero, and five passing checks.
- Artifact upload succeeded as `native-41026852865-1`; no hardware or flashing was involved.

Evidence is under `artifacts/<run-id>/mock-container:ubuntu-24.04:spread/mock:laptop/results/`:
`receipt.json`, `phase-results.json`, `mock-result.json`, and `artifacts.tar.gz`.
`native-output.log` is inside the evidence archive. The runner copies the
results to the colon-free `artifacts/<run-id>/laptop/` directory. GitHub uploads
that directory together with `artifacts/<run-id>/build/`, including Workshop
logs and the exact executable/manifest, as `native-<github-run-id>-<attempt>`.

The durable submission ledger is `~/.local/state/HILAAS-Demo/mock-submissions/`
on AWS. Container-created private ledger files may require administrator
access because of UID mapping; the exported run artifacts are runner-readable.
Timeouts do not cancel or repost jobs. GitHub reruns are blocked. Inspect the
saved UUID and server state before choosing a new run ID; never delete a ledger
entry to retry an uncertain submission. Normal worker cleanup preserves it.
