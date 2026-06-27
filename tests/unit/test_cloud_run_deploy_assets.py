"""Regression tests for local Docker and Cloud Run deployment asset wiring."""

from pathlib import Path
import shutil
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cloud_run_local_docker_build_assets_exist_and_define_four_images():
    proxy_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "proxy" / "Dockerfile"
    prometheus_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile"
    grafana_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile"

    assert proxy_dockerfile.exists()
    assert prometheus_dockerfile.exists()
    assert grafana_dockerfile.exists()

    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    for image_name in [
        "gl-doc-capstone-app",
        "gl-doc-capstone-proxy",
        "gl-doc-capstone-prometheus",
        "gl-doc-capstone-grafana",
    ]:
        assert image_name in bash_text
        assert image_name in ps_text


def test_deploy_scripts_build_and_push_images_with_local_docker():
    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    for script_text in [bash_text, ps_text]:
        assert "gcloud builds submit" not in script_text
        assert "cloudbuild.cloudrun.yaml" not in script_text
        assert "cloudbuild.googleapis.com" not in script_text
        assert "gcloud auth configure-docker" in script_text
        assert "docker build" in script_text
        assert "docker push" in script_text

    assert "require_command docker" in bash_text
    assert 'Assert-Command "docker"' in ps_text


def test_deploy_scripts_build_cloud_run_images_for_amd64_linux():
    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    assert 'IMAGE_PLATFORM="${IMAGE_PLATFORM:-linux/amd64}"' in bash_text
    assert "--image-platform" in bash_text
    assert '--platform "$IMAGE_PLATFORM"' in bash_text

    assert '[string]$ImagePlatform = "linux/amd64"' in ps_text
    assert '"--platform", $ImagePlatform' in ps_text
    assert "& docker @buildArgs" in ps_text


def test_deploy_scripts_make_infra_and_secret_sync_opt_in():
    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    for flag in ["--setup-infra", "--sync-secrets"]:
        assert flag in bash_text
        assert flag.lstrip("-").replace("-", "").lower() in ps_text.replace("-", "").lower()

    assert 'SETUP_INFRA="false"' in bash_text
    assert 'SYNC_SECRETS="false"' in bash_text
    assert 'if [[ "$SETUP_INFRA" == "true" ]]' in bash_text
    assert 'if [[ "$SYNC_SECRETS" == "true" ]]' in bash_text
    assert "if ($SetupInfra)" in ps_text
    assert "if ($SyncSecrets)" in ps_text


def test_cloud_run_dockerfiles_allow_base_image_overrides():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    proxy_dockerfile = (REPO_ROOT / "docker" / "cloudrun" / "proxy" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    prometheus_dockerfile = (
        REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile"
    ).read_text(encoding="utf-8")
    grafana_dockerfile = (REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    assert "ARG PYTHON_BASE_IMAGE=python:3.11-slim" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE}" in dockerfile
    assert "ARG PROXY_BASE_IMAGE=nginx:1.27-alpine" in proxy_dockerfile
    assert "FROM ${PROXY_BASE_IMAGE}" in proxy_dockerfile
    assert "ARG PROMETHEUS_BASE_IMAGE=prom/prometheus:v2.54.1" in prometheus_dockerfile
    assert "FROM ${PROMETHEUS_BASE_IMAGE}" in prometheus_dockerfile
    assert "ARG GRAFANA_BASE_IMAGE=grafana/grafana-oss:11.2.0" in grafana_dockerfile
    assert "FROM ${GRAFANA_BASE_IMAGE}" in grafana_dockerfile

    for build_arg in [
        "PYTHON_BASE_IMAGE",
        "PROXY_BASE_IMAGE",
        "PROMETHEUS_BASE_IMAGE",
        "GRAFANA_BASE_IMAGE",
    ]:
        assert f"{build_arg}=" in bash_text
        assert f"{build_arg}=" in ps_text

    assert '--platform "$IMAGE_PLATFORM"' in bash_text
    assert '--build-arg "${build_args[$i]}"' in bash_text
    assert '"--platform", $ImagePlatform' in ps_text
    assert '"--build-arg", $buildArg' in ps_text


def test_proxy_routes_streamlit_and_protects_observability_paths():
    proxy_conf = REPO_ROOT / "docker" / "cloudrun" / "proxy" / "default.conf.template"
    entrypoint = REPO_ROOT / "docker" / "cloudrun" / "proxy" / "entrypoint.sh"

    assert proxy_conf.exists()
    assert entrypoint.exists()

    proxy_text = proxy_conf.read_text(encoding="utf-8")
    entrypoint_text = entrypoint.read_text(encoding="utf-8")

    assert "proxy_pass http://127.0.0.1:8501" in proxy_text
    assert "proxy_pass http://127.0.0.1:3000" in proxy_text
    assert "proxy_pass http://127.0.0.1:9090" in proxy_text
    assert "proxy_pass http://127.0.0.1:9091" in proxy_text
    assert 'auth_basic "LLMOps observability"' in proxy_text
    assert "auth_basic_user_file /etc/nginx/.htpasswd" in proxy_text
    assert "Upgrade $http_upgrade" in proxy_text
    assert "htpasswd -bc" in entrypoint_text


def test_proxy_entrypoint_is_lf_only_and_image_build_normalizes_line_endings():
    proxy_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "proxy" / "Dockerfile"
    entrypoint = REPO_ROOT / "docker" / "cloudrun" / "proxy" / "entrypoint.sh"

    assert b"\r" not in entrypoint.read_bytes()
    proxy_dockerfile_text = proxy_dockerfile.read_text(encoding="utf-8")
    assert "sed -i 's/\\r$//' /cloudrun-entrypoint.sh" in proxy_dockerfile_text
    assert 'ENTRYPOINT ["/bin/sh", "/cloudrun-entrypoint.sh"]' in proxy_dockerfile_text


def test_deploy_scripts_force_fresh_cloud_run_proxy_revision_by_default():
    bash_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ps_text = (REPO_ROOT / "scripts" / "deploy_cloud_run.ps1").read_text(encoding="utf-8")

    assert 'TAG="${TAG:-}"' in bash_text
    assert 'TAG="deploy-$(date -u +%Y%m%d%H%M%S)"' in bash_text
    assert "--no-cache" in bash_text

    assert '[string]$Tag = ""' in ps_text
    assert '$Tag = "deploy-{0}" -f (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")' in ps_text
    assert "--no-cache" in ps_text


def test_deploy_scripts_have_matching_flags_secret_sync_and_validation():
    bash_script = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
    powershell_script = REPO_ROOT / "scripts" / "deploy_cloud_run.ps1"

    assert bash_script.exists()
    assert powershell_script.exists()

    bash_text = bash_script.read_text(encoding="utf-8")
    ps_text = powershell_script.read_text(encoding="utf-8")

    for flag in [
        "--project",
        "--region",
        "--service",
        "--repo",
        "--tag",
        "--env-file",
        "--allow-unauthenticated",
        "--min-instances",
        "--setup-infra",
        "--sync-secrets",
        "--dry-run",
    ]:
        assert flag in bash_text
        assert flag.lstrip("-").replace("-", "").lower() in ps_text.replace("-", "").lower()

    for required in [
        "OPENAI_API_KEY",
        "GRAFANA_ADMIN_PASSWORD",
        "OPS_BASIC_AUTH_USER",
        "OPS_BASIC_AUTH_PASSWORD",
        "craft_mlt_25k.pth",
        "english_g2.pth",
        "roles/secretmanager.secretAccessor",
    ]:
        assert required in bash_text
        assert required in ps_text


def test_cloud_run_safe_env_and_model_bake_contract_are_declared():
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "!models/easyocr/craft_mlt_25k.pth" in dockerignore
    assert "!models/easyocr/english_g2.pth" in dockerignore
    assert "COPY models/easyocr /models/easyocr" in dockerfile
    assert "EASYOCR_MODEL_DIR=/models/easyocr" in env_example
    assert "OPS_BASIC_AUTH_USER=" in env_example
    assert "OPS_BASIC_AUTH_PASSWORD=" in env_example


def test_runtime_uses_headless_opencv_dependency_for_slim_linux_images():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lockfile = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert '"opencv-python-headless>=' in pyproject
    assert '"opencv-python>=' not in pyproject
    assert '{ name = "opencv-python-headless" }' in lockfile
    assert '{ name = "opencv-python" }' not in lockfile


def test_deploy_scripts_parse_successfully():
    bash_script = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
    powershell_script = REPO_ROOT / "scripts" / "deploy_cloud_run.ps1"

    subprocess.run(["bash", "-n", str(bash_script)], check=True)

    if shutil.which("pwsh"):
        subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                f"[scriptblock]::Create((Get-Content -Raw '{powershell_script}')) | Out-Null",
            ],
            check=True,
        )
