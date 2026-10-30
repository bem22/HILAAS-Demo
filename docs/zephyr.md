# Zephyr and micro:bit provisioning

Both micro:bit V1 and V2 were requested on 2026-10-30. Provisioning is
build-only; Pi connectivity, agents, flashing, and device testing are on hold.

`config/zephyr-workshop/workshop.yaml` defines an isolated Ubuntu 26.04 Workshop
with `zephyr` on `26.04/stable`, `zephyr-sdk-ng` and `zephyr-arm` on
`1.0.1/stable`, and `uv` on `latest/stable`. Its connections select the ARM
cross-toolchain, SDK NG bundle, and Python environment. `check-tools` is intended
to verify west, the ARM compiler, and the release's actual micro:bit board IDs.

The definition is staged at `/opt/hilaas-zephyr` in the stopped AWS template.
Workshop failed to initialize its required ZFS storage pool before it could
download these SDKs. Therefore Zephyr and ARM tools are **not yet installed or
validated**, and neither micro:bit variant has been compiled. No board identifier
has been assumed or hardware target enabled. Resolve the worker storage blocker
documented in `worker.md`, then launch this definition and validate both boards.

Canonical's `zephyr` SDK publishing workflow maps the 4.4 version branch to both
the `4.4/stable` and `26.04/stable` tracks:
[SDK publishing workflow](https://github.com/canonical/zephyr-sdk/blob/main/.github/workflows/upload.yml)

Use the requested `26.04/stable` track for the forthcoming Zephyr board recipes.
Record the resolved SDK revision, upstream Zephyr revision, module revisions and
toolchain version in each build manifest. A track name alone is not an immutable
pin. The current SDK configuration builds on Ubuntu 26.04 and publishes AMD64
host packages; validate the complete Workshop base/SDK combination before use.

The SDK requires a Python environment provider, `zephyr-sdk-ng`, and the correct
target cross-toolchain. SDK host architecture and MCU architecture are different:
the C6 is RISC-V; micro:bit and Nucleo families generally need ARM cross-tools.
Do not copy the documentation's QEMU x86 toolchain example for those boards.

Before enabling micro:bit or Nucleo in the complete pipeline, confirm:

- Both micro:bit revisions' identifiers in the pinned Zephyr release; the exact
  Nucleo model remains unknown.
- Companion SDK/toolchain channels and any required board/module packages.
- A Twister build-only invocation inside Workshop and a portable build package.
- The matching ARM64 Twister and flashing/debug tools on the Pi agent.
- A test-only/device-testing invocation that consumes the transferred build
  without rebuilding or executing AMD64 host tools on the Pi.
- An administrator-owned hardware map selecting only the device allocated by
  Testflinger, with no extra Twister hardware allocation or automatic retry.
- Return paths for Twister JSON/JUnit reports, logs, manifest and board identity.

Test relocating the build package from the AMD64 worker to the ARM64 Pi before
adding it to the matrix. Do not assume an ELF/HEX alone is enough or that CMake
paths in a copied build tree are portable. Exact Twister CLI/packaging options
must be verified against the selected release; they are deliberately not
invented in an executable placeholder here.

The initial ESP32-C6 implementation follows the agreed ESP-IDF/pytest path.
Running Zephyr on that same C6 would be a separate target and adapter, not a
silent change to the first demo's firmware stack.
