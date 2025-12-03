#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${PROJECT_ROOT}/.venv"
MODEL_ID="${MODEL_ID:-datalab-to/chandra}"
MODEL_DIR="${PROJECT_ROOT}/.models/datalab-to/chandra"
QWEN_MODEL_ID="${QWEN_MODEL_ID:-Qwen/Qwen2.5-VL-7B-Instruct}"
QWEN_MODEL_DIR="${PROJECT_ROOT}/.models/qwen/Qwen2.5-VL-7B-Instruct"
PYTORCH_INDEX_URL="${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

log() {
  printf '[start.sh] %s\n' "$*"
}

download_hf_model() {
  local label="$1"
  local target_dir="$2"
  local model_id="$3"
  mkdir -p "$target_dir"
  if [ -z "$(ls -A "$target_dir" 2>/dev/null)" ]; then
    log "$label 다운로드 중 ($model_id)"
    if command -v hf_transfer >/dev/null 2>&1; then
      export HF_HUB_ENABLE_HF_TRANSFER=1
    fi
    MODEL_ID="$model_id" MODEL_DIR="$target_dir" python3 <<'PY'
import os
from pathlib import Path
from huggingface_hub import snapshot_download

repo_id = os.environ["MODEL_ID"]
local_dir = Path(os.environ["MODEL_DIR"])
snapshot_download(
    repo_id=repo_id,
    local_dir=str(local_dir),
    local_dir_use_symlinks=False,
    resume_download=True,
)
PY
    log "$label 다운로드 완료"
  else
    log "$label 이미 존재하여 다운로드를 건너뜁니다."
  fi
}

ensure_python() {
  if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    return
  fi

  log "python3.12 바이너리가 없어 자동 설치를 시도합니다."
  if command -v apt-get >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO="sudo"
    else
      SUDO=""
    fi
    log "apt-get update 및 python3.12 설치를 진행합니다."
    ${SUDO} apt-get update
    ${SUDO} apt-get install -y python3.12 python3.12-venv
  else
    log "apt-get 을 찾을 수 없어 자동 설치를 진행할 수 없습니다."
  fi

  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    log "python3.12 설치에 실패했습니다. 수동 설치 후 다시 실행해주세요."
    exit 1
  fi
}

EXISTING_VENV_PY="${VENV_DIR}/bin/python"
if [[ -x "${EXISTING_VENV_PY}" ]]; then
  log "기존 가상환경에서 Python 바이너리를 발견했습니다. ${EXISTING_VENV_PY} 사용."
  PYTHON_BIN="${EXISTING_VENV_PY}"
else
  ensure_python
fi

if [ ! -d "${VENV_DIR}" ]; then
  log "새 가상환경을 ${VENV_DIR} 에 생성합니다."
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  log "기존 가상환경 ${VENV_DIR} 를 재사용합니다."
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

log "pip 업그레이드"
python -m pip install --upgrade pip

log "CUDA 12.8 + CPython 3.12용 PyTorch 패키지 설치"
pip install \
  --index-url "${PYTORCH_INDEX_URL}" \
  torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0

log "기타 파이썬 의존성 설치 (현재 가상환경과 동일한 버전)"
pip install \
  pypdfium2==5.0.0 \
  chandra-ocr==0.1.8 \
  transformers==4.57.1 \
  huggingface_hub==0.36.0
pip install hf_transfer==0.1.9
pip install bitsandbytes==0.48.2
# qdrant-client (dense search용). fastembed는 사용하지 않습니다.
pip install qdrant-client==1.16.0 requests==2.32.5
pip install docx2pdf==0.1.8
if command -v sudo >/dev/null 2>&1; then
  sudo apt-get update 
else
  apt-get update 
fi

# download_hf_model "Chandra 모델" "${MODEL_DIR}" "${MODEL_ID}"
download_hf_model "Qwen2.5-VL-7B-Instruct 모델" "${QWEN_MODEL_DIR}" "${QWEN_MODEL_ID}"

log "환경 구성이 완료되었습니다. 이후 실행은 'source ${VENV_DIR}/bin/activate' 후 하세요."
