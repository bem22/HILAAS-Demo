"""Create the Pi agent login and restricted queues inside the server workload."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


CLIENT_ID = "testflinger-agent"
SMOKE_SUBMITTER = "hilaas-pi-smoke-submitter"
QUEUES = (
    "hilaas-esp32c6-pi1",
    "hilaas-microbit-pi1",
    "hilaas-nucleo-pi1",
    "hilaas-esp32-pi2",
    "hilaas-esp32c3-pi2",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--smoke-submitter", action="store_true")
    parser.add_argument("--c6-demo", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)

    import yaml
    from flask import Flask
    from testflinger import database
    from testflinger.api.auth import generate_refresh_token, hash_secret, validate_client_key_pair

    plan = subprocess.run(
        ["/charm/bin/pebble", "plan"], capture_output=True, text=True,
        check=True, timeout=30,
    )
    environment = yaml.safe_load(plan.stdout)["services"]["testflinger"]["environment"]
    os.environ.update({
        key: str(value) for key, value in environment.items()
        if key.startswith("MONGODB_")
    })
    application = Flask(__name__)
    database.mongo.init_app(
        application, uri=database.get_mongo_uri(), serverSelectionTimeoutMS=10000,
    )
    with application.app_context():
        if args.c6_demo:
            client_id = "hilaas-c6-demo-submitter"
            queue = "hilaas-esp32c6-pi1"
            owners = database.get_restricted_queues_owners()
            if database.check_client_exists(client_id) or set(owners.get(queue, [])) != {CLIENT_ID, SMOKE_SUBMITTER}:
                raise ValueError("Unexpected demo account or queue ownership; refusing replacement")
            if args.check:
                print(json.dumps({"client_id": client_id, "queue": queue, "ready": True}))
                return
            if args.output is None or args.output.exists():
                raise ValueError("A fresh private output path is required")
            args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with args.output.open("x") as stream:
                database.create_or_update_client_permissions(client_id, {
                    "client_id": client_id, "role": "contributor", "allowed_queues": [queue],
                    "max_priority": {queue: 0}, "max_reservation_time": {queue: 0},
                })
                token = generate_refresh_token(client_id, expires_in=86400)
                json.dump({"client_id": client_id, "refresh_token": token}, stream)
            print("C6-only demo contributor created; 24-hour token saved privately")
            return
        if args.smoke_submitter:
            if database.check_client_exists(SMOKE_SUBMITTER):
                raise ValueError("Smoke submitter already exists; refusing replacement")
            owners = database.get_restricted_queues_owners()
            if not all(database.check_queue_restricted(queue)
                       and set(owners.get(queue, [])) == {CLIENT_ID} for queue in QUEUES):
                raise ValueError("Unexpected queue ownership; refusing permission changes")
            if args.check:
                print(json.dumps({"client_id": SMOKE_SUBMITTER, "queues": QUEUES, "ready": True}))
                return
            if args.output is None or args.output.exists():
                raise ValueError("A fresh private credential output path is required")
            args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with args.output.open("x") as stream:
                database.create_or_update_client_permissions(SMOKE_SUBMITTER, {
                    "client_id": SMOKE_SUBMITTER, "role": "contributor",
                    "allowed_queues": list(QUEUES),
                    "max_priority": {queue: 0 for queue in QUEUES},
                    "max_reservation_time": {queue: 0 for queue in QUEUES},
                })
                token = generate_refresh_token(SMOKE_SUBMITTER, expires_in=3600)
                json.dump({"client_id": SMOKE_SUBMITTER, "refresh_token": token}, stream)
            print("Restricted smoke submitter created; one-hour token saved privately")
            return
        if database.check_client_exists(CLIENT_ID):
            raise ValueError("Agent login already exists; refusing replacement")
        if any(database.check_queue_restricted(queue) for queue in QUEUES):
            raise ValueError("A queue restriction already exists; refusing replacement")
        if args.check:
            print(json.dumps({"client_id": CLIENT_ID, "queues": QUEUES, "ready": True}))
            return
        credentials = json.load(sys.stdin)
        secret_key = credentials["secret-key"]
        if credentials["client-id"] != CLIENT_ID or not isinstance(secret_key, str) or not secret_key:
            raise ValueError("Invalid credentials input")
        permissions = {
            "client_id": CLIENT_ID,
            "client_secret_hash": hash_secret(secret_key),
            "role": "agent",
            "allowed_queues": list(QUEUES),
            "max_priority": {queue: 0 for queue in QUEUES},
            "max_reservation_time": {queue: 0 for queue in QUEUES},
        }
        for queue in QUEUES:
            database.add_restricted_queue(queue, CLIENT_ID)
        database.create_or_update_client_permissions(CLIENT_ID, permissions)
        if not validate_client_key_pair(CLIENT_ID, secret_key):
            raise RuntimeError("Registration credential verification failed")
        if not all(database.check_queue_restricted(queue) for queue in QUEUES):
            raise RuntimeError("Registration queue verification failed")
    print("Agent login and five restricted queues created; agent services must still connect")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Pi agent registration failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None