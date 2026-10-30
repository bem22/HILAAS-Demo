#!/bin/bash
set -eo pipefail
cd /project
[[ ${HILAAS_COMMIT:-} =~ ^[0-9a-f]{40}$ ]] || exit 1
mkdir -p build
exec 9>build/.lock
flock -n 9 || { printf 'Another build is running\n' >&2; exit 1; }
rm -f build/bundle/manifest.json
source /etc/profile.d/freertos-esp-idf.sh
source "${IDF_PATH:?}/export.sh"
[[ "$(idf.py --version)" == 'ESP-IDF v5.5.3' ]]
export HILAAS_IDENTITY_DIR=/project/build/identity
python -m hilaas.cli identity
idf.py -C firmware/esp32c6 -B /project/build/idf \
  -D IDF_TARGET=esp32c6 -D SDKCONFIG=/project/build/idf/sdkconfig build
python -m hilaas.cli package
