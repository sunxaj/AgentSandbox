#!/usr/bin/env bash
set -euo pipefail

# Start the Qdrant Docker Compose service on the server selected during setup.
# Usage: ./start_docker.sh <ssh-user>@192.168.0.222
# Or set QDRANT_SSH_TARGET in the environment.
QDRANT_SSH_TARGET="${QDRANT_SSH_TARGET:-${1:-}}"

if [[ -z "$QDRANT_SSH_TARGET" ]]; then
  echo "Usage: QDRANT_SSH_TARGET=<user>@192.168.0.222 $0"
  echo "   or: $0 <user>@192.168.0.222"
  exit 2
fi

ssh "$QDRANT_SSH_TARGET" 'set -eu
  # Run Compose from the server-side service directory so its volume paths resolve there.
  cd "$HOME/services/qdrant"
  # Fail clearly instead of reporting a misleading successful container start without Docker.
  if ! docker info >/dev/null 2>&1; then
    echo "Docker is not available on the server. Start or install the Docker Engine first." >&2
    exit 1
  fi
  # `up -d` is idempotent and leaves the Qdrant service running after this SSH call exits.
  docker compose up -d
  docker compose ps
'
