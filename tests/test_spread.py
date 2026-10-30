from contextlib import redirect_stdout
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as element_tree
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from hilaas import progress, showcase, worker


ROOT = Path(__file__).resolve().parents[1]


class SpreadTests(unittest.TestCase):
    def test_generic_stage_events_preserve_failure_and_target_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target in ("laptop", "esp32c6-devkitc-1"):
                with patch.dict(os.environ, HILAAS_PROGRESS_DIR=directory, HILAAS_RUN_ID="run-1", HILAAS_TARGET=target):
                    with progress.stage("workshop.build"):
                        pass
                    with self.assertRaises(TimeoutError), progress.stage("testflinger.submit"):
                        raise TimeoutError("private detail must not be logged")
                recorded = progress.events(root, "run-1", target)
                self.assertEqual(len(recorded), 4)
                self.assertEqual([event["status"] for event in recorded], ["running", "passed", "running", "failed"])
                self.assertNotIn("private detail", json.dumps(recorded))
                self.assertIn("failed", progress.table(root, "run-1", target))
            self.assertEqual(progress.events(root, "other-run", "laptop"), [])

    def test_progress_runner_preserves_command_exit_status(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HILAAS_RUN_ID="run-1"):
            result = progress.run(["/bin/false"], Path(directory) / "progress", "laptop")
            self.assertEqual(result, 1)
            self.assertIn("unobserved", progress.table(Path(directory) / "progress", "run-1", "laptop"))

    def test_progress_runner_streams_real_subprocess_events(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HILAAS_RUN_ID="run-1"):
            output = io.StringIO()
            with redirect_stdout(output):
                result = progress.run([sys.executable, "-m", "hilaas.progress", "step", "workshop.build", "--", "/bin/true"],
                                      Path(directory) / "progress", "esp32c6-devkitc-1")
            self.assertEqual(result, 0)
            self.assertIn("::group::[esp32c6-devkitc-1] Workshop build and build-time tests: running", output.getvalue())
            self.assertIn("tests: passed", output.getvalue())
            self.assertEqual(output.getvalue().count("::endgroup::"), 2)

    def test_progress_io_failure_does_not_repeat_or_mask_operation(self):
        with patch.dict(os.environ, HILAAS_PROGRESS_DIR="unwritable", HILAAS_RUN_ID="run-1", HILAAS_TARGET="laptop"):
            with patch("pathlib.Path.mkdir", side_effect=PermissionError), patch("sys.stderr", new_callable=io.StringIO):
                operation = Mock(side_effect=TimeoutError)
                with self.assertRaises(TimeoutError), progress.stage("testflinger.submit"):
                    operation()
                operation.assert_called_once()

    def test_laptop_service_checks_tunnel_readiness_before_start(self):
        provisioner = runpy.run_path(str(ROOT / "scripts/provision-laptop-mock.py"))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state = home / ".local/state/HILAAS-Demo/mock"
            (state / "venv/bin").mkdir(parents=True)
            (state / "venv/bin/testflinger-agent").touch()
            (state / "agent-token.json").write_text("{}")
            with (
                patch("pathlib.Path.home", return_value=home),
                patch("os.getuid", return_value=1000),
                patch("os.umask"),
                patch("sys.argv", ["provision-laptop-mock.py", "--server-ip", "10.152.183.194"]),
            ):
                provisioner["main"]()
            unit = (home / ".config/systemd/user/hilaas-mock-agent.service").read_text()
            self.assertIn("ExecStartPre=/usr/bin/curl --fail", unit)
            self.assertIn("--max-time 10 --output /dev/null http://127.0.0.1:15000/v1/agents/queues", unit)
            self.assertIn("Restart=always", unit)
            self.assertIn("RestartSec=10", unit)

    def test_demo_scenario_does_not_count_skips_as_success(self):
        runner = runpy.run_path(str(ROOT / "scripts/demo-scenarios.py"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            suites = element_tree.Element("testsuites")
            suite = element_tree.SubElement(suites, "testsuite")
            case = element_tree.SubElement(suite, "testcase")
            element_tree.SubElement(case, "skipped")
            element_tree.ElementTree(suites).write(output / "usb-missing.xml")
            with patch("subprocess.run", return_value=Mock(returncode=0, stdout="Dependency missing\n")) as process:
                result = runner["run_scenario"]("usb-missing", output)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["skipped"], 1)
            self.assertIn("tests/test_device.py::DeviceTests::test_missing_usb_port_blocks_device_access", process.call_args.args[0])
            self.assertEqual(process.call_args.kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")

    def test_demo_scenario_requires_a_test_report(self):
        runner = runpy.run_path(str(ROOT / "scripts/demo-scenarios.py"))
        with tempfile.TemporaryDirectory() as directory, patch("subprocess.run", return_value=Mock(returncode=0, stdout="")):
            result = runner["run_scenario"]("usb-missing", Path(directory))
        self.assertEqual(result["status"], "failed")

    def test_mock_workflow_is_manual_and_checks_out_exact_source(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mock.yml").read_text())
        self.assertEqual(set(workflow.get("on", workflow.get(True))), {"workflow_dispatch"})
        job = workflow["jobs"]["mock"]
        inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["target"]["options"], ["laptop", "esp32c6-devkitc-1", "microbit", "nucleo", "zephyr-all"])
        self.assertFalse(inputs["preliminary_build"]["default"])
        self.assertEqual(inputs["target"]["default"], "laptop")
        self.assertFalse(inputs["flash_and_test"]["default"])
        self.assertIn("refs/heads/main", job["if"])
        self.assertIn("hilaas-demo", job["runs-on"])
        self.assertNotIn("strategy", job)
        checkout = job["steps"][0]
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        chain = next(step for step in job["steps"] if step.get("id") == "chain")
        self.assertIn("bash scripts/run-mock-spread.sh", chain["run"])
        report_step = next(step for step in job["steps"] if "--report" in step.get("run", ""))
        self.assertEqual(report_step["if"], "always()")
        self.assertEqual(report_step["env"]["PIPELINE_OUTCOME"], "${{ steps.chain.outcome }}")
        self.assertIn("/showcase.md", job["steps"][-1]["with"]["path"])

    def test_showcase_missing_evidence_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            text, passed = showcase.report(Path(directory), "run", "a" * 40, "success")
        self.assertFalse(passed)
        self.assertIn("INCOMPLETE", text)

    def test_showcase_failed_orchestration_does_not_claim_success(self):
        with patch.object(showcase, "validate") as validate:
            text, passed = showcase.report(Path("unused"), "run", "a" * 40, "failure")
        validate.assert_not_called()
        self.assertFalse(passed)
        self.assertIn("Do not resubmit automatically", text)

    def test_showcase_requires_matching_build_and_laptop_identity(self):
        manifest = {"run_id": "run", "commit": "a" * 40, "nonce": "b" * 32,
                    "binary_sha256": "c" * 64}
        receipt = dict(manifest, kind="native", stage="complete", status="pass")
        proof = {"job_id": "test-job", "agent": "hilaas-laptop-mock", "checks": 5, "exit_code": 0}
        with (
            patch.object(showcase, "validate", return_value=(manifest, {})),
            patch.object(showcase, "read_json", side_effect=lambda path: json.loads(path.read_text()) if path.name == "targets.json" else receipt if path.name == "receipt.json" else {}),
            patch.object(showcase, "verify_evidence", return_value=proof) as verify,
        ):
            text, passed = showcase.report(Path("unused"), "run", "a" * 40, "success")
            self.assertTrue(passed)
            self.assertIn("test-job", text)
            self.assertIn("0 / 0 / 0", text)
            verify.assert_called_once()
            for field in ("commit", "run_id", "nonce", "binary_sha256"):
                with self.subTest(field=field), patch.dict(receipt, {field: "mismatch"}):
                    verify.reset_mock()
                    text, passed = showcase.report(Path("unused"), "run", "a" * 40, "success")
                    self.assertFalse(passed)
                    verify.assert_not_called()

    def test_mock_is_separate_from_hardware_backend(self):
        project = yaml.safe_load((ROOT / "spread.yaml").read_text())
        mock = yaml.safe_load((ROOT / "spread/mock/task.yaml").read_text())
        boards = yaml.safe_load((ROOT / "spread/boards/task.yaml").read_text())
        self.assertIn("mock-container", project["backends"])
        self.assertNotIn("HILAAS_PROGRESS_DIR", project["environment"])
        for task in (mock, boards):
            for phase in ("prepare", "execute"):
                self.assertIn("export HILAAS_PROGRESS_DIR=/var/lib/hilaas-progress", task[phase])
        allocation = project["backends"]["mock-container"]["allocate"]
        self.assertLess(allocation.index("python3 -m hilaas.native"), allocation.index("python3 -m hilaas.worker allocate"))
        self.assertIn("--bundle /opt/hilaas-native", mock["execute"])
        self.assertEqual(mock["backends"], ["mock-container"])
        self.assertEqual(boards["backends"], ["local-vm"])
        self.assertEqual(mock["environment"], {"HILAAS_TARGET/laptop": "laptop"})
        self.assertIn("python3 -m pytest tests/test_mock.py", mock["prepare"])
        self.assertNotIn("workshop", mock["execute"])

    def test_mock_worker_has_separate_marker_and_only_submitter_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "HILAAS_MOCK_WORKER_IMAGE": "c" * 64, "SPREAD_PASSWORD": "test-only", "HILAAS_MODE": "mock"}), patch.object(worker, "lxc") as process:
            root = Path(directory)
            (root / "progress/worker").mkdir(parents=True)
            token = root / "submitter.json"
            token.write_text(json.dumps({"client_id": "hilaas-laptop-mock-submitter"}))
            path = root / "lease.json"
            def response(*arguments, **kwargs):
                value = ""
                if arguments[0] == "image":
                    value = json.dumps([{"fingerprint": "c" * 64, "type": "container"}])
                elif "route" in arguments:
                    value = json.dumps([{"prefsrc": "10.20.0.4"}])
                elif "cat" in arguments:
                    value = "mock-v1\n"
                elif arguments[0] == "list":
                    lease = json.loads(path.read_text())
                    value = json.dumps([{"name": lease["name"], "status": "Stopped", "config": {"user.hilaas-owner": lease["owner"]}}])
                return Mock(stdout=value, returncode=0)
            process.side_effect = response
            with patch.dict(os.environ, {"HILAAS_MOCK_TOKEN_FILE": str(token), "HILAAS_PROGRESS_DIR": str(root / "progress"),
                                         "HILAAS_RUN_ID": "run-1", "HILAAS_TARGET": "laptop"}):
                worker.allocate(path)
            initialization = process.call_args_list[1].args
            self.assertIn("security.nesting=false", initialization)
            self.assertIn("security.privileged=false", initialization)
            pushes = [call.args for call in process.call_args_list if call.args[0] == "file"]
            self.assertEqual(len(pushes), 1)
            self.assertIn(str(token), pushes[0])
            mounts = [call.args for call in process.call_args_list if call.args[:3] == ("config", "device", "add")]
            progress_mount = next(mount for mount in mounts if "hilaas-progress" in mount)
            self.assertIn("source=" + str(root / "progress/worker"), progress_mount)
            self.assertIn("path=/var/lib/hilaas-progress", progress_mount)
            worker.discard(path)

    def test_browser_access_requires_explicit_github_user(self):
        documents = list(yaml.safe_load_all((ROOT / "config/browser-gateway.yaml").read_text()))
        deployment = next(document for document in documents if document["kind"] == "Deployment" and document["metadata"]["name"] == "hilaas-browser")
        arguments = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual([argument for argument in arguments if argument.startswith("--github-")], [
            "--github-user=bem22",
            "--github-user=aleccondry",
            "--github-user=locnnil",
            "--github-user=pedroishimaru",
            "--github-user=fderepas",
        ])
        self.assertIn("--cookie-name=__Host-hilaas_users_v2", arguments)
        self.assertIn("--cookie-secure=true", arguments)

    def test_public_ingress_rejects_direct_api_and_old_hostname(self):
        helper = runpy.run_path(str(ROOT / "scripts/publish-browser-ingress.py"))
        ingress = {"metadata": {"namespace": "testflinger"}, "spec": {"ingressClassName": "public", "rules": [{"host": helper["HOST"], "http": {"paths": [{"path": "/", "backend": {"service": {"name": "hilaas-browser", "port": {"number": 4180}}}}]}}]}}
        helper["validate_routes"]([ingress])
        ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] = "hilaas-testflinger-api"
        with self.assertRaisesRegex(ValueError, "bypasses"):
            helper["validate_routes"]([ingress])
        ingress["spec"]["rules"][0]["host"] = "testflinger.local"
        with self.assertRaisesRegex(ValueError, "another hostname"):
            helper["validate_routes"]([ingress])
        with self.assertRaisesRegex(ValueError, "No authenticated"):
            helper["validate_routes"]([])

    def test_browser_credentials_only_go_to_kubectl_stdin(self):
        import base64

        helper = runpy.run_path(str(ROOT / "scripts/configure-browser-oauth.py"))
        with patch("subprocess.run", return_value=Mock(returncode=0)) as process:
            helper["install_credentials"]("test-client-id", "test-client-secret")
        arguments = process.call_args.args[0]
        self.assertNotIn("test-client-secret", " ".join(arguments))
        self.assertIn("create", arguments)
        self.assertNotIn("apply", arguments)
        self.assertTrue(process.call_args.kwargs["capture_output"])
        secret = json.loads(process.call_args.kwargs["input"])
        self.assertEqual(secret["metadata"]["namespace"], "testflinger")
        self.assertEqual(secret["stringData"]["client-secret"], "test-client-secret")
        self.assertEqual(len(base64.urlsafe_b64decode(secret["stringData"]["cookie-secret"])), 32)

    def test_browser_secret_failure_does_not_echo_kubectl_output(self):
        helper = runpy.run_path(str(ROOT / "scripts/configure-browser-oauth.py"))
        with patch("subprocess.run", return_value=Mock(returncode=1, stderr="test-client-secret")):
            with self.assertRaises(RuntimeError) as failure:
                helper["install_credentials"]("test-client-id", "test-client-secret")
        self.assertNotIn("test-client-secret", str(failure.exception))

    def test_matrix_only_enables_known_c6(self):
        task = yaml.safe_load((ROOT / "spread/boards/task.yaml").read_text())
        self.assertEqual(task["environment"], {"HILAAS_TARGET/esp32c6": "esp32c6-devkitc-1",
                               "HILAAS_FIRMWARE_BUNDLE": "/opt/hilaas-firmware"})
        targets = json.loads((ROOT / "targets.json").read_text())
        self.assertEqual([name for name, target in targets.items() if target["enabled"] and target.get("kind") == "mcu"], ["esp32c6-devkitc-1"])
        self.assertEqual(targets["laptop"]["adapter"], "native")

    def test_workflow_has_no_pr_trigger_or_duplicate_matrix(self):
        self.assertFalse((ROOT / ".github/workflows/demo.yml").exists())
        workflow = yaml.safe_load((ROOT / ".github/workflows/mock.yml").read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        self.assertNotIn("strategy", workflow["jobs"]["mock"])
        self.assertFalse(triggers["workflow_dispatch"]["inputs"]["flash_and_test"]["default"])

    def test_no_cached_image_no_vm_mutation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch.object(worker, "lxc") as process:
            with self.assertRaisesRegex(ValueError, "cached local"):
                worker.allocate(Path(directory) / "lease.json")
            process.assert_not_called()

    def test_discard_rejects_unrelated_instance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, "lxc") as process:
            path = Path(directory) / "lease.json"
            path.write_text(json.dumps({"name": "juju-controller", "owner": "fake"}))
            with self.assertRaisesRegex(ValueError, "unrelated"):
                worker.discard(path)
            process.assert_not_called()

    def test_discard_requires_matching_owner(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, "lxc") as process:
            name = "hilaas-spread-" + "a" * 12
            path = Path(directory) / "lease.json"
            path.write_text(json.dumps({"name": name, "owner": "expected"}))
            process.return_value = Mock(stdout=json.dumps([{"name": name, "config": {"user.hilaas-owner": "other"}}]))
            with self.assertRaisesRegex(ValueError, "ownership"):
                worker.discard(path)
            self.assertEqual(process.call_count, 1)

    def test_runner_registration_keeps_token_out_of_arguments(self):
        script = (ROOT / "scripts/provision-cloud-runner.sh").read_text()
        self.assertIn("read -r -s", script)
        self.assertIn("export ACTIONS_RUNNER_INPUT_TOKEN", script)
        self.assertNotIn("--token", script)
        self.assertNotIn("--replace", script)
        self.assertIn("https://github.com/bem22/HILAAS-Demo", script)
        self.assertIn("--name hilaas-aws --labels hilaas-demo", script)

    def test_zephyr_worker_uses_requested_track_and_arm_toolchain(self):
        configuration = yaml.safe_load((ROOT / "config/zephyr-workshop/workshop.yaml").read_text())
        channels = {sdk["name"]: sdk["channel"] for sdk in configuration["sdks"]}
        self.assertEqual(channels, {"uv": "latest/stable", "zephyr": "26.04/stable", "zephyr-sdk-ng": "1.0.1/stable", "zephyr-arm": "1.0.1/stable"})
        self.assertIn({"plug": "zephyr-sdk-ng:arm", "slot": "zephyr-arm:toolchain"}, configuration["connections"])
        targets = json.loads((ROOT / "targets.json").read_text())
        self.assertFalse(targets["microbit"]["enabled"])
        self.assertFalse(targets["nucleo"]["enabled"])
        self.assertEqual(targets["microbit"]["board_revisions"], ["v2"])
        self.assertEqual(targets["microbit"]["board"], "bbc_microbit_v2/nrf52833")
        self.assertEqual(targets["nucleo"]["board"], "nucleo_h753zi/stm32h753xx")
        self.assertTrue(targets["microbit"]["build_enabled"])
        self.assertTrue(targets["nucleo"]["build_enabled"])
        self.assertIn('west build -p always -b "$HILAAS_BOARD"', configuration["actions"]["build"])

    def test_worker_provisioning_requires_matching_virtualization(self):
        with tempfile.TemporaryDirectory() as directory:
            for worker_type, virtualization, expected in (
                ("virtual-machine", "lxc", "container provisioning requires explicit opt-in"),
                ("container", "lxc", "Missing input"),
                ("container", "kvm", "Expected an LXD template container"),
                ("invalid", "lxc", "Unsupported worker type"),
            ):
                with self.subTest(worker_type=worker_type, virtualization=virtualization):
                    environment = dict(os.environ, HILAAS_WORKER_TYPE=worker_type, MOCK_VIRT=virtualization)
                    script = 'id() { echo 0; }; systemd-detect-virt() { echo "$MOCK_VIRT"; }; hostname() { echo hilaas-worker-template-test; }; source "$1" "$2"'
                    result = subprocess.run(["bash", "-c", script, "test", str(ROOT / "scripts/provision-worker.sh"), directory], env=environment, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)

    def test_shell_syntax(self):
        files = [*ROOT.glob("scripts/*.sh"), ROOT / "agent/run-c6.sh", ROOT / ".workshop/build-prereqs/hooks/setup-base"]
        for path in files:
            with self.subTest(path=path):
                subprocess.run(["bash", "-n", str(path)], check=True)

    def test_allocate_and_discard_owned_vm(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HILAAS_WORKER_IMAGE": "b" * 64, "SPREAD_PASSWORD": "test-only", "HILAAS_MODE": "build"}), patch.object(worker, "lxc") as process:
            path = Path(directory) / "lease.json"
            def response(*arguments, **kwargs):
                value = ""
                if arguments[0] == "image":
                    value = json.dumps([{"fingerprint": "b" * 64, "type": "virtual-machine"}])
                elif "route" in arguments:
                    value = json.dumps([{"prefsrc": "10.20.0.2"}])
                elif "cat" in arguments:
                    value = "v1\n"
                elif arguments[0] == "list":
                    lease = json.loads(path.read_text())
                    value = json.dumps([{"name": lease["name"], "status": "Running", "config": {"user.hilaas-owner": lease["owner"]}}])
                return Mock(stdout=value, returncode=0)
            process.side_effect = response
            worker.allocate(path)
            self.assertEqual(json.loads(path.read_text())["address"], "10.20.0.2")
            worker.discard(path)
            self.assertFalse(path.exists())
            operations = [call.args[0] for call in process.call_args_list]
            self.assertEqual(operations, ["image", "init", "start", "exec", "exec", "exec", "list", "stop", "delete"])
            self.assertNotIn("config", operations)

    def test_hardware_config_cannot_share_a_home_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, "lxc") as process:
            root = Path(directory)
            config = root / "client.json"
            config.write_text("{}")
            (root / "unrelated-secret").write_text("not-a-real-secret")
            process.return_value = Mock(stdout=json.dumps([{"fingerprint": "b" * 64, "type": "virtual-machine"}]))
            with patch.dict(os.environ, {"HILAAS_WORKER_IMAGE": "b" * 64, "SPREAD_PASSWORD": "test-only", "HILAAS_MODE": "hardware", "HILAAS_CLIENT_CONFIG": str(config), "HILAAS_AUTH_DIR": str(root)}):
                with self.assertRaisesRegex(ValueError, "dedicated directory"):
                    worker.allocate(root / "lease.json")
            self.assertEqual(process.call_count, 1)

    def test_container_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HILAAS_WORKER_IMAGE": "b" * 64, "HILAAS_WORKER_TYPE": "virtual-machine"}), patch.object(worker, "lxc") as process:
            process.return_value = Mock(stdout=json.dumps([{"fingerprint": "b" * 64, "type": "container"}]))
            with self.assertRaisesRegex(ValueError, "matching the worker type"):
                worker.allocate(Path(directory) / "lease.json")
            self.assertEqual(process.call_count, 1)

    def test_container_allocate_and_discard(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HILAAS_WORKER_IMAGE": "b" * 64, "HILAAS_WORKER_TYPE": "container", "SPREAD_PASSWORD": "test-only", "HILAAS_MODE": "build"}), patch.object(worker, "lxc") as process:
            path = Path(directory) / "lease.json"
            def response(*arguments, **kwargs):
                value = ""
                if arguments[0] == "image":
                    value = json.dumps([{"fingerprint": "b" * 64, "type": "container"}])
                elif "route" in arguments:
                    value = json.dumps([{"prefsrc": "10.20.0.3"}])
                elif "cat" in arguments:
                    value = "v1\n"
                elif arguments[0] == "list":
                    lease = json.loads(path.read_text())
                    value = json.dumps([{"name": lease["name"], "status": "Running", "config": {"user.hilaas-owner": lease["owner"]}}])
                return Mock(stdout=value, returncode=0)
            process.side_effect = response
            worker.allocate(path)
            initialization = process.call_args_list[1].args
            self.assertNotIn("--vm", initialization)
            self.assertIn("security.nesting=true", initialization)
            self.assertIn("limits.memory=4GiB", initialization)
            self.assertNotIn("security.privileged=true", initialization)
            worker.discard(path)
            self.assertFalse(path.exists())
