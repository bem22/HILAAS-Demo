#!/bin/bash
set -euo pipefail
export HILAAS_DEVICE_CONFIG=/etc/hilaas/esp32c6.json
export HILAAS_JOB="$PWD/testflinger.json"
export HILAAS_BUNDLE="$PWD/attachments/test"
export HILAAS_EVIDENCE="$PWD/artifacts"
mkdir -p "$HILAAS_EVIDENCE"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec /opt/hilaas-demo/.venv/bin/python -I -m pytest -q --confcutdir=/opt/hilaas-demo/agent \
  /opt/hilaas-demo/agent/test_c6.py \
  --junitxml="$HILAAS_EVIDENCE/junit.xml" -o addopts=
