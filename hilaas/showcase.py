"""Publish the native pipeline topology and independently verified run evidence."""

import argparse
import os
from pathlib import Path

from hilaas.bundle import read_json, require
from hilaas.mock_client import verify_evidence
from hilaas.native import validate


ARCHITECTURE = """## Architecture: Source to Laptop

```mermaid
flowchart LR
    GitHub[GitHub: exact source commit] --> Runner[AWS self-hosted runner]
    Runner --> Spread[Spread: orchestration]
    Spread --> Workshop[Workshop: isolated native build and CTest]
    Workshop --> Worker[Spread worker: binary and manifest]
    Worker --> TF[Testflinger: restricted queue and attachments]
    TF --> Laptop[Laptop agent: sandboxed binary execution]
    Laptop --> Evidence[Testflinger: phase results and execution proof]
    Evidence --> Verify[Spread client: evidence verification]
    Verify --> Artifacts[GitHub: independent verification and artifacts]
```

| Boundary | Responsibility |
| --- | --- |
| GitHub -> AWS runner | Check out the exact dispatched commit; manual dispatch only |
| Runner -> Spread -> Workshop | Compile the source and run CTest in a dedicated Workshop environment |
| Workshop -> Spread worker | Validate and transfer the ELF binary and identity manifest into an isolated LXD worker |
| Worker -> Testflinger | Check the dedicated queue, submit once, and upload the binary attachments |
| Testflinger -> laptop | Agent polls through the SSH tunnel and executes the binary in a restricted sandbox |
| Laptop -> Testflinger -> Spread -> GitHub | Return phase results, correlate execution proof, verify the binary digest, and publish evidence |

This run executes a real native binary on the laptop. It does not flash an MCU.
The 13 offline failure scenarios are separate tests, not live fault injections.
USB MCU execution and the proposed device registry are not demonstrated by this run.
"""


def report(root, run_id, commit, pipeline_outcome):
    lines = ["## Downstream Execution Evidence", "",
             f"Spread orchestration outcome: **{pipeline_outcome}**", ""]
    try:
        require(pipeline_outcome == "success", "Spread did not succeed")
        manifest, _ = validate(root / "build/bundle")
        receipt = read_json(root / "laptop/receipt.json")
        results = read_json(root / "laptop/phase-results.json")
        require(manifest["run_id"] == receipt["run_id"] == run_id, "Run identity mismatch")
        require(manifest["commit"] == receipt["commit"] == commit, "Source identity mismatch")
        require(manifest["nonce"] == receipt["nonce"], "Nonce mismatch")
        require(manifest["binary_sha256"] == receipt["binary_sha256"], "Binary identity mismatch")
        require(receipt["kind"] == "native" and receipt["stage"] == "complete"
                and receipt["status"] == "pass", "Missing native completion receipt")
        proof = verify_evidence(root / "laptop/artifacts.tar.gz", results, receipt)
    except Exception as error:
        lines += ["**INCOMPLETE: end-to-end success has not been verified.**", "",
                  f"Verification stopped with `{type(error).__name__}`. Inspect the failed step and available artifacts.",
                  "Do not resubmit automatically: an existing Testflinger job may still be active."]
        return "\n".join(lines) + "\n", False
    lines += ["**PASS: the Workshop binary matches the laptop execution evidence.**", "",
              "| Evidence | Verified value |", "| --- | --- |",
              f"| Source commit | `{manifest['commit']}` |",
              f"| Run identity | `{manifest['run_id']}` |",
              f"| Binary SHA256 | `{manifest['binary_sha256']}` |",
              f"| Per-build nonce | `{manifest['nonce']}` |",
              f"| Testflinger job | `{proof['job_id']}` |",
              f"| Laptop agent / queue | `{proof['agent']}` |",
              "| Testflinger setup / test / cleanup | `0 / 0 / 0` |",
              f"| Laptop self-tests / exit code | `{proof['checks']} passed / {proof['exit_code']}` |",
              "| MCU access / flash | `false / false` |", "",
              "### Evidence to Inspect", "",
              "- `build/workshop-build.log`: compiler and CTest output.",
              "- `build/bundle/`: compiled ELF binary and source/run/digest manifest.",
              "- `laptop/receipt.json`: Testflinger job identity and completion receipt.",
              "- `laptop/phase-results.json`: downstream phase statuses.",
              "- `laptop/artifacts.tar.gz`: laptop proof and `artifacts/native-output.log`.",
              "- `showcase.md`: this run's architecture and verification report.", "",
              "Artifact publication is a separate step; check its outcome before sharing the bundle."]
    return "\n".join(lines) + "\n", True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--outcome", choices=("success", "failure", "cancelled", "skipped"))
    args = parser.parse_args()
    text, passed = ARCHITECTURE, True
    if args.report is not None:
        require(args.outcome is not None, "Missing orchestration outcome")
        text, passed = report(args.report, os.environ["HILAAS_RUN_ID"],
                              os.environ["HILAAS_COMMIT"], args.outcome)
        args.report.mkdir(parents=True, exist_ok=True)
        (args.report / "showcase.md").write_text(ARCHITECTURE + "\n" + text)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(text + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())