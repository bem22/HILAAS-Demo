from pathlib import Path
import runpy

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