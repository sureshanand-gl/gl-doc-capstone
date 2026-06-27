#!/usr/bin/env bash
# Bash deploy helper that builds, pushes, and provisions per-service Cloud Run stack.

set -euo pipefail

PROJECT="${PROJECT:-}"
REGION="us-central1"
SERVICE="gl-doc-capstone"
REPO="gl-doc-capstone"
TAG="${TAG:-}"
ENV_FILE=".env"
ALLOW_UNAUTHENTICATED="true"
MIN_INSTANCES="1"
DRY_RUN="false"
SETUP_INFRA="false"
SYNC_SECRETS="false"
IMAGE_PLATFORM="${IMAGE_PLATFORM:-linux/amd64}"
PYTHON_BASE_IMAGE="${PYTHON_BASE_IMAGE:-python:3.11-slim}"
PROMETHEUS_BASE_IMAGE="${PROMETHEUS_BASE_IMAGE:-prom/prometheus:v2.54.1}"
GRAFANA_BASE_IMAGE="${GRAFANA_BASE_IMAGE:-grafana/grafana-oss:11.2.0}"

APP_SECRET_ENV_KEYS=(
  OPENAI_API_KEY
  OPENAI_API_BASE
  FIELD_EXTRACTOR_MODE
  LLMOPS_PROMPT_VERSION
  LLMOPS_SCHEMA_VERSION
  LLMOPS_TRACE_TEXT
  PROMETHEUS_METRICS_PORT
  LLMOPS_PRICING_FILE
  EASYOCR_MODEL_DIR
  QWEN_MODEL_DIR
  LAYOUT_WORKER_PYTHON
)

REQUIRED_ENV_KEYS=(
  OPENAI_API_KEY
  GRAFANA_ADMIN_PASSWORD
)

usage() {
  cat <<'USAGE'
Usage: scripts/deploy_cloud_run.sh [options]

Options:
  --project PROJECT_ID
  --region REGION
  --service SERVICE_PREFIX
  --repo ARTIFACT_REGISTRY_REPO
  --tag IMAGE_TAG
  --env-file PATH
  --allow-unauthenticated
  --no-allow-unauthenticated
  --min-instances COUNT
  --setup-infra
  --sync-secrets
  --image-platform PLATFORM
  --python-base-image IMAGE
  --prometheus-base-image IMAGE
  --grafana-base-image IMAGE
  --dry-run
  -h, --help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --allow-unauthenticated) ALLOW_UNAUTHENTICATED="true"; shift ;;
    --no-allow-unauthenticated) ALLOW_UNAUTHENTICATED="false"; shift ;;
    --min-instances) MIN_INSTANCES="$2"; shift 2 ;;
    --setup-infra) SETUP_INFRA="true"; shift ;;
    --sync-secrets) SYNC_SECRETS="true"; shift ;;
    --image-platform) IMAGE_PLATFORM="$2"; shift 2 ;;
    --python-base-image) PYTHON_BASE_IMAGE="$2"; shift 2 ;;
    --prometheus-base-image) PROMETHEUS_BASE_IMAGE="$2"; shift 2 ;;
    --grafana-base-image) GRAFANA_BASE_IMAGE="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

ENV_KEYS=()
ENV_VALS=()

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

strip_quotes() {
  local value="$1"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

env_index() {
  local key="$1"
  local i
  for ((i = 0; i < ${#ENV_KEYS[@]}; i++)); do
    if [[ "${ENV_KEYS[$i]}" == "$key" ]]; then
      printf '%s' "$i"
      return 0
    fi
  done
  return 1
}

get_env() {
  local key="$1"
  local index
  if index="$(env_index "$key")"; then
    printf '%s' "${ENV_VALS[$index]}"
  fi
}

set_env() {
  local key="$1"
  local value="$2"
  local index
  if index="$(env_index "$key")"; then
    ENV_VALS[$index]="$value"
  else
    ENV_KEYS+=("$key")
    ENV_VALS+=("$value")
  fi
}

set_default() {
  local key="$1"
  local value="$2"
  if [[ -z "$(get_env "$key")" ]]; then
    set_env "$key" "$value"
  fi
}

load_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Env file not found: $ENV_FILE" >&2
    exit 1
  fi

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line key value
    line="$(trim "$raw_line")"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == export\ * ]] && line="$(trim "${line#export }")"
    [[ "$line" != *=* ]] && continue
    key="$(trim "${line%%=*}")"
    value="$(strip_quotes "$(trim "${line#*=}")")"
    [[ -z "$key" ]] && continue
    set_env "$key" "$value"
  done < "$ENV_FILE"
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || {
    echo "$name is required" >&2
    exit 1
  }
}

secret_name_for() {
  local key="$1"
  printf '%s-%s' "$SERVICE" "$key"
}

host_from_url() {
  local url="$1"
  url="${url#https://}"
  url="${url#http://}"
  printf '%s' "${url%%/*}"
}

validate_inputs() {
  if [[ -z "$PROJECT" ]]; then
    PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
  fi
  if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
    echo "--project is required when gcloud project is unset" >&2
    exit 1
  fi

  for key in "${REQUIRED_ENV_KEYS[@]}"; do
    if [[ -z "$(get_env "$key")" ]]; then
      echo "$key is required in $ENV_FILE" >&2
      exit 1
    fi
  done

  for model_file in models/easyocr/craft_mlt_25k.pth models/easyocr/english_g2.pth; do
    if [[ ! -f "$model_file" ]]; then
      echo "Required EasyOCR model file missing: $model_file" >&2
      exit 1
    fi
  done
}

sync_secret() {
  local key="$1"
  local value
  value="$(get_env "$key")"
  [[ -z "$value" ]] && return 0

  local secret_name
  secret_name="$(secret_name_for "$key")"
  if ! gcloud secrets describe "$secret_name" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud secrets create "$secret_name" \
      --project "$PROJECT" \
      --replication-policy=automatic \
      --quiet
  fi
  printf '%s' "$value" | gcloud secrets versions add "$secret_name" \
    --project "$PROJECT" \
    --data-file=- \
    --quiet >/dev/null
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --project "$PROJECT" \
    --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role roles/secretmanager.secretAccessor \
    --quiet >/dev/null
}

write_secret_env_yaml() {
  local indent="$1"
  shift
  local key
  for key in "$@"; do
    [[ -z "$(get_env "$key")" ]] && continue
    cat <<YAML
${indent}- name: ${key}
${indent}  valueFrom:
${indent}    secretKeyRef:
${indent}      name: $(secret_name_for "$key")
${indent}      key: latest
YAML
  done
}

write_literal_env_yaml() {
  local indent="$1"
  local key="$2"
  local value="$3"
  cat <<YAML
${indent}- name: ${key}
${indent}  value: "${value}"
YAML
}

write_pushgateway_service_yaml() {
  local yaml_path="$1"
  cat <<YAML > "$yaml_path"
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${PUSHGATEWAY_SERVICE}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "${MIN_INSTANCES}"
        run.googleapis.com/cpu-throttling: "false"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_EMAIL}
      containers:
        - name: pushgateway
          image: prom/pushgateway:v1.8.0
          ports:
            - name: http1
              containerPort: 9091
          args:
            - --web.listen-address=0.0.0.0:9091
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
YAML
}

write_app_service_yaml() {
  local yaml_path="$1"
  local pushgateway_url="$2"
  {
    cat <<YAML
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${APP_SERVICE}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "${MIN_INSTANCES}"
        run.googleapis.com/cpu-throttling: "false"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_EMAIL}
      containers:
        - name: app
          image: ${APP_IMAGE}
          ports:
            - name: http1
              containerPort: 8501
          env:
YAML
    write_secret_env_yaml "            " "${APP_SECRET_ENV_KEYS[@]}"
    write_literal_env_yaml "            " "PROMETHEUS_PUSHGATEWAY_URL" "${pushgateway_url}"
    cat <<YAML
          resources:
            limits:
              cpu: "2"
              memory: 4Gi
YAML
  } > "$yaml_path"
}

write_prometheus_service_yaml() {
  local yaml_path="$1"
  local app_metrics_host="$2"
  local pushgateway_host="$3"
  {
    cat <<YAML
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${PROMETHEUS_SERVICE}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "${MIN_INSTANCES}"
        run.googleapis.com/cpu-throttling: "false"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_EMAIL}
      containers:
        - name: prometheus
          image: ${PROMETHEUS_IMAGE}
          ports:
            - name: http1
              containerPort: 9090
          env:
YAML
    write_literal_env_yaml "            " "APP_METRICS_HOST" "${app_metrics_host}"
    write_literal_env_yaml "            " "PUSHGATEWAY_HOST" "${pushgateway_host}"
    cat <<YAML
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
YAML
  } > "$yaml_path"
}

write_grafana_service_yaml() {
  local yaml_path="$1"
  local prometheus_url="$2"
  {
    cat <<YAML
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${GRAFANA_SERVICE}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "${MIN_INSTANCES}"
        run.googleapis.com/cpu-throttling: "false"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_EMAIL}
      containers:
        - name: grafana
          image: ${GRAFANA_IMAGE}
          ports:
            - name: http1
              containerPort: 3000
          env:
YAML
    write_secret_env_yaml "            " GRAFANA_ADMIN_PASSWORD
    write_literal_env_yaml "            " "GF_SECURITY_ADMIN_USER" "admin"
    write_literal_env_yaml "            " "GF_USERS_ALLOW_SIGN_UP" "false"
    write_literal_env_yaml "            " "GF_SERVER_HTTP_PORT" "3000"
    write_literal_env_yaml "            " "GF_PROMETHEUS_URL" "${prometheus_url}"
    cat <<YAML
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
YAML
  } > "$yaml_path"
}

build_and_push_images() {
  local registry_host="${REGION}-docker.pkg.dev"
  gcloud auth configure-docker "$registry_host" --quiet >/dev/null

  local dockerfiles=(
    Dockerfile
    docker/cloudrun/prometheus/Dockerfile
    docker/cloudrun/grafana/Dockerfile
  )
  local build_args=(
    "PYTHON_BASE_IMAGE=$PYTHON_BASE_IMAGE"
    "PROMETHEUS_BASE_IMAGE=$PROMETHEUS_BASE_IMAGE"
    "GRAFANA_BASE_IMAGE=$GRAFANA_BASE_IMAGE"
  )
  local images=(
    "$APP_IMAGE"
    "$PROMETHEUS_IMAGE"
    "$GRAFANA_IMAGE"
  )

  local i
  for ((i = 0; i < ${#images[@]}; i++)); do
    docker build \
      --platform "$IMAGE_PLATFORM" \
      --build-arg "${build_args[$i]}" \
      -t "${images[$i]}" \
      -f "${dockerfiles[$i]}" .
    docker push "${images[$i]}"
  done
}

apply_public_access_policy() {
  local service_name="$1"
  if [[ "$ALLOW_UNAUTHENTICATED" == "true" ]]; then
    gcloud run services add-iam-policy-binding "$service_name" \
      --project "$PROJECT" \
      --region "$REGION" \
      --member allUsers \
      --role roles/run.invoker \
      --quiet >/dev/null
  else
    gcloud run services remove-iam-policy-binding "$service_name" \
      --project "$PROJECT" \
      --region "$REGION" \
      --member allUsers \
      --role roles/run.invoker \
      --quiet >/dev/null || true
  fi
}

describe_service_url() {
  local service_name="$1"
  gcloud run services describe "$service_name" \
    --project "$PROJECT" \
    --region "$REGION" \
    --format 'value(status.url)'
}

deploy_service_yaml() {
  local service_name="$1"
  local yaml_path="$2"
  if ! gcloud run services replace "$yaml_path" \
    --project "$PROJECT" \
    --region "$REGION" >/dev/null; then
    return 1
  fi
  apply_public_access_policy "$service_name"
  describe_service_url "$service_name"
}

load_env_file
set_default OPENAI_API_BASE "https://aibe.mygreatlearning.com/openai/v1"
set_default FIELD_EXTRACTOR_MODE "auto"
set_default LLMOPS_PROMPT_VERSION "v2"
set_default LLMOPS_SCHEMA_VERSION "v2"
set_default LLMOPS_TRACE_TEXT "false"
set_default PROMETHEUS_METRICS_PORT "9108"
set_default LLMOPS_PRICING_FILE "/app/configs/model_pricing.yaml"
set_default EASYOCR_MODEL_DIR "/models/easyocr"
set_default LAYOUT_WORKER_PYTHON "/app/.venv/bin/python"

if [[ "$DRY_RUN" != "true" ]]; then
  require_command gcloud
  require_command docker
fi

validate_inputs

if [[ -z "$TAG" ]]; then
  TAG="deploy-$(date -u +%Y%m%d%H%M%S)"
fi

IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
APP_IMAGE="${IMAGE_BASE}/gl-doc-capstone-app:${TAG}"
PROMETHEUS_IMAGE="${IMAGE_BASE}/gl-doc-capstone-prometheus:${TAG}"
GRAFANA_IMAGE="${IMAGE_BASE}/gl-doc-capstone-grafana:${TAG}"
APP_SERVICE="${SERVICE}-app"
PROMETHEUS_SERVICE="${SERVICE}-prometheus"
GRAFANA_SERVICE="${SERVICE}-grafana"
PUSHGATEWAY_SERVICE="${SERVICE}-pushgateway"
SERVICE_ACCOUNT_NAME="${SERVICE}-runtime"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT}.iam.gserviceaccount.com"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run OK"
  echo "Project: $PROJECT"
  echo "Region: $REGION"
  echo "Service prefix: $SERVICE"
  echo "Repo: $REPO"
  echo "Image platform: $IMAGE_PLATFORM"
  echo "Setup infra: $SETUP_INFRA"
  echo "Sync secrets: $SYNC_SECRETS"
  echo "App service: $APP_SERVICE"
  echo "Grafana service: $GRAFANA_SERVICE"
  echo "Prometheus service: $PROMETHEUS_SERVICE"
  echo "Pushgateway service: $PUSHGATEWAY_SERVICE"
  echo "App image: $APP_IMAGE"
  echo "Prometheus image: $PROMETHEUS_IMAGE"
  echo "Grafana image: $GRAFANA_IMAGE"
  echo "Pushgateway image: prom/pushgateway:v1.8.0"
  echo "Pushgateway target URL: resolved after deploy"
  echo "Prometheus targets: app /metrics + pushgateway /metrics"
  echo "Grafana target: direct Prometheus service URL"
  exit 0
fi

if [[ "$SETUP_INFRA" == "true" ]]; then
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    iam.googleapis.com \
    --project "$PROJECT"

  if ! gcloud artifacts repositories describe "$REPO" \
    --project "$PROJECT" \
    --location "$REGION" >/dev/null 2>&1; then
    gcloud artifacts repositories create "$REPO" \
      --project "$PROJECT" \
      --location "$REGION" \
      --repository-format=docker \
      --description "gl-doc-capstone Cloud Run images"
  fi

  if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" \
    --project "$PROJECT" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
      --project "$PROJECT" \
      --display-name "${SERVICE} Cloud Run runtime"
  fi
fi

if [[ "$SYNC_SECRETS" == "true" ]]; then
  for key in "${APP_SECRET_ENV_KEYS[@]}" GRAFANA_ADMIN_PASSWORD; do
    sync_secret "$key"
  done
fi

build_and_push_images

PUSHGATEWAY_YAML="$(mktemp)"
APP_YAML="$(mktemp)"
PROMETHEUS_YAML="$(mktemp)"
GRAFANA_YAML="$(mktemp)"

cleanup() {
  rm -f "$PUSHGATEWAY_YAML" "$APP_YAML" "$PROMETHEUS_YAML" "$GRAFANA_YAML"
}
trap cleanup EXIT

write_pushgateway_service_yaml "$PUSHGATEWAY_YAML"
PUSHGATEWAY_URL="$(deploy_service_yaml "$PUSHGATEWAY_SERVICE" "$PUSHGATEWAY_YAML")"

write_app_service_yaml "$APP_YAML" "$PUSHGATEWAY_URL"
APP_URL="$(deploy_service_yaml "$APP_SERVICE" "$APP_YAML")"

APP_METRICS_HOST="$(host_from_url "$APP_URL")"
PUSHGATEWAY_HOST="$(host_from_url "$PUSHGATEWAY_URL")"
write_prometheus_service_yaml "$PROMETHEUS_YAML" "$APP_METRICS_HOST" "$PUSHGATEWAY_HOST"
PROMETHEUS_URL="$(deploy_service_yaml "$PROMETHEUS_SERVICE" "$PROMETHEUS_YAML")"

write_grafana_service_yaml "$GRAFANA_YAML" "$PROMETHEUS_URL"
GRAFANA_URL="$(deploy_service_yaml "$GRAFANA_SERVICE" "$GRAFANA_YAML")"

echo "App URL: $APP_URL"
echo "Grafana URL: $GRAFANA_URL"
echo "Prometheus URL: $PROMETHEUS_URL"
echo "Pushgateway URL: $PUSHGATEWAY_URL"
