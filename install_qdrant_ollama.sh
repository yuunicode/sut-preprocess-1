#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $(id -u) -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  echo "[install] $1"
}

ARCH="$(dpkg --print-architecture)"
if [[ "$ARCH" != "amd64" ]]; then
  echo "Unsupported architecture: $ARCH. This script currently targets amd64." >&2
  exit 1
fi

log "Updating package index"
$SUDO apt-get update

log "Ensuring base packages are available"
$SUDO apt-get install -y curl ca-certificates gnupg lsb-release

if dpkg -l qdrant >/dev/null 2>&1; then
  log "Removing previously installed Qdrant package"
  $SUDO apt-get remove -y qdrant
  $SUDO apt-get autoremove -y
fi

if [[ -x /usr/local/bin/qdrant ]]; then
  log "Deleting legacy Qdrant binary at /usr/local/bin/qdrant"
  $SUDO rm -f /usr/local/bin/qdrant
fi

if ! command -v docker >/dev/null 2>&1; then
  log "Docker CLI not found. Installing docker.io and plugins"
  $SUDO apt-get install -y docker.io docker-compose-plugin
fi

if command -v systemctl >/dev/null 2>&1; then
  log "Ensuring docker service is running"
  $SUDO systemctl enable --now docker || true
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop or the docker service and re-run." >&2
  exit 1
fi

QDRANT_IMAGE="${QDRANT_IMAGE:-qdrant/qdrant:latest}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-qdrant-server}"
QDRANT_VOLUME="${QDRANT_VOLUME:-qdrant_storage}"
QDRANT_HTTP_PORT="${QDRANT_HTTP_PORT:-6333}"
QDRANT_GRPC_PORT="${QDRANT_GRPC_PORT:-6334}"

log "Pulling Qdrant image ${QDRANT_IMAGE}"
docker pull "$QDRANT_IMAGE"

if docker ps -a --format '{{.Names}}' | grep -Fx "$QDRANT_CONTAINER" >/dev/null 2>&1; then
  log "Removing existing container ${QDRANT_CONTAINER}"
  docker rm -f "$QDRANT_CONTAINER"
fi

log "Starting Qdrant container ${QDRANT_CONTAINER}"
docker run -d \
  --name "$QDRANT_CONTAINER" \
  -p "${QDRANT_HTTP_PORT}:6333" \
  -p "${QDRANT_GRPC_PORT}:6334" \
  -v "${QDRANT_VOLUME}:/qdrant/storage" \
  "$QDRANT_IMAGE"

OLLAMA_INSTALLER="/tmp/install_ollama.sh"
log "Fetching Ollama install script"
curl -fsSL https://ollama.com/install.sh -o "$OLLAMA_INSTALLER"

log "Installing Ollama"
$SUDO bash "$OLLAMA_INSTALLER"

if command -v systemctl >/dev/null 2>&1; then
  log "Ensuring Ollama service is running"
  $SUDO systemctl enable --now ollama || true
else
  log "systemd not detected; launch 'ollama serve' manually if necessary."
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama CLI not found in PATH after installation" >&2
  exit 1
fi

log "Pulling snowflake-arctic-embed2 model"
ollama pull snowflake-arctic-embed2

log "Pulling qwen2.5:3b-instruct model"
ollama pull qwen2.5:3b-instruct

log "Pulling qwen2.5:14b-instruct model"
ollama pull qwen2.5:14b-instruct

log "Installation complete"
