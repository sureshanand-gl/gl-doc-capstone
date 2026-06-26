#!/usr/bin/env bash
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
PROXY_BASE_IMAGE="${PROXY_BASE_IMAGE:-nginx:1.27-alpine}"
PROMETHEUS_BASE_IMAGE="${PROMETHEUS_BASE_IMAGE:-prom/prometheus:v2.54.1}"
GRAFANA_BASE_IMAGE="${GRAFANA_BASE_IMAGE:-grafana/grafana-oss:11.2.0}"

APP_ENV_KEYS=(
  OPENAI_API_KEY
  OPENAI_API_BASE
  FIELD_EXTRACTOR_MODE
  LLMOPS_PROMPT_VERSION
  LLMOPS_SCHEMA_VERSION
  LLMOPS_TRACE_TEXT
  PROMETHEUS_METRICS_PORT
  PROMETHEUS_PUSHGATEWAY_URL
  LLMOPS_PRICING_FILE
  EASYOCR_MODEL_DIR
  QWEN_MODEL_DIR
  LAYOUT_WORKER_PYTHON
)

REQUIRED_ENV_KEYS=(
  OPENAI_API_KEY
  GRAFANA_ADMIN_PASSWORD
  OPS_BASIC_AUTH_USER
  OPS_BASIC_AUTH_PASSWORD
)

usage() {
  cat <<'USAGE'
Usage: scripts/deploy_cloud_run.sh [options]

Options:
  --project PROJECT_ID
  --region REGION
  --service SERVICE_NAME
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
  --proxy-base-image IMAGE
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
    --proxy-base-image) PROXY_BASE_IMAGE="$2"; shift 2 ;;
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

secret_name_for() {
  local key="$1"
  printf '%s-%s' "$SERVICE" "$key"
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || {
    echo "$name is required" >&2
    exit 1
  }
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

write_service_yaml() {
  local yaml_path="$1"
  {
    cat <<YAML
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ${SERVICE}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "${MIN_INSTANCES}"
    spec:
      serviceAccountName: ${SERVICE_ACCOUNT_EMAIL}
      containers:
        - name: proxy
          image: ${PROXY_IMAGE}
          ports:
            - name: http1
              containerPort: 8080
          env:
YAML
    write_secret_env_yaml "            " OPS_BASIC_AUTH_USER OPS_BASIC_AUTH_PASSWORD
    cat <<YAML
          resources:
            limits:
              cpu: "1"
              memory: 256Mi
        - name: app
          image: ${APP_IMAGE}
          env:
YAML
    write_secret_env_yaml "            " "${APP_ENV_KEYS[@]}"
    cat <<YAML
          resources:
            limits:
              cpu: "2"
              memory: 4Gi
        - name: prometheus
          image: ${PROMETHEUS_IMAGE}
          resources:
            limits:
              cpu: "1"
              memory: 512Mi
        - name: pushgateway
          image: prom/pushgateway:v1.8.0
          args:
            - --web.listen-address=0.0.0.0:9091
          resources:
            limits:
              cpu: "1"
              memory: 256Mi
        - name: grafana
          image: ${GRAFANA_IMAGE}
          env:
YAML
    write_secret_env_yaml "            " GRAFANA_ADMIN_PASSWORD
    write_literal_env_yaml "            " GF_SECURITY_ADMIN_USER "admin"
    write_literal_env_yaml "            " GF_USERS_ALLOW_SIGN_UP "false"
    write_literal_env_yaml "            " GF_SERVER_HTTP_PORT "3000"
    write_literal_env_yaml "            " GF_SERVER_ROOT_URL "%(protocol)s://%(domain)s/grafana/"
    write_literal_env_yaml "            " GF_SERVER_SERVE_FROM_SUB_PATH "true"
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
  gcloud auth configure-docker "$registry_host" --quiet

  local dockerfiles=(
    Dockerfile
    docker/cloudrun/proxy/Dockerfile
    docker/cloudrun/prometheus/Dockerfile
    docker/cloudrun/grafana/Dockerfile
  )
  local build_args=(
    "PYTHON_BASE_IMAGE=$PYTHON_BASE_IMAGE"
    "PROXY_BASE_IMAGE=$PROXY_BASE_IMAGE"
    "PROMETHEUS_BASE_IMAGE=$PROMETHEUS_BASE_IMAGE"
    "GRAFANA_BASE_IMAGE=$GRAFANA_BASE_IMAGE"
  )
  local images=(
    "$APP_IMAGE"
    "$PROXY_IMAGE"
    "$PROMETHEUS_IMAGE"
    "$GRAFANA_IMAGE"
  )

  local i
  for ((i = 0; i < ${#images[@]}; i++)); do
    local build_options=(--platform "$IMAGE_PLATFORM" --build-arg "${build_args[$i]}")
    if [[ "${dockerfiles[$i]}" == "docker/cloudrun/proxy/Dockerfile" ]]; then
      build_options+=(--no-cache)
    fi
    docker build "${build_options[@]}" -t "${images[$i]}" -f "${dockerfiles[$i]}" .
    docker push "${images[$i]}"
  done
}

load_env_file
set_default OPENAI_API_BASE "https://aibe.mygreatlearning.com/openai/v1"
set_default FIELD_EXTRACTOR_MODE "auto"
set_default LLMOPS_PROMPT_VERSION "v2"
set_default LLMOPS_SCHEMA_VERSION "v2"
set_default LLMOPS_TRACE_TEXT "false"
set_default PROMETHEUS_METRICS_PORT "9108"
set_default PROMETHEUS_PUSHGATEWAY_URL "http://127.0.0.1:9091"
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
PROXY_IMAGE="${IMAGE_BASE}/gl-doc-capstone-proxy:${TAG}"
PROMETHEUS_IMAGE="${IMAGE_BASE}/gl-doc-capstone-prometheus:${TAG}"
GRAFANA_IMAGE="${IMAGE_BASE}/gl-doc-capstone-grafana:${TAG}"
SERVICE_ACCOUNT_NAME="${SERVICE}-runtime"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT}.iam.gserviceaccount.com"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run OK"
  echo "Project: $PROJECT"
  echo "Region: $REGION"
  echo "Service: $SERVICE"
  echo "Repo: $REPO"
  echo "Image platform: $IMAGE_PLATFORM"
  echo "Setup infra: $SETUP_INFRA"
  echo "Sync secrets: $SYNC_SECRETS"
  echo "Images:"
  echo "  $APP_IMAGE"
  echo "  $PROXY_IMAGE"
  echo "  $PROMETHEUS_IMAGE"
  echo "  $GRAFANA_IMAGE"
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
  for key in "${APP_ENV_KEYS[@]}" GRAFANA_ADMIN_PASSWORD OPS_BASIC_AUTH_USER OPS_BASIC_AUTH_PASSWORD; do
    sync_secret "$key"
  done
fi

build_and_push_images

SERVICE_YAML="$(mktemp)"
write_service_yaml "$SERVICE_YAML"
gcloud run services replace "$SERVICE_YAML" \
  --project "$PROJECT" \
  --region "$REGION"

if [[ "$ALLOW_UNAUTHENTICATED" == "true" ]]; then
  gcloud run services add-iam-policy-binding "$SERVICE" \
    --project "$PROJECT" \
    --region "$REGION" \
    --member allUsers \
    --role roles/run.invoker \
    --quiet >/dev/null
else
  gcloud run services remove-iam-policy-binding "$SERVICE" \
    --project "$PROJECT" \
    --region "$REGION" \
    --member allUsers \
    --role roles/run.invoker \
    --quiet >/dev/null || true
fi

gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format 'value(status.url)'
