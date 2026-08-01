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
dataset_file="$lab_root/datasets/default.ndjson"
dataset_was_set=false
scale_profile="laptop"
records="10000"
accept_sample_policy=false
force=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sample)
      [ "$#" -ge 2 ] || { echo "--sample requires a JSON file path" >&2; exit 2; }
      sample_file=$2
      shift 2
      ;;
    --dataset)
      [ "$#" -ge 2 ] || { echo "--dataset requires an NDJSON file path" >&2; exit 2; }
      dataset_file=$2
      dataset_was_set=true
      shift 2
      ;;
    --scale-profile)
      [ "$#" -ge 2 ] || { echo "--scale-profile requires laptop, workstation, or dedicated" >&2; exit 2; }
      scale_profile=$2
      shift 2
      ;;
    --records)
      [ "$#" -ge 2 ] || { echo "--records requires a positive integer" >&2; exit 2; }
      records=$2
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

case "$scale_profile" in
  laptop|workstation|dedicated) : ;;
  *) echo "--scale-profile requires laptop, workstation, or dedicated" >&2; exit 2 ;;
esac

case "$records" in
  ''|*[!0-9]*) echo "--records requires a positive integer" >&2; exit 2 ;;
esac

if [ "$accept_sample_policy" = true ]; then
  LAB_SAMPLE_POLICY_ACK="I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
  LAB_DATASET_POLICY_ACK="I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
  export LAB_SAMPLE_POLICY_ACK
  export LAB_DATASET_POLICY_ACK
else
  unset LAB_SAMPLE_POLICY_ACK
  unset LAB_DATASET_POLICY_ACK
fi

require_python() {
  command -v python3 >/dev/null 2>&1 || {
    echo "python3 is required for fail-closed local validation." >&2
    exit 1
  }
}

load_scale_profile() {
  profile_file="$lab_root/profiles/$scale_profile.env"
  [ -f "$profile_file" ] || { echo "Scale profile could not be found: $scale_profile" >&2; exit 1; }

  unset LAB_SCALE_PROFILE LAB_BULK_CONCURRENCY LAB_DATASET_MAX_RECORDS
  unset LAB_DATASET_MAX_BYTES LAB_DATASET_MAX_LINE_BYTES
  unset LAB_BULK_REQUEST_TIMEOUT_SECONDS LAB_RATE_LIMIT_PER_MINUTE
  carriage_return=$(printf '\r')
  while IFS= read -r profile_line || [ -n "$profile_line" ]; do
    profile_line=${profile_line%"$carriage_return"}
    case "$profile_line" in
      ''|'#'*) continue ;;
      *=*)
        profile_key=${profile_line%%=*}
        profile_value=${profile_line#*=}
        case "$profile_key" in
          LAB_SCALE_PROFILE|LAB_BULK_CONCURRENCY|LAB_DATASET_MAX_RECORDS|LAB_DATASET_MAX_BYTES|LAB_DATASET_MAX_LINE_BYTES|LAB_BULK_REQUEST_TIMEOUT_SECONDS|LAB_RATE_LIMIT_PER_MINUTE)
            [ -n "$profile_value" ] || { echo "Scale profile contains an empty value: $profile_key" >&2; exit 1; }
            export "$profile_key=$profile_value"
            ;;
          *)
            echo "Scale profile contains a non-allowlisted setting: $profile_key" >&2
            exit 1
            ;;
        esac
        ;;
      *)
        echo "Scale profile contains an invalid line." >&2
        exit 1
        ;;
    esac
  done < "$profile_file"

  [ "${LAB_SCALE_PROFILE:-}" = "$scale_profile" ] &&
    [ -n "${LAB_BULK_CONCURRENCY:-}" ] &&
    [ -n "${LAB_DATASET_MAX_RECORDS:-}" ] &&
    [ -n "${LAB_DATASET_MAX_BYTES:-}" ] &&
    [ -n "${LAB_DATASET_MAX_LINE_BYTES:-}" ] &&
    [ -n "${LAB_BULK_REQUEST_TIMEOUT_SECONDS:-}" ] &&
    [ -n "${LAB_RATE_LIMIT_PER_MINUTE:-}" ] || {
      echo "Scale profile is incomplete or does not match: $scale_profile" >&2
      exit 1
    }
}

resolve_dataset() {
  if ! dataset_file=$(python3 "$lab_root/workload/dataset.py" --resolve-for-launch "$dataset_file" "$lab_root"); then
    exit 1
  fi
  [ -f "$dataset_file" ] || { echo "Dataset file could not be resolved." >&2; exit 1; }
  LAB_DATASET_FILE="$dataset_file"
  export LAB_DATASET_FILE
}

check_dataset() {
  python3 "$lab_root/workload/dataset.py" check "$LAB_DATASET_FILE"
}

initialize_sample() {
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
}

check_sample() {
  python3 "$lab_root/workload/scenario.py" "$LAB_SAMPLE_FILE"
}

if [ "$command_name" = "proof" ] || [ "$command_name" = "report" ]; then
  latest_proof="$artifacts/latest-receipt.json"
  [ -f "$latest_proof" ] || { echo "No exported proof exists yet. Run './lab.sh up' first." >&2; exit 1; }
  cat "$latest_proof"
  exit 0
fi

if [ "$command_name" = "capacity-report" ]; then
  latest_capacity="$artifacts/latest-capacity-receipt.json"
  [ -f "$latest_capacity" ] || { echo "No capacity receipt exists yet. Run './lab.sh ingest-dataset' first." >&2; exit 1; }
  cat "$latest_capacity"
  exit 0
fi

if [ "$command_name" = "list-integrations" ]; then
  require_python
  python3 "$lab_root/workload/catalog.py" "$lab_root/integrations/catalog.json"
  exit 0
fi

if [ "$command_name" = "check-dataset" ]; then
  require_python
  load_scale_profile
  resolve_dataset
  check_dataset
  exit 0
fi

if [ "$command_name" = "generate-dataset" ]; then
  require_python
  load_scale_profile
  if [ "$dataset_was_set" != true ]; then
    dataset_file="$lab_root/datasets/generated.local.ndjson"
  fi
  python3 "$lab_root/workload/dataset.py" generate "$dataset_file" \
    --records "$records" \
    --dataset-id "generated-local" \
    --agent-id "lians-homelab-dataset" \
    --lab-root "$lab_root"
  exit 0
fi

require_python

if [ "$command_name" = "ingest-dataset" ]; then
  load_scale_profile
  resolve_dataset
  check_dataset
fi

initialize_sample

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

env_status=$(python3 "$lab_root/env_bootstrap.py" "$example_env" "$env_file")
case "$env_status" in
  created) echo "Created homelab/.env with random local secrets." ;;
  upgraded) echo "Added a random Evidence Pack signing key to the existing homelab/.env." ;;
  unchanged) : ;;
  *) echo "Unexpected homelab environment bootstrap result." >&2; exit 1 ;;
esac
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
  ingest-dataset)
    check_sample
    compose_real down --remove-orphans
    compose up --build -d
    compose --profile bulk build bulk-ingest
    compose --profile bulk run --rm --no-deps bulk-ingest
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
    echo "Usage: ./lab.sh {up|up-real|check-sample|list-integrations|check-dataset|generate-dataset|ingest-dataset|capacity-report|verify|verify-real|status|logs|logs-real|proof|report|down|dispose|reset} [--sample FILE] [--dataset FILE] [--scale-profile laptop|workstation|dedicated] [--records N] [--accept-sample-policy] [--force]" >&2
    exit 2
    ;;
esac
