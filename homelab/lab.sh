#!/usr/bin/env sh
set -eu

command_name="${1:-up}"
[ "$#" -eq 0 ] || shift
lab_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
compose_file="$lab_root/compose.yaml"
real_model_file="$lab_root/compose.real-model.yaml"
env_file="$lab_root/.env"
example_env="$lab_root/.env.example"
artifacts="$lab_root/artifacts"
sample_file="$lab_root/samples/default.json"
accept_sample_policy=false
force=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sample)
      [ "$#" -ge 2 ] || { echo "--sample requires a JSON file path" >&2; exit 2; }
      sample_file=$2
      shift 2
      ;;
    --accept-sample-policy)
      accept_sample_policy=true
      shift
      ;;
    --force)
      force=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required for fail-closed sample validation." >&2
  exit 1
}
if ! sample_file=$(python3 "$lab_root/workload/scenario.py" --resolve-for-launch "$sample_file" "$lab_root"); then
  exit 1
fi
[ -f "$sample_file" ] || { echo "Sample file could not be resolved." >&2; exit 1; }
sample_bytes=$(wc -c < "$sample_file" | tr -d ' ')
[ "$sample_bytes" -gt 0 ] && [ "$sample_bytes" -le 65536 ] || {
  echo "Sample must be a non-empty JSON file no larger than 64 KiB." >&2
  exit 1
}
LAB_SAMPLE_FILE="$sample_file"
export LAB_SAMPLE_FILE
if [ "$accept_sample_policy" = true ]; then
  LAB_SAMPLE_POLICY_ACK="I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
  export LAB_SAMPLE_POLICY_ACK
else
  unset LAB_SAMPLE_POLICY_ACK
fi

check_sample() {
  python3 "$lab_root/workload/scenario.py" "$LAB_SAMPLE_FILE"
}

if [ "$command_name" = "proof" ] || [ "$command_name" = "report" ]; then
  latest_proof="$artifacts/latest-receipt.json"
  [ -f "$latest_proof" ] || { echo "No exported proof exists yet. Run './lab.sh up' first." >&2; exit 1; }
  cat "$latest_proof"
  exit 0
fi

if [ "$command_name" = "check-sample" ]; then
  check_sample
  exit 0
fi

LAB_GIT_COMMIT="unrecorded"
if command -v git >/dev/null 2>&1 && git -C "$lab_root/.." rev-parse --verify HEAD >/dev/null 2>&1; then
  LAB_GIT_COMMIT=$(git -C "$lab_root/.." rev-parse --verify HEAD)
  if [ -n "$(git -C "$lab_root/.." status --porcelain --untracked-files=normal)" ]; then
    LAB_GIT_COMMIT="${LAB_GIT_COMMIT}-dirty"
  fi
fi
export LAB_GIT_COMMIT
export LAB_UID="${LAB_UID:-$(id -u)}"

if [ ! -f "$env_file" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required once to generate local lab secrets." >&2
    exit 1
  fi
  python3 - "$example_env" "$env_file" <<'PY'
import base64
import os
import pathlib
import re
import sys

source, target = map(pathlib.Path, sys.argv[1:])
content = source.read_text(encoding="utf-8")
content = re.sub(r"(?m)^LIANS_ADMIN_SECRET=.*$", f"LIANS_ADMIN_SECRET={os.urandom(32).hex()}", content)
content = re.sub(
    r"(?m)^LIANS_MASTER_ENCRYPTION_KEY=.*$",
    f"LIANS_MASTER_ENCRYPTION_KEY={base64.b64encode(os.urandom(32)).decode()}",
    content,
)
target.write_text(content, encoding="utf-8")
PY
  echo "Created homelab/.env with random local secrets."
fi
chmod 600 "$env_file"

mkdir -p "$artifacts"
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but its Linux engine is not running. Start Docker Desktop or Docker Engine, then retry." >&2
  exit 1
fi

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

compose_real() {
  docker compose --env-file "$env_file" -f "$compose_file" --project-name lians-homelab-real -f "$real_model_file" "$@"
}

verify_lightweight() {
  compose --profile tools build verify
  compose --profile tools run --rm verify
}

verify_real() {
  compose_real --profile tools build verify
  compose_real --profile tools run --rm verify
}

show_endpoints() {
  printf '\n%s\n' \
    "Lians API   http://localhost:8001/docs" \
    "Grafana     http://localhost:3000/d/lians-homelab-proof" \
    "Prometheus  http://localhost:9090" \
    "Alloy       http://localhost:12345" \
    "" \
    "Grafana credentials are in homelab/.env."
}

case "$command_name" in
  up)
    check_sample
    compose_real down --remove-orphans
    compose up --build -d
    verify_lightweight
    show_endpoints
    ;;
  up-real)
    check_sample
    compose down --remove-orphans
    compose_real up --build -d
    verify_real
    show_endpoints
    ;;
  verify)
    verify_lightweight
    ;;
  verify-real)
    verify_real
    ;;
  status)
    echo "Lightweight project"
    compose ps
    echo "Real-model project"
    compose_real ps
    show_endpoints
    ;;
  logs)
    compose logs --follow --tail 200
    ;;
  logs-real)
    compose_real logs --follow --tail 200
    ;;
  down)
    compose down --remove-orphans
    compose_real down --remove-orphans
    ;;
  dispose|reset)
    if [ "$force" != true ]; then
      printf "Dispose deletes all homelab databases, telemetry, and proof state. Type DISPOSE to continue: "
      read -r answer
      [ "$answer" = "DISPOSE" ] || { echo "Dispose cancelled." >&2; exit 1; }
    fi
    compose down --volumes --remove-orphans
    compose_real down --volumes --remove-orphans
    echo "Removed homelab containers and named volumes. .env and sanitized exported reports were preserved."
    ;;
  *)
    echo "Usage: ./lab.sh {up|up-real|check-sample|verify|verify-real|status|logs|logs-real|proof|report|down|dispose|reset} [--sample FILE] [--accept-sample-policy] [--force]" >&2
    exit 2
    ;;
esac
