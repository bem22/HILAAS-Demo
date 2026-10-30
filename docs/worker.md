# Disposable Spread worker

## Worker isolation

Spread's AdHoc backend connects over SSH and may modify its execution system.
Never point it at the laptop itself or a Pi agent host. The backend in this
repository defaults to a cached **virtual-machine** image fingerprint, creates
a new `hilaas-spread-*` instance in LXD's `default` project, and verifies a
template marker before configuring its temporary root password. It never
mounts the host LXD socket into the guest or into Workshop.

An explicit `HILAAS_WORKER_TYPE=container` mode exists for the AWS trial, but
that trial is blocked by Workshop's ZFS requirement; it is not an approved
alternative worker yet. The unit tests do not prove host KVM availability,
guest networking, nested LXD, or SDK compatibility. Neither the laptop nor AWS
has a published, approved firmware worker image. A separate mock-only container
image is validated; see [mock setup](mock.md). Do not repurpose existing Juju
containers as worker templates.

## Template contract

Provision a dedicated Ubuntu 24.04 AMD64 LXD VM on a private local bridge,
with Internet egress, at least 6 GiB RAM, and a 40 GiB root disk. Install/configure
these **inside that disposable VM**, not on the laptop:

- OpenSSH, with password login permitted for root for Spread's temporary
  per-run password. Do not expose this VM's SSH through a hotel/public interface.
- LXD initialized with its own internal network and storage, independent of
  the laptop's LXD and Juju infrastructure.
- Workshop matching the tested project recipe (current reference: 0.1.29),
  SDKcraft, and the local `try-freertos-esp-idf` SDK with ESP-IDF 5.5.3. The
  reference SDK source is the existing laptop checkout at
  `~/code/freertos-esp-sdk`; verify its remote and revision before packaging it.
  Retain any source paths used by `sdkcraft try` inside the image.
- A Python environment at `/opt/hilaas-client` containing the `client` extra
  dependencies from this repository. Record the resolved dependency versions
  as part of the template's provisioning records. Stage the Testflinger CLI
  source as `inputs/cli`; it is installed before the extra because the package
  was unavailable from the configured public package index.
- A file `/etc/hilaas-spread-template` containing exactly `v1` on its own line,
  created only after the disposable template's setup has been reviewed.

The client environment runs code copied into the worker by Spread; it does not
need an editable installation pointing at an old checkout. Before publishing
the VM image, test `workshop launch` and a build with the project copied to
`/home/ubuntu/HILAAS-Demo`. Remove that test project and its Workshop afterwards,
so each Spread job can launch its own fresh project there. Preserve downloaded
SDK caches. Remove all credentials, job receipts, and previous evidence before
publishing the image. Shut down the template cleanly and publish it locally
with LXD. Use the resulting full fingerprint as `HILAAS_WORKER_IMAGE`.

The source SDK name is intentionally the existing local `try-` SDK, not an
invented store channel. Import/provision it in the template; merely installing
Workshop on the host does not satisfy this requirement.

## Host prerequisites and first validation

The host needs LXD access, Python 3.10+, `flock`, and Canonical Spread. With an
existing Go installation, the release reference to evaluate is:

```sh
go install github.com/canonical/spread/cmd/spread@2026.07.12
```

This downloads source/dependencies. Add the resulting Go bin directory to PATH.
Before allocating anything, validate selection with the actual Spread binary:

```sh
spread -list local-vm:spread/boards:esp32c6
```

Expected: exactly `local-vm:ubuntu-24.04:spread/boards:esp32c6`.
The native parser requires the lowercase project name `hilaas-demo` and an
artifact path without a trailing slash (`results`).
The wrapper serializes all HILAAS runs on this host. Do not use Spread `-reuse`,
`-repeat`, or parallel invocations of this backend: lifecycle and hardware run
identity are deliberately single-run for this first implementation.

## Local setup checkpoint

Spread is installed at `~/.local/bin/spread` from release `2026.07.12`
(module revision `32cad07bfd7f`), using checksum-verified Go 1.27.3 under
`~/.local/opt/go1.27.3`. Native C6 job selection passes. Put `~/.local/bin` on
PATH before using the run wrapper.

The dedicated `hilaas-worker-template-20261030` VM is running in the local LXD
`default` project on `lxdbr0`, with 4 CPUs, 6 GiB RAM and a 40 GiB root disk.
Its ownership label is `user.hilaas-template=20261030-spread-worker`.
Ubuntu cloud-init completed and outbound HTTPS was verified. Its base image is
`a80e9c9fd0d484dec024da3e9e264bbad4ce159b5312d6e149ef6ad532e18e43`.
This is the Ubuntu base fingerprint, **not** an approved `HILAAS_WORKER_IMAGE`.

Project provisioning inputs and the existing SDK archive are staged under
`/opt/hilaas-inputs` inside that VM. The copied SDK SHA-256 is
`751d22c89804b0ee9635c4bbe691dec0cddf0a89fd79973bf15debb828c0e849`.
Its actual ESP-IDF version still needs validation inside Workshop.

The host uses locally built Workshop 0.1.29 and SDKcraft 0.1.13 snaps.
Their cached files are root-readable only. An administrator must copy them into
the VM as `/opt/hilaas-inputs/workshop.snap` and
`/opt/hilaas-inputs/sdkcraft.snap`; do not silently substitute newer Store
versions. Then run:

```sh
lxc --force-local --project default exec hilaas-worker-template-20261030 -- \
  bash /opt/hilaas-inputs/project/scripts/provision-worker.sh /opt/hilaas-inputs
```

The script is guarded against execution outside a named KVM template. It
installs guest prerequisites, imports the SDK, and configures SSH, but does
not create the approval marker or publish an image. Nested Workshop, the SDK,
and Spread allocation/SSH/discard remain unverified. No hardware jobs have
been submitted. The normal build wrapper also still requires a clean committed
source revision; this setup does not bypass that check or create a commit.

## Credentials, networking, and recovery

Hardware mode mounts only three explicitly selected resources: the client
configuration directory read-only, a dedicated refreshable authentication directory, and
a persistent submission ledger. Build-only mode mounts none of them. The auth
directory must be dedicated to HILAAS, not your whole home or normal XDG config.
The configuration must be named `client.json` and be the only entry in its
directory. This prevents mounting unrelated neighboring files into the VM.
Spread scripts must not print tokens or enable shell tracing around secrets.

Both the worker and Pi agent guest must resolve and reach the existing laptop
Testflinger endpoint. The laptop's ClusterIP is not a routable endpoint for the
Pis. Do not hard-code the historical pod or Wi-Fi IPs into the repository.
Use HTTPS or a trusted private network/tunnel. Setting `allow_trusted_http` is
an explicit acknowledgement, not encryption and not suitable for hotel Wi-Fi.

Normal Spread discard stops/deletes only a VM with a matching random ownership
marker. A failed or interrupted run may leave a VM and a lease under
`~/.local/state/HILAAS-Demo/worker-<run-id>.json`. Inspect those first. To request
guarded cleanup from the repository root, with the original run ID:

```sh
HILAAS_RUN_ID=ORIGINAL_RUN_ID python -m hilaas.worker discard
```

Cleanup never deletes submission records. Before any new hardware run, inspect
the recorded Testflinger job. Do not flush CNI/firewall rules or reset LXD to
recover a demo worker.

## AWS deployment

The AWS host will run the GitHub runner, Spread, Workshop workers, and
Testflinger. Testflinger must follow the
[official MicroK8s/Juju deployment guide](https://ubuntu.com/docs/testflinger/latest/how-to/administer-server/deploy-testflinger-server/),
not the development Docker Compose topology:

- MicroK8s `1.34-strict/stable` with DNS, hostpath storage, and NGINX ingress.
- A Juju controller on LXD and separate `testflinger-db` and `testflinger` models.
- MongoDB machine charm on `6/stable`, offered to the Kubernetes model.
- `testflinger-k8s` and `nginx-ingress-integrator` charms with explicit relations.

The guide requires at least 4 CPU cores, 6 GB RAM, and 30 GB free disk. For
this combined build-and-server host, 4 vCPUs and 16 GiB RAM are recommended.
The user explicitly approved a trial on the existing 2-vCPU, 8-GiB instance
with its 120-GiB disk. Set `HILAAS_ALLOW_UNDERSIZED=yes` to acknowledge this
exception when running `scripts/provision-cloud-host.sh`; the default CPU
guard remains enabled. That script installs prerequisites only.

The trial reached active status on 2026-10-30:

- Juju 3.6.29, controller `localhost-controller`, controller memory limit 2 GiB.
- MicroK8s 1.34.9 on `1.34-strict/stable`.
- MongoDB 6.0.24, charm revision 265 on `6/stable`, Ubuntu 22.04 LXD machine,
  memory limit 2 GiB, active cross-model database relation.
- Testflinger charm revision 391 on `latest/stable`, both pod containers ready.
- NGINX Ingress Integrator revision 203, active, reporting `127.0.0.1`.

`scripts/provision-cloud-server.sh` deploys the database and server charms.
Its generated signing key is private under `~/.config/hilaas-cloud`, with a
private copy under `~/snap/juju/common/hilaas-cloud` for snap-confined Juju.
Do not print or commit these files.

`scripts/provision-cloud-ingress.sh` renders the installed official NGINX
addon manifest with host ports restricted to `127.0.0.1` before applying it.
Re-enabling the stock ingress addon could overwrite that restriction; review
it before doing so. The ingress hostname is `testflinger.local`. Verified:
`GET /v1/agents/queues` returns HTTP 200 through ingress, anonymous access to
the administrator endpoint returns 403, and port 80 is unreachable on the
host's non-loopback address. `/v1/queues` is not a queue-list endpoint.

At idle the host used approximately 2.4 GiB RAM with 5.1 GiB available, and
had 99 GiB free disk. All Kubernetes pods were running with zero restarts.
This verifies startup and basic API routing, not concurrent build capacity,
long-term kernel compatibility, or hardware operation. Administrator setup,
runner registration, Workshop worker validation, and secure Pi access remain.
The cloud database is new; laptop accounts and history have not been migrated.

The abandoned `hilaas-services` Compose container is stopped with autostart
disabled. Its volumes and generated credentials are preserved; it is not the
active deployment. The MongoDB image failed on the host's 7.0 kernel, and the
MongoDB 6 machine charm subsequently started and reached active status on that
same kernel. This is a startup observation, not a compatibility certification.
No database compatibility check was bypassed.

For trusted manual builds, the user approved disposable unprivileged LXD
containers on this single AWS host. Set `HILAAS_WORKER_TYPE=container`
explicitly; the allocator still defaults to VMs on the laptop. Containers
share the host kernel and are not suitable isolation for untrusted PR builds.
The container worker template and complete Spread lifecycle remain unverified.
Keep the Pi agents under their existing Juju controllers and Testflinger
ownership; they are not Spread execution workers. The laptop deployment has
not been removed or migrated.

## Public browser access

The live URL is `https://testflinger.16-16-193-47.sslip.io`. DNS resolves to
the current instance; use an Elastic IP before relying on this as a permanent
bookmark. A changed public IP requires corresponding DNS, certificate, and
GitHub OAuth callback updates.

The user created the GitHub OAuth application and entered its credentials
directly through `scripts/configure-browser-oauth.py`. They are stored in
`testflinger/hilaas-browser-oauth`; do not print or export that Secret.
`config/browser-gateway.yaml` deploys OAuth2 Proxy 7.15.0 and permits only GitHub
users `bem22`, `aleccondry`, `locnnil`, `pedroishimaru`, and `fderepas` through
repeated `--github-user` arguments. No organization, team, or repository rule is
configured. It uses secure HTTP-only cookies and disables request logging.
This allowlist replaces the `canonical` organization rule. The cookie name is
`__Host-hilaas_users_v2`, requiring fresh login after the username correction.
The provider still requests its default `user:email read:org` scopes, but access
does not depend on Canonical membership or organization approval.
This grants browser access, not Testflinger administrator permissions.
The gateway forwards to the internal `hilaas-testflinger-api` Service. Forged
user headers and an invalid bearer token still produced a login redirect for
both `/` and `/v1/agents/queues`; an actual browser login remains unverified.

The direct Testflinger-to-ingress relation was removed. NGINX Ingress
Integrator revision 203 requires a relation and prioritizes its service name
over configuration, so it cannot independently target this gateway. It is now
idle, waiting for a relation. A dedicated Kubernetes Ingress in
`config/browser-gateway.yaml` routes the public hostname exclusively to the
gateway; Testflinger and MongoDB remain managed by their Juju charms.
Do not restore the direct ingress relation: that could bypass browser login.
This gateway is for browser access, not a replacement for Testflinger client
credentials or the future private agent connection.

`config/browser-tls.yaml` uses cert-manager and Let's Encrypt HTTP-01 with
automatic certificate renewal. On 2026-10-30, the user opened inbound HTTP and
HTTPS in AWS, certificate issuance completed, and public HTTPS was enabled.
External checks used normal certificate verification and confirmed HTTP-to-HTTPS
redirection, GitHub authorization with the exact registered callback, and Secure,
HttpOnly, SameSite=Lax cookies. Forged user headers and an invalid bearer token
could not access the API; `/oauth2/auth` returned 401 without a session and the
old `testflinger.local` hostname returned 404 rather than exposing the API.
A real user-completed GitHub sign-in still needs confirmation.

AWS instance
`i-04b7221088fc1dc8e` uses `sg-0a6252cccafebf59f` (`launch-wizard-1`) in
`eu-north-1`. Keep TCP 80 reachable for certificate renewal and HTTP redirects,
and TCP 443 reachable for browser access. Do not expose MongoDB, the Kubernetes
API, or the internal API service.

`scripts/publish-browser-ingress.py` validates every ingress route before
opening listeners. Current stage is `https`: ports 80 and 443 are enabled,
while the controller health port remains loopback-only. To re-enable HTTPS
after deliberately returning to a private stage, run on the AWS host:

```sh
microk8s kubectl wait --for=condition=Ready certificate/hilaas-browser \
  -n testflinger --timeout=300s
python3 /home/ubuntu/publish-browser-ingress.py https \
  --expected-hostname ip-172-31-14-120
```

Then verify trusted HTTPS, login redirects, rejected forged identities, and a
real allowlisted-user browser login. The `private` stage of the same script restores
loopback-only listeners without deleting services or credentials. Do not run
the stock ingress addon installer again without reviewing its listener changes.

## AWS worker and runner checkpoint: 2026-10-30

The unprivileged `hilaas-worker-template-aws` container was created from the
cached Ubuntu 24.04 image with two CPUs, 4 GiB RAM, nesting enabled, and no
host LXD socket mount. It is now stopped with autostart disabled. No approval
marker or worker image was published.

Installed inside the template: LXD 5.21.8, Workshop 0.1.29, SDKcraft 0.1.13,
the imported `try-freertos-esp-idf` SDK, OpenSSH, and the client environment.
SDKcraft needs the root user-session bus during noninteractive provisioning;
the provisioner now starts it explicitly. The client dependencies passed
`pip check`. Its source is the local Testflinger CLI snapshot at
`a95171cd0c23cc8ea8ad676286eaaec2908eaceb`, archived with `git archive REV cli`.
The archive SHA-256 is
`13e6d1c38b171422ef4957998ac413d05202436639c80b61e3bc51e0ec1b2a50`.
That revision was not fetchable from the public upstream remote, so preserve
the staged source under `/home/ubuntu/hilaas-worker-inputs/cli`.

Workshop launch failed before SDK downloads: it tries to create its `workshop`
ZFS pool, which an unprivileged nested container cannot create. The installed
version also rejects an existing pool with a non-ZFS driver. Do not work around
this by making the container privileged or mounting host storage/devices. A
supported ZFS-capable worker environment, or a separately approved and tested
Workshop backend change, is needed before ESP-IDF or Zephyr builds can run.

GitHub Actions runner 2.338.2 is installed at
`/home/ubuntu/actions-runner-hilaas` on AWS. The official Linux x64 archive
passed SHA-256 verification:
`baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40`.
It is registered as `hilaas-aws` for `bem22/HILAAS-Demo`; the service
`actions.runner.bem22-HILAAS-Demo.hilaas-aws.service` is active.
`scripts/provision-cloud-runner.sh` provides guarded `install` and `register`
actions. Registration is fixed to `bem22/HILAAS-Demo`, name `hilaas-aws`, and
label `hilaas-demo`. It reads the short-lived registration token without echo
and passes it through `ACTIONS_RUNNER_INPUT_TOKEN`, not command arguments.
Enter it directly in an interactive terminal; never put it in chat or files.
Registration then installs and starts the runner service as `ubuntu`.

Only trusted, manually dispatched workflows should use this host-side runner:
its LXD group access is privileged and the server shares the host. Source still
needs an authorized commit and publication before the workflow can run; no
provenance guard has been bypassed. Pi connections, agents, flashing, and
hardware tests are explicitly on hold.

## AWS host Workshop checkpoint

On 2026-10-30, Workshop was installed directly on the AWS host instead of
inside the unprivileged firmware template. Its separate `workshop` ZFS pool
is 8 GiB; existing Juju storage pools were not changed.

Workshop 0.1.29 requires LXD >=6.3. With approval for a maintenance interruption,
the two running Juju containers were stopped cleanly, LXD was stopped, and
`snap save lxd` produced snapshot set `1` (9.6 GB, LXD revision `40958`).
The host was upgraded to LXD 6.10.1, revision `41228`, on `6/stable`, and the
same Juju containers were restarted. Preserve the private backup; it contains
service data and credentials and must not be published. Restore has not been tested.

The old Workshop then failed because it supplied obsolete NVIDIA configuration
keys to LXD. Updating only the host Workshop with
`sudo snap refresh workshop --amend --channel=latest/stable --classic`
installed Store release `0.9.9-f8fdf0d` and resolved the launch failure.
The stopped nested firmware template and its SDK versions remain unchanged.

The native definition from `config/native-workshop/`, including its `.workshop`
directory, and the `firmware/common/` and `firmware/native/` source directories
are staged under `/home/ubuntu/hilaas-native-workshop` on AWS. Its native-tools
project SDK installs GCC and CMake inside Workshop, not on the host.

These commands succeeded on AWS:

```sh
workshop -p /home/ubuntu/hilaas-native-workshop launch
workshop -p /home/ubuntu/hilaas-native-workshop run --non-interactive -- build
```

Workshop `hilaas-native-build` is Ready. GCC 13.3.0 compiled the x86-64 ELF
`build/native/hilaas-selftest`, and CTest passed its portable test (five checks).
The output SHA-256 was
`c2eae0091518968991d5c82f3f23998639323532008073cf34cd125c9b760269`.
Testflinger was 2/2 Ready, both Juju containers were Running, and the GitHub
runner service was active after validation.

This checkpoint was a direct Workshop build of staged source, not a
GitHub-triggered binary pipeline. The native workflow now invokes Workshop
from Spread and delivers its executable through Testflinger; see the
[laptop binary pipeline](mock.md) for its contract and verification status.
The old staged binary is not reused by that workflow. No new Testflinger job
or hardware operation was performed during this checkpoint; ESP-IDF and
Zephyr cross-builds remain unverified.
