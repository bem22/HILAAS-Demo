"""Create isolated user-service configuration for the laptop mock agent."""

import argparse
import ipaddress
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-ip", required=True)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.server_ip)
    if not address.is_private or os.getuid() == 0:
        raise ValueError("Run as the laptop user with the private service IPv4 address")
    home = Path.home()
    root = home / ".local/state/HILAAS-Demo/mock"
    units = home / ".config/systemd/user"
    paths = [root / "agent.json", units / "hilaas-mock-tunnel.service", units / "hilaas-mock-agent.service"]
    if any(path.exists() for path in paths):
        raise ValueError("Mock configuration already exists; refusing replacement")
    if not (root / "venv/bin/testflinger-agent").is_file() or not (root / "agent-token.json").is_file():
        raise ValueError("Install the agent and its private token first")
    os.umask(0o077)
    root.chmod(0o700)
    units.mkdir(parents=True, exist_ok=True)
    for name in ("run", "logs", "results"):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    config = {"agent_id": "hilaas-laptop-mock", "server_address": "http://127.0.0.1:15000",
              "job_queues": ["hilaas-laptop-mock"],
              "advertised_queues": {"hilaas-laptop-mock": "Laptop mock only: no firmware or hardware"},
              "execution_basedir": str(root / "run"), "logging_basedir": str(root / "logs"),
              "results_basedir": str(root / "results"), "polling_interval": 5,
              "logging_level": "INFO", "provision_type": "noprovision", "global_timeout": 120,
              "setup_command": "/bin/true", "provision_command": "/bin/false",
              "firmware_update_command": "/bin/false", "allocate_command": "/bin/false",
              "reserve_command": "/bin/false", "cleanup_command": "/bin/true",
              "test_command": str(root / "venv/bin/python") + " -I -m hilaas.mock_agent"}
    with paths[0].open("x") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
    tunnel = f"""[Unit]
Description=Private SSH tunnel for HILAAS laptop mock
After=network-online.target
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/bin/ssh -n -N -T -i %h/.ssh/hilaas.pem -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=15 -L 127.0.0.1:15000:{address}:5000 ubuntu@ec2-16-16-193-47.eu-north-1.compute.amazonaws.com
Restart=always
RestartSec=10
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
"""
    agent = """[Unit]
Description=HILAAS laptop mock Testflinger agent (no hardware)
Requires=hilaas-mock-tunnel.service
After=hilaas-mock-tunnel.service
StartLimitIntervalSec=0

[Service]
ExecStartPre=/usr/bin/curl --fail --silent --show-error --noproxy * --connect-timeout 3 --max-time 10 --output /dev/null http://127.0.0.1:15000/v1/agents/queues
ExecStart=%h/.local/state/HILAAS-Demo/mock/venv/bin/testflinger-agent -c %h/.local/state/HILAAS-Demo/mock/agent.json --token-file %h/.local/state/HILAAS-Demo/mock/agent-token.json
WorkingDirectory=%h/.local/state/HILAAS-Demo/mock
Environment=NO_PROXY=127.0.0.1,localhost
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
TimeoutStopSec=60
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.local/state/HILAAS-Demo/mock
MemoryMax=512M
CPUQuota=100%
UMask=0077

[Install]
WantedBy=default.target
"""
    for path, content in zip(paths[1:], (tunnel, agent)):
        with path.open("x") as stream:
            stream.write(content)
    print("Mock configuration created; review and enable the two hilaas-mock user services")


if __name__ == "__main__":
    main()