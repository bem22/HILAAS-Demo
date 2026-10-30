# Laptop mock pipeline

The mock path is separate from all firmware and Pi work:

GitHub manual workflow -> AWS runner -> Spread -> disposable mock container ->
private Testflinger API -> laptop mock agent -> correlated JSON evidence.

## Target and source test

Spread selection: `mock-container:ubuntu-24.04:spread/mock:laptop`.
The target is `hilaas-laptop-mock`; it has no board, RTOS, or firmware architecture.
Hardware targets remain in `targets.json`; this independent mock task does not
enable micro:bit, Nucleo, flashing, or the unfinished Workshop worker.

`tests/test_mock.py` runs from the dispatched source inside the Spread worker.
It covers the fixed laptop handler, correlation checks, invalid submissions,
and duplicate-submission prevention. The handler in `hilaas/mock_agent.py`
runs on the laptop from its installed package, not from uploaded shell commands.
After changing it, reinstall the project in the laptop mock venv before testing.

Jobs use `name` for the run ID, one `tags` entry for a fresh nonce, and the
literal `test_cmds: HILAAS_MOCK_V1`. The handler never executes that string.
Provisioning, firmware update, allocation, reservation, and arbitrary commands
are rejected. Results must correlate the job UUID, run ID, nonce, and mock
identity, and report `hardware: false` and `flashed: false`.

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

On AWS, from a checkout with the current mock source:

```sh
export HILAAS_MOCK_WORKER_IMAGE=3c9090e9657b2f4bca98345178c44718c1e0776d0ffe2d230b13a5f0d39b2845
export HILAAS_RUN_ID=YOUR_UNIQUE_MOCK_RUN_ID
bash scripts/run-mock-spread.sh
```

The script forces mock mode and disables hardware. Manual staged-source runs
do not claim commit provenance. The GitHub workflow checks out the exact
dispatched commit and uses `github-<run-id>` as the run identity.
Publish `.github/workflows/mock.yml` and the project to `main`, then manually
dispatch **HILAAS laptop mock**. It uses the registered `hilaas-demo` runner;
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
This validates the AWS command-line path, not a GitHub-triggered workflow run.

Evidence is under `artifacts/<run-id>/mock-container:ubuntu-24.04:spread/mock:laptop/results/`:
`receipt.json`, `phase-results.json`, `mock-result.json`, and `artifacts.tar.gz`.
The GitHub workflow uploads that directory as a run artifact.

The durable submission ledger is `~/.local/state/HILAAS-Demo/mock-submissions/`
on AWS. Container-created private ledger files may require administrator
access because of UID mapping; the exported run artifacts are runner-readable.
Timeouts do not cancel or repost jobs. GitHub reruns are blocked. Inspect the
saved UUID and server state before choosing a new run ID; never delete a ledger
entry to retry an uncertain submission. Normal worker cleanup preserves it.