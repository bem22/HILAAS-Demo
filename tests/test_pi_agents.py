from contextlib import nullcontext
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml


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
        for phase in ("setup", "provision", "test"):
            assert config[phase + "_command"] == "/bin/false"
        assert config["cleanup_command"] == "/bin/true"
        assert yaml.safe_load((path.parent / "default.yaml").read_text()) == {
            "agent_name": config["agent_id"],
        }
        assert not any("secret" in key or "password" in key for key in config)
    assert queues == set(registration["QUEUES"])


@pytest.mark.parametrize("existing", [None, "client", "queue"])
def test_registration_preflight_never_mutates_server(monkeypatch, capsys, existing):
    registration = runpy.run_path(str(ROOT / "scripts/register-pi-agents.py"))
    database = Mock()
    database.check_client_exists.return_value = existing == "client"
    database.check_queue_restricted.return_value = existing == "queue"
    application = SimpleNamespace(app_context=nullcontext)
    monkeypatch.setitem(sys.modules, "flask", SimpleNamespace(Flask=lambda name: application))
    monkeypatch.setitem(sys.modules, "testflinger", SimpleNamespace(database=database))
    monkeypatch.setitem(sys.modules, "testflinger.api.auth", SimpleNamespace(
        hash_secret=Mock(), validate_client_key_pair=Mock(),
    ))
    plan = {"services": {"testflinger": {"environment": {}}}}
    monkeypatch.setattr(registration["subprocess"], "run", Mock(
        return_value=SimpleNamespace(stdout=json.dumps(plan)),
    ))
    monkeypatch.setattr(sys, "argv", ["register-pi-agents", "--check"])
    if existing:
        with pytest.raises(ValueError, match="refusing replacement"):
            registration["main"]()
    else:
        registration["main"]()
        assert json.loads(capsys.readouterr().out)["ready"] is True
    database.add_restricted_queue.assert_not_called()
    database.create_or_update_client_permissions.assert_not_called()