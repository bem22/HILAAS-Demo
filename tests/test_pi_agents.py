from contextlib import nullcontext
import io
import json
from pathlib import Path
import runpy
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from hilaas import pi_smoke, pi_smoke_client


ROOT = Path(__file__).resolve().parents[1]


def test_pi_agents_have_unique_scoped_fail_closed_configs():
    registration = runpy.run_path(str(ROOT / "scripts/register-pi-agents.py"))
    configurations = list((ROOT / "agent/live").glob("*/*/testflinger-agent.conf"))
    assert len(configurations) == 5
    agent_ids = set()
    queues = set()
    for path in configurations:
        config = yaml.safe_load(path.read_text())
        assert len(list(path.parent.parent.iterdir())) == 1
        assert config["agent_id"] == path.parent.name
        assert config["agent_id"] not in agent_ids
        agent_ids.add(config["agent_id"])
        assert config["server_address"] == "http://127.0.0.1:15000"
        assert len(config["job_queues"]) == 1
        queue = config["job_queues"][0]
        assert queue not in queues
        queues.add(queue)
        assert "execution disabled" in config["advertised_queues"][queue]
        command = ("/usr/bin/env PYTHONPATH=/srv/agent-configs /srv/testflinger-venv/bin/python "
                   "/srv/agent-configs/hilaas/pi_smoke.py --agent " + config["agent_id"])
        assert config["setup_command"] == command + " --validate-only"
        assert config["test_command"] == command
        assert config["provision_command"] == "/bin/true"
        assert config["cleanup_command"] == "/bin/true"
        assert yaml.safe_load((path.parent / "default.yaml").read_text()) == {
            "agent_name": config["agent_id"],
        }
        assert not any("secret" in key or "password" in key for key in config)
    assert queues == set(registration["QUEUES"])
    assert queues == set(pi_smoke.AGENTS.values())
    assert registration["SMOKE_SUBMITTER"] == pi_smoke.SUBMITTER


@pytest.mark.parametrize("existing", [None, "client", "queue"])
@pytest.mark.parametrize("smoke", [False, True])
def test_registration_preflight_never_mutates_server(monkeypatch, capsys, existing, smoke):
    registration = runpy.run_path(str(ROOT / "scripts/register-pi-agents.py"))
    database = Mock()
    database.check_client_exists.return_value = existing == "client"
    database.check_queue_restricted.return_value = smoke or existing == "queue"
    database.get_restricted_queues_owners.return_value = {} if existing == "queue" else {
        queue: ["testflinger-agent"] for queue in registration["QUEUES"]
    }
    application = SimpleNamespace(app_context=nullcontext)
    monkeypatch.setitem(sys.modules, "flask", SimpleNamespace(Flask=lambda name: application))
    monkeypatch.setitem(sys.modules, "testflinger", SimpleNamespace(database=database))
    monkeypatch.setitem(sys.modules, "testflinger.api.auth", SimpleNamespace(
        hash_secret=Mock(), validate_client_key_pair=Mock(), generate_refresh_token=Mock(),
    ))
    plan = {"services": {"testflinger": {"environment": {}}}}
    monkeypatch.setattr(registration["subprocess"], "run", Mock(
        return_value=SimpleNamespace(stdout=json.dumps(plan)),
    ))
    monkeypatch.setattr(sys, "argv", ["register-pi-agents", "--check"] + (["--smoke-submitter"] if smoke else []))
    if existing:
        with pytest.raises(ValueError, match="refusing"):
            registration["main"]()
    else:
        registration["main"]()
        assert json.loads(capsys.readouterr().out)["ready"] is True
    database.add_restricted_queue.assert_not_called()
    database.create_or_update_client_permissions.assert_not_called()


@pytest.fixture
def smoke_job(tmp_path, monkeypatch):
    monkeypatch.setattr(pi_smoke.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(pi_smoke.subprocess, "check_output", lambda *args, **kwargs: "1" * 40)
    job = {
        "job_id": "4e8acde5-a33d-4c94-8a08-0381dbb2b233",
        "job_queue": pi_smoke.AGENTS["hilaas-pi1-c6"],
        "name": "pi-smoke-test", "tags": ["0" * 32, "1" * 40],
        "test_data": {"test_cmds": pi_smoke.TOKEN},
    }
    path = tmp_path / "testflinger.json"
    path.write_text(json.dumps(job))
    return path


def test_smoke_produces_correlated_non_flashing_evidence(smoke_job, tmp_path):
    proof = pi_smoke.execute(smoke_job, tmp_path / "artifacts", "hilaas-pi1-c6", "/dev/null")
    assert proof["job_id"] == json.loads(smoke_job.read_text())["job_id"]
    assert proof["commit"] == "1" * 40 and proof["nonce"] == "0" * 32
    assert proof["device_opened"] is proof["flashed"] is proof["firmware_executed"] is False
    assert json.loads((tmp_path / "artifacts/pi-smoke-result.json").read_text()) == proof


@pytest.mark.parametrize("change", [
    {"test_data": {"test_cmds": "echo unexpected"}},
    {"provision_data": {"image": "unexpected"}},
    {"job_queue": "another-queue"},
    {"tags": ["invalid", "1" * 40]},
    {"tags": ["0" * 32, "2" * 40]},
])
def test_smoke_rejects_invalid_jobs_before_device_access(smoke_job, tmp_path, change):
    job = json.loads(smoke_job.read_text())
    job.update(change)
    smoke_job.write_text(json.dumps(job))
    with pytest.raises(ValueError):
        pi_smoke.execute(smoke_job, tmp_path / "artifacts", "hilaas-pi1-c6", "/does-not-exist")
    assert not (tmp_path / "artifacts").exists()


def test_smoke_rejects_regular_file_as_device(smoke_job, tmp_path):
    with pytest.raises(ValueError, match="not a character device"):
        pi_smoke.execute(smoke_job, tmp_path / "artifacts", "hilaas-pi1-c6", smoke_job)


def test_smoke_client_verifies_exact_artifact(smoke_job, tmp_path):
    proof = pi_smoke.execute(smoke_job, tmp_path / "artifacts", "hilaas-pi1-c6", "/dev/null")
    archive_path = tmp_path / "result.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        content = json.dumps(proof).encode()
        member = tarfile.TarInfo("artifacts/pi-smoke-result.json")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    results = {"job_state": "complete", "setup_status": 0, "test_status": 0, "cleanup_status": 0}
    assert pi_smoke_client.verify_evidence(archive_path, results, proof) == proof
    with pytest.raises(ValueError, match="mismatched"):
        pi_smoke_client.verify_evidence(archive_path, results, dict(proof, nonce="f" * 32))
    with pytest.raises(ValueError, match="test failed"):
        pi_smoke_client.verify_evidence(archive_path, dict(results, test_status=1), proof)


def test_smoke_client_records_intent_and_never_resubmits(tmp_path):
    agent_id = "hilaas-pi1-c6"
    queue = pi_smoke.AGENTS[agent_id]
    api = Mock()
    api.get_json.side_effect = [
        {"owners": ["testflinger-agent", pi_smoke.SUBMITTER]},
        {"state": "waiting", "queues": [queue], "job": {}},
        [],
    ]
    output = tmp_path / "attempt"

    def submit(method, path, **kwargs):
        receipt = json.loads((output / "receipt.json").read_text())
        assert receipt["job_id"] == kwargs["json"]["job_id"]
        assert receipt["stage"] == "submission_intent"
        return SimpleNamespace(json=lambda: {"job_id": "unexpected"})

    api.request.side_effect = submit
    with pytest.raises(ValueError, match="changed the smoke job ID"):
        pi_smoke_client.execute(api, agent_id, "unit-smoke", "1" * 40, output)
    with pytest.raises(ValueError, match="already attempted"):
        pi_smoke_client.execute(api, agent_id, "unit-smoke", "1" * 40, output)
    assert api.request.call_count == 1
    assert json.loads((output / "receipt.json").read_text())["status"] == "fail"