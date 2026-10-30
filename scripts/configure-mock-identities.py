"""Bootstrap only the demo mock identities inside the server workload."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists():
        raise ValueError("Credential output already exists")

    import yaml
    from flask import Flask
    from testflinger import database
    from testflinger.api.auth import generate_refresh_token

    plan = subprocess.run(["/charm/bin/pebble", "plan"], capture_output=True, text=True, check=True, timeout=30)
    environment = yaml.safe_load(plan.stdout)["services"]["testflinger"]["environment"]
    os.environ.update({key: str(value) for key, value in environment.items() if key.startswith("MONGODB_")})
    application = Flask(__name__)
    database.mongo.init_app(application, uri=database.get_mongo_uri(), serverSelectionTimeoutMS=10000)
    queue = "hilaas-laptop-mock"
    identities = {"agent": ("hilaas-laptop-mock-agent", "agent"),
                  "submitter": ("hilaas-laptop-mock-submitter", "contributor")}
    with application.app_context():
        if any(database.check_client_exists(client_id) for client_id, role in identities.values()):
            raise ValueError("A mock identity already exists; refusing replacement")
        if database.mongo.db.restricted_queues.find_one({"queue_name": queue}):
            raise ValueError("The queue already has a restriction; refusing replacement")
        args.output.mkdir(mode=0o700)
        for name, (client_id, role) in identities.items():
            permissions = {"client_id": client_id, "role": role, "allowed_queues": [queue],
                           "max_priority": {queue: 0}, "max_reservation_time": {queue: 0}}
            database.create_or_update_client_permissions(client_id, permissions)
            token = generate_refresh_token(client_id, expires_in=7 * 24 * 60 * 60)
            with (args.output / (name + ".json")).open("x") as stream:
                json.dump({"client_id": client_id, "refresh_token": token}, stream)
        database.add_restricted_queue(queue, identities["submitter"][0])
    print("Dedicated mock agent and restricted submitter created; tokens saved privately")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Mock credential bootstrap failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None