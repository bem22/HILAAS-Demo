"""Collect GitHub OAuth credentials directly in an AWS SSH terminal."""

import argparse
import base64
import getpass
import json
import os
import secrets
import socket
import subprocess
import warnings


KUBECTL = ["/snap/bin/microk8s", "kubectl", "--namespace=testflinger"]
SECRET_NAME = "hilaas-browser-oauth"


def install_credentials(client_id, client_secret):
    if not client_id or not client_secret or any(character.isspace() for character in client_id + client_secret):
        raise ValueError("Credentials must be nonempty and contain no whitespace")
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET_NAME,
            "namespace": "testflinger",
            "labels": {"app.kubernetes.io/part-of": "hilaas-demo"},
        },
        "type": "Opaque",
        "stringData": {
            "client-id": client_id,
            "client-secret": client_secret,
            "cookie-secret": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        },
    }
    try:
        result = subprocess.run(
            [*KUBECTL, "create", "-f", "-"],
            input=json.dumps(document), text=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Secret creation could not be confirmed; inspect its name before retrying") from None
    if result.returncode:
        raise RuntimeError("Secret creation failed; existing credentials were not overwritten")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-hostname", required=True)
    args = parser.parse_args()
    if socket.gethostname() != args.expected_hostname or not os.isatty(0):
        raise SystemExit("Run in an interactive SSH terminal on the confirmed AWS host")
    warnings.simplefilter("error", getpass.GetPassWarning)
    try:
        existing = subprocess.run(
            [*KUBECTL, "get", "secret", SECRET_NAME, "--ignore-not-found", "-o", "name"],
            text=True, capture_output=True, timeout=30,
        )
        if existing.returncode or existing.stdout.strip():
            raise RuntimeError("Cannot confirm the secret is absent; nothing changed")
        client_id = getpass.getpass("GitHub OAuth Client ID (hidden): ")
        client_secret = getpass.getpass("GitHub OAuth Client Secret (hidden): ")
        install_credentials(client_id, client_secret)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit("OAuth setup did not complete. No credential values will be displayed.") from None
    print("OAuth credentials installed in testflinger/hilaas-browser-oauth. Public access is still disabled.")


if __name__ == "__main__":
    main()