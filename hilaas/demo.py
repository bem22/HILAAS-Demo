"""One firmware build, explicit test jobs, and a combined demo report."""

import time
import xml.etree.ElementTree as element_tree
from pathlib import Path

from hilaas.bundle import require
from hilaas.client import execute, save


SUITES = ("crc32", "uint32-wrap")


def run_suites(bundle, config, api, run_id, commit, ledger, output, selection="both",
               clock=time.monotonic, wait=time.sleep):
    require(selection in ("both", *SUITES), "Unknown demo selection")
    require(len(run_id) <= 80, "Demo run ID is too long")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    require(not (output / "summary.json").exists(), "Demo already attempted; inspect its receipts")
    selected = list(SUITES) if selection == "both" else [selection]
    summary = {"run_id": run_id, "commit": commit, "selected": selected, "status": "running",
               "tests": [{"suite": suite, "status": "not-run"} for suite in selected]}
    save(output / "summary.json", summary)
    try:
        for result in summary["tests"]:
            suite = result["suite"]
            result["status"] = "running"
            save(output / "summary.json", summary)
            receipt = execute(bundle, dict(config, suite=suite), api, run_id + "-" + suite,
                              commit, ledger, output / suite)
            result.update(status="pass", job_id=receipt["job_id"], proof=receipt["proof"])
            save(output / "summary.json", summary)
            deadline = clock() + 60
            while True:
                agent = api.get_json("/v1/agents/data/" + config["agent"])
                if agent and agent.get("state") == "waiting" and not agent.get("job"):
                    break
                require(clock() < deadline, "Agent has not returned to waiting; no further job submitted")
                wait(2)
        summary["status"] = "pass"
        return summary
    except Exception as error:
        summary.update(status="fail", error=type(error).__name__)
        for result in summary["tests"]:
            if result["status"] == "running":
                result.update(status="fail", error=type(error).__name__)
        raise
    finally:
        save(output / "summary.json", summary)
        report = element_tree.Element("testsuite", name="esp32c6-firmware", tests=str(len(selected)),
                                      failures=str(sum(result["status"] == "fail" for result in summary["tests"])),
                                      skipped=str(sum(result["status"] == "not-run" for result in summary["tests"])))
        lines = ["# Firmware Demo", "", f"Run: `{run_id}`. Source: `{commit}`.", "",
                 "| Test | Result | Testflinger Job |", "| --- | --- | --- |"]
        for result in summary["tests"]:
            case = element_tree.SubElement(report, "testcase", classname="esp32c6", name=result["suite"])
            if result["status"] == "fail":
                element_tree.SubElement(case, "failure", message="Inspect the saved job receipt and logs")
            elif result["status"] == "not-run":
                element_tree.SubElement(case, "skipped", message="Stopped after earlier failure; no retry")
            lines.append(f"| {result['suite']} | {result['status']} | {result.get('job_id', 'See receipt')} |")
        element_tree.ElementTree(report).write(output / "junit.xml", encoding="utf-8", xml_declaration=True)
        (output / "report.md").write_text("\n".join(lines) + "\n")