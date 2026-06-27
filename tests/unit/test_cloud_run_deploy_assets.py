"""Regression tests for local Docker and Cloud Run deployment asset wiring."""

from pathlib import Path
import shutil
import subprocess
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _powershell_command() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_cloud_run_local_docker_build_assets_exist_and_define_runtime_images():
    dockerfile = REPO_ROOT / "Dockerfile"
    prometheus_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile"
    grafana_dockerfile = REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile"

    assert dockerfile.exists()
    assert prometheus_dockerfile.exists()
    assert grafana_dockerfile.exists()

    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    for image_name in [
        "gl-doc-capstone-app",
        "gl-doc-capstone-prometheus",
        "gl-doc-capstone-grafana",
    ]:
        assert image_name in bash_text
        assert image_name in ps_text

    assert "gcloud auth configure-docker" in bash_text
    assert "gcloud auth configure-docker" in ps_text
    assert "docker build" in bash_text
    assert "docker build" in ps_text
    assert "docker push" in bash_text
    assert "docker push" in ps_text


def test_deploy_scripts_use_local_docker_and_per_service_names():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    for script_text in [bash_text, ps_text]:
        assert "gcloud builds submit" not in script_text
        assert "cloudbuild.cloudrun.yaml" not in script_text
        assert "cloudbuild.googleapis.com" not in script_text
        assert "docker build" in script_text
        assert "docker push" in script_text
        assert "gcloud auth configure-docker" in script_text
        assert "gl-doc-capstone-proxy" not in script_text

    assert "require_command docker" in bash_text
    assert 'Assert-Command "docker"' in ps_text
    assert "-app" in bash_text
    assert "-grafana" in bash_text
    assert "-prometheus" in bash_text
    assert "-pushgateway" in bash_text
    assert '-app"' in ps_text
    assert '-grafana"' in ps_text
    assert '-prometheus"' in ps_text
    assert '-pushgateway"' in ps_text


def test_deploy_scripts_use_direct_urls_and_no_sidecar_localhost_contract():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    for script_text in [bash_text, ps_text]:
        assert "127.0.0.1:9091" not in script_text
        assert "127.0.0.1:9090" not in script_text
        assert "GF_SERVER_ROOT_URL" not in script_text
        assert "GF_SERVER_SERVE_FROM_SUB_PATH" not in script_text
        assert "name: proxy" not in script_text
        assert "PROMETHEUS_PUSHGATEWAY_URL" in script_text
        assert "GRAFANA_ADMIN_PASSWORD" in script_text

    assert 'PROMETHEUS_PUSHGATEWAY_URL=http://pushgateway:9091' not in _read_text(
        REPO_ROOT / "docs" / "llmops.md"
    )


def test_deploy_scripts_make_infra_and_secret_sync_opt_in():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

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
    dockerfile = _read_text(REPO_ROOT / "Dockerfile")
    prometheus_dockerfile = _read_text(REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile")
    grafana_dockerfile = _read_text(REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile")
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    assert "ARG PYTHON_BASE_IMAGE=python:3.11-slim" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE}" in dockerfile
    assert "ARG PROMETHEUS_BASE_IMAGE=prom/prometheus:v2.54.1" in prometheus_dockerfile
    assert "FROM ${PROMETHEUS_BASE_IMAGE}" in prometheus_dockerfile
    assert "ARG GRAFANA_BASE_IMAGE=grafana/grafana-oss:11.2.0" in grafana_dockerfile
    assert "FROM ${GRAFANA_BASE_IMAGE}" in grafana_dockerfile

    for build_arg in [
        "PYTHON_BASE_IMAGE",
        "PROMETHEUS_BASE_IMAGE",
        "GRAFANA_BASE_IMAGE",
    ]:
        assert f"{build_arg}=" in bash_text
        assert f"{build_arg}=" in ps_text

    assert '--platform "$IMAGE_PLATFORM"' in bash_text
    assert "--platform" in ps_text


def test_app_image_proxies_streamlit_and_metrics_on_single_public_port():
    dockerfile = REPO_ROOT / "Dockerfile"
    nginx_template = REPO_ROOT / "docker" / "cloudrun" / "app" / "default.conf.template"
    entrypoint = REPO_ROOT / "docker" / "cloudrun" / "app" / "entrypoint.sh"

    assert dockerfile.exists()
    assert nginx_template.exists()
    assert entrypoint.exists()

    dockerfile_text = _read_text(dockerfile)
    nginx_text = _read_text(nginx_template)
    entrypoint_text = _read_text(entrypoint)

    assert "nginx" in dockerfile_text
    assert "docker/cloudrun/app/default.conf.template" in dockerfile_text
    assert "docker/cloudrun/app/entrypoint.sh" in dockerfile_text
    assert 'ENTRYPOINT ["/bin/sh", "/cloudrun-app-entrypoint.sh"]' in dockerfile_text
    assert "proxy_pass http://127.0.0.1:8502" in nginx_text
    assert "proxy_pass http://127.0.0.1:9108" in nginx_text
    assert "/metrics" in nginx_text
    assert "streamlit run app_frontend.py" in entrypoint_text
    assert "8502" in entrypoint_text


def test_prometheus_cloud_run_assets_template_remote_targets():
    dockerfile = REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile"
    config_template = REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "prometheus.yml.tmpl"
    entrypoint = REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "entrypoint.sh"

    assert dockerfile.exists()
    assert config_template.exists()
    assert entrypoint.exists()

    dockerfile_text = _read_text(dockerfile)
    config_text = _read_text(config_template)
    entrypoint_text = _read_text(entrypoint)

    assert "prometheus.yml.tmpl" in dockerfile_text
    assert "entrypoint.sh" in dockerfile_text
    assert "APP_METRICS_HOST" in config_text
    assert "PUSHGATEWAY_HOST" in config_text
    assert "scheme: https" in config_text
    assert "127.0.0.1" not in config_text
    assert "APP_METRICS_HOST" in entrypoint_text
    assert "PUSHGATEWAY_HOST" in entrypoint_text


def test_grafana_cloud_run_assets_template_remote_prometheus_url():
    dockerfile = REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile"
    datasource_template = (
        REPO_ROOT / "docker" / "cloudrun" / "grafana" / "provisioning" / "datasources" / "prometheus.yml.tmpl"
    )
    entrypoint = REPO_ROOT / "docker" / "cloudrun" / "grafana" / "entrypoint.sh"

    assert dockerfile.exists()
    assert datasource_template.exists()
    assert b"\r" not in entrypoint.read_bytes()
    dockerfile_text = _read_text(dockerfile)
    datasource_text = _read_text(datasource_template)
    entrypoint_text = _read_text(entrypoint)

    assert "prometheus.yml.tmpl" in dockerfile_text
    assert "entrypoint.sh" in dockerfile_text
    assert "GF_PROMETHEUS_URL" in datasource_text
    assert "127.0.0.1" not in datasource_text
    assert "/prometheus" not in datasource_text
    assert "GF_PROMETHEUS_URL" in entrypoint_text


def test_entrypoint_scripts_are_lf_only():
    for entrypoint in [
        REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "entrypoint.sh",
        REPO_ROOT / "docker" / "cloudrun" / "grafana" / "entrypoint.sh",
    ]:
        assert entrypoint.exists()
        assert b"\r" not in entrypoint.read_bytes()


def test_cloud_run_dockerfiles_do_not_mutate_entrypoints_under_non_root_users():
    for dockerfile in [
        REPO_ROOT / "docker" / "cloudrun" / "prometheus" / "Dockerfile",
        REPO_ROOT / "docker" / "cloudrun" / "grafana" / "Dockerfile",
    ]:
        dockerfile_text = _read_text(dockerfile)
        assert "COPY --chmod=755" in dockerfile_text
        assert "sed -i" not in dockerfile_text
        assert "chmod +x" not in dockerfile_text


def test_pushgateway_cloud_run_memory_meets_unthrottled_cpu_minimum():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    bash_block = bash_text.split("write_pushgateway_service_yaml() {", 1)[1].split(
        "write_app_service_yaml() {", 1
    )[0]
    ps_block = ps_text.split("function Write-PushgatewayServiceYaml {", 1)[1].split(
        "function Write-AppServiceYaml {", 1
    )[0]

    assert 'run.googleapis.com/cpu-throttling: "false"' in bash_block
    assert 'memory: 512Mi' in bash_block
    assert 'memory: 256Mi' not in bash_block
    assert 'run.googleapis.com/cpu-throttling: `"false`"' in ps_block
    assert 'memory: 512Mi' in ps_block
    assert 'memory: 256Mi' not in ps_block


def test_bash_deploy_service_yaml_stops_after_replace_failure():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")

    assert 'if ! gcloud run services replace "$yaml_path" \\' in bash_text
    assert 'return 1' in bash_text
    assert 'apply_public_access_policy "$service_name"' in bash_text


def test_deploy_scripts_force_fresh_cloud_run_revision_by_default():
    bash_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.sh")
    ps_text = _read_text(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1")

    assert 'TAG="${TAG:-}"' in bash_text
    assert 'TAG="deploy-$(date -u +%Y%m%d%H%M%S)"' in bash_text

    assert '[string]$Tag = ""' in ps_text
    assert '$Tag = "deploy-{0}" -f (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")' in ps_text


def test_deploy_scripts_have_matching_flags_secret_sync_and_validation():
    bash_script = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
    powershell_script = REPO_ROOT / "scripts" / "deploy_cloud_run.ps1"

    assert bash_script.exists()
    assert powershell_script.exists()

    bash_text = _read_text(bash_script)
    ps_text = _read_text(powershell_script)

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
        "craft_mlt_25k.pth",
        "english_g2.pth",
        "roles/secretmanager.secretAccessor",
    ]:
        assert required in bash_text
        assert required in ps_text

    assert "require_command docker" in bash_text
    assert 'Assert-Command "docker"' in ps_text

    for removed in [
        "OPS_BASIC_AUTH_USER",
        "OPS_BASIC_AUTH_PASSWORD",
    ]:
        assert removed not in bash_text
        assert removed not in ps_text


def test_cloud_run_safe_env_and_model_bake_contract_are_declared():
    dockerignore = _read_text(REPO_ROOT / ".dockerignore")
    env_example = _read_text(REPO_ROOT / ".env.example")
    dockerfile = _read_text(REPO_ROOT / "Dockerfile")

    assert "!models/easyocr/craft_mlt_25k.pth" in dockerignore
    assert "!models/easyocr/english_g2.pth" in dockerignore
    assert "COPY models/easyocr /models/easyocr" in dockerfile
    assert "EASYOCR_MODEL_DIR=/models/easyocr" in env_example
    assert "OPS_BASIC_AUTH_USER=" not in env_example
    assert "OPS_BASIC_AUTH_PASSWORD=" not in env_example


def test_runtime_uses_headless_opencv_dependency_for_slim_linux_images():
    pyproject = _read_text(REPO_ROOT / "pyproject.toml")
    lockfile = _read_text(REPO_ROOT / "uv.lock")

    assert '"opencv-python-headless>=' in pyproject
    assert '"opencv-python>=' not in pyproject
    assert '{ name = "opencv-python-headless" }' in lockfile
    assert '{ name = "opencv-python" }' not in lockfile


def test_powershell_dry_run_outputs_per_service_targets():
    powershell = _powershell_command()
    if not powershell:
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "OPENAI_API_KEY=test-key",
                    "GRAFANA_ADMIN_PASSWORD=test-password",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPO_ROOT / "scripts" / "deploy_cloud_run.ps1"),
                "-Project",
                "sample-project",
                "-EnvFile",
                str(env_file),
                "-DryRun",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    assert "App service:" in result.stdout
    assert "Grafana service:" in result.stdout
    assert "Prometheus service:" in result.stdout
    assert "Pushgateway service:" in result.stdout
    assert "Pushgateway target URL:" in result.stdout


def test_deploy_scripts_parse_successfully():
    bash_script = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
    powershell_script = REPO_ROOT / "scripts" / "deploy_cloud_run.ps1"

    if shutil.which("bash"):
        try:
            subprocess.run(
                ["bash", "-n", str(bash_script)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            if "/bin/bash" not in (exc.stderr or ""):
                raise

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
