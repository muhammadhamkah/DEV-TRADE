#!/bin/sh
set -e
# Lock down egress as root, then drop to the unprivileged agent user.
if [ "$(id -u)" = "0" ]; then
  ./scripts/lockdown.sh || echo "lockdown skipped (no NET_ADMIN?); egress is OPEN"
  exec su -s /bin/sh agent -c "cd /app && exec python -m survival $*"
fi
exec python -m survival "$@"
