"""Create the Pi agent login and restricted queues inside the server workload."""

import argparse
import json
import os
import subprocess
import sys


CLIENT_ID = "testflinger-agent"
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
    args = parser.parse_args()

    import yaml
    from flask import Flask
    from testflinger import database
    from testflinger.api.auth import hash_secret, validate_client_key_pair

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