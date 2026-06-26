param(
    [string]$Project = "",
    [string]$Region = "us-central1",
    [string]$Service = "gl-doc-capstone",
    [string]$Repo = "gl-doc-capstone",
    [string]$Tag = "",
    [string]$EnvFile = ".env",
    [bool]$AllowUnauthenticated = $true,
    [int]$MinInstances = 1,
    [switch]$SetupInfra,
    [switch]$SyncSecrets,
    [string]$ImagePlatform = "linux/amd64",
    [string]$PythonBaseImage = "python:3.11-slim",
    [string]$ProxyBaseImage = "nginx:1.27-alpine",
    [string]$PrometheusBaseImage = "prom/prometheus:v2.54.1",
    [string]$GrafanaBaseImage = "grafana/grafana-oss:11.2.0",
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$AppEnvKeys = @(
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "FIELD_EXTRACTOR_MODE",
    "LLMOPS_PROMPT_VERSION",
    "LLMOPS_SCHEMA_VERSION",
    "LLMOPS_TRACE_TEXT",
    "PROMETHEUS_METRICS_PORT",
    "PROMETHEUS_PUSHGATEWAY_URL",
    "LLMOPS_PRICING_FILE",
    "EASYOCR_MODEL_DIR",
    "QWEN_MODEL_DIR",
    "LAYOUT_WORKER_PYTHON"
)

$RequiredEnvKeys = @(
    "OPENAI_API_KEY",
    "GRAFANA_ADMIN_PASSWORD",
    "OPS_BASIC_AUTH_USER",
    "OPS_BASIC_AUTH_PASSWORD"
)

function Read-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw "Env file not found: $Path"
    }

    $values = @{}
    foreach ($rawLine in Get-Content $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $key = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key] = $value
    }
    return $values
}

function Set-Default {
    param(
        [hashtable]$Values,
        [string]$Key,
        [string]$Value
    )
    if (-not $Values.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Values[$Key])) {
        $Values[$Key] = $Value
    }
}

function Get-SecretName {
    param([string]$Key)
    return "$Service-$Key"
}

function Test-GcloudEntity {
    param([string[]]$ProbeArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud @ProbeArgs *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
}

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required"
    }
}

function Assert-Inputs {
    param([hashtable]$Values)
    if ([string]::IsNullOrWhiteSpace($Project)) {
        $script:Project = (& gcloud config get-value project 2>$null)
    }
    if ([string]::IsNullOrWhiteSpace($Project) -or $Project -eq "(unset)") {
        throw "-Project is required when gcloud project is unset"
    }

    foreach ($key in $RequiredEnvKeys) {
        if (-not $Values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($Values[$key])) {
            throw "$key is required in $EnvFile"
        }
    }

    foreach ($modelFile in @("models/easyocr/craft_mlt_25k.pth", "models/easyocr/english_g2.pth")) {
        if (-not (Test-Path $modelFile)) {
            throw "Required EasyOCR model file missing: $modelFile"
        }
    }
}

function Sync-Secret {
    param(
        [hashtable]$Values,
        [string]$Key
    )
    if (-not $Values.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Values[$Key])) {
        return
    }

    $secretName = Get-SecretName $Key
    $secretExists = Test-GcloudEntity @("secrets", "describe", $secretName, "--project", $Project)
    if (-not $secretExists) {
        & gcloud secrets create $secretName `
            --project $Project `
            --replication-policy automatic `
            --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud secrets create failed for $secretName"
        }
    }

    $tempFile = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tempFile, $Values[$Key])
        & gcloud secrets versions add $secretName `
            --project $Project `
            --data-file $tempFile `
            --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud secrets versions add failed for $secretName"
        }
    }
    finally {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    }

    & gcloud secrets add-iam-policy-binding $secretName `
        --project $Project `
        --member "serviceAccount:$ServiceAccountEmail" `
        --role roles/secretmanager.secretAccessor `
        --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud secrets add-iam-policy-binding failed for $secretName"
    }
}

function Add-SecretEnvYaml {
    param(
        [System.Text.StringBuilder]$Builder,
        [hashtable]$Values,
        [string[]]$Keys,
        [string]$Indent
    )
    foreach ($key in $Keys) {
        if (-not $Values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($Values[$key])) {
            continue
        }
        [void]$Builder.AppendLine("${Indent}- name: $key")
        [void]$Builder.AppendLine("${Indent}  valueFrom:")
        [void]$Builder.AppendLine("${Indent}    secretKeyRef:")
        [void]$Builder.AppendLine("${Indent}      name: $(Get-SecretName $key)")
        [void]$Builder.AppendLine("${Indent}      key: latest")
    }
}

function Add-LiteralEnvYaml {
    param(
        [System.Text.StringBuilder]$Builder,
        [string]$Key,
        [string]$Value,
        [string]$Indent
    )
    [void]$Builder.AppendLine("${Indent}- name: $Key")
    [void]$Builder.AppendLine("${Indent}  value: `"$Value`"")
}

function Write-ServiceYaml {
    param(
        [hashtable]$Values,
        [string]$Path
    )
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.AppendLine("apiVersion: serving.knative.dev/v1")
    [void]$builder.AppendLine("kind: Service")
    [void]$builder.AppendLine("metadata:")
    [void]$builder.AppendLine("  name: $Service")
    [void]$builder.AppendLine("  annotations:")
    [void]$builder.AppendLine("    run.googleapis.com/ingress: all")
    [void]$builder.AppendLine("spec:")
    [void]$builder.AppendLine("  template:")
    [void]$builder.AppendLine("    metadata:")
    [void]$builder.AppendLine("      annotations:")
    [void]$builder.AppendLine("        autoscaling.knative.dev/minScale: `"$MinInstances`"")
    [void]$builder.AppendLine("    spec:")
    [void]$builder.AppendLine("      serviceAccountName: $ServiceAccountEmail")
    [void]$builder.AppendLine("      containers:")
    [void]$builder.AppendLine("        - name: proxy")
    [void]$builder.AppendLine("          image: $ProxyImage")
    [void]$builder.AppendLine("          ports:")
    [void]$builder.AppendLine("            - name: http1")
    [void]$builder.AppendLine("              containerPort: 8080")
    [void]$builder.AppendLine("          env:")
    Add-SecretEnvYaml $builder $Values @("OPS_BASIC_AUTH_USER", "OPS_BASIC_AUTH_PASSWORD") "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 256Mi")
    [void]$builder.AppendLine("        - name: app")
    [void]$builder.AppendLine("          image: $AppImage")
    [void]$builder.AppendLine("          env:")
    Add-SecretEnvYaml $builder $Values $AppEnvKeys "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"2`"")
    [void]$builder.AppendLine("              memory: 4Gi")
    [void]$builder.AppendLine("        - name: prometheus")
    [void]$builder.AppendLine("          image: $PrometheusImage")
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 512Mi")
    [void]$builder.AppendLine("        - name: pushgateway")
    [void]$builder.AppendLine("          image: prom/pushgateway:v1.8.0")
    [void]$builder.AppendLine("          args:")
    [void]$builder.AppendLine("            - --web.listen-address=0.0.0.0:9091")
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 256Mi")
    [void]$builder.AppendLine("        - name: grafana")
    [void]$builder.AppendLine("          image: $GrafanaImage")
    [void]$builder.AppendLine("          env:")
    Add-SecretEnvYaml $builder $Values @("GRAFANA_ADMIN_PASSWORD") "            "
    Add-LiteralEnvYaml $builder "GF_SECURITY_ADMIN_USER" "admin" "            "
    Add-LiteralEnvYaml $builder "GF_USERS_ALLOW_SIGN_UP" "false" "            "
    Add-LiteralEnvYaml $builder "GF_SERVER_HTTP_PORT" "3000" "            "
    Add-LiteralEnvYaml $builder "GF_SERVER_ROOT_URL" "%(protocol)s://%(domain)s/grafana/" "            "
    Add-LiteralEnvYaml $builder "GF_SERVER_SERVE_FROM_SUB_PATH" "true" "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 512Mi")
    [System.IO.File]::WriteAllText($Path, $builder.ToString())
}

function Invoke-LocalDockerBuilds {
    $registryHost = "$Region-docker.pkg.dev"
    & gcloud auth configure-docker $registryHost --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud auth configure-docker failed for $registryHost"
    }

    $builds = @(
        @{ Image = $AppImage; Dockerfile = "Dockerfile"; BuildArg = "PYTHON_BASE_IMAGE=$PythonBaseImage"; NoCache = $false },
        @{ Image = $ProxyImage; Dockerfile = "docker/cloudrun/proxy/Dockerfile"; BuildArg = "PROXY_BASE_IMAGE=$ProxyBaseImage"; NoCache = $true },
        @{ Image = $PrometheusImage; Dockerfile = "docker/cloudrun/prometheus/Dockerfile"; BuildArg = "PROMETHEUS_BASE_IMAGE=$PrometheusBaseImage"; NoCache = $false },
        @{ Image = $GrafanaImage; Dockerfile = "docker/cloudrun/grafana/Dockerfile"; BuildArg = "GRAFANA_BASE_IMAGE=$GrafanaBaseImage"; NoCache = $false }
    )

    foreach ($build in $builds) {
        $image = $build["Image"]
        $dockerfile = $build["Dockerfile"]
        $buildArg = $build["BuildArg"]
        $buildArgs = @("build", "--platform", $ImagePlatform, "--build-arg", $buildArg)
        if ($build["NoCache"]) {
            $buildArgs += "--no-cache"
        }
        $buildArgs += @("-t", $image, "-f", $dockerfile, ".")
        & docker @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "docker build failed for $image"
        }
        & docker push $image
        if ($LASTEXITCODE -ne 0) {
            throw "docker push failed for $image"
        }
    }
}

$EnvValues = Read-EnvFile $EnvFile
Set-Default $EnvValues "OPENAI_API_BASE" "https://aibe.mygreatlearning.com/openai/v1"
Set-Default $EnvValues "FIELD_EXTRACTOR_MODE" "auto"
Set-Default $EnvValues "LLMOPS_PROMPT_VERSION" "v2"
Set-Default $EnvValues "LLMOPS_SCHEMA_VERSION" "v2"
Set-Default $EnvValues "LLMOPS_TRACE_TEXT" "false"
Set-Default $EnvValues "PROMETHEUS_METRICS_PORT" "9108"
Set-Default $EnvValues "PROMETHEUS_PUSHGATEWAY_URL" "http://127.0.0.1:9091"
Set-Default $EnvValues "LLMOPS_PRICING_FILE" "/app/configs/model_pricing.yaml"
Set-Default $EnvValues "EASYOCR_MODEL_DIR" "/models/easyocr"
Set-Default $EnvValues "LAYOUT_WORKER_PYTHON" "/app/.venv/bin/python"

if (-not $DryRun) {
    Assert-Command "gcloud"
    Assert-Command "docker"
}

Assert-Inputs $EnvValues

if ([string]::IsNullOrWhiteSpace($Tag)) {
    $Tag = "deploy-{0}" -f (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
}

$ImageBase = "$Region-docker.pkg.dev/$Project/$Repo"
$AppImage = "$ImageBase/gl-doc-capstone-app:$Tag"
$ProxyImage = "$ImageBase/gl-doc-capstone-proxy:$Tag"
$PrometheusImage = "$ImageBase/gl-doc-capstone-prometheus:$Tag"
$GrafanaImage = "$ImageBase/gl-doc-capstone-grafana:$Tag"
$ServiceAccountName = "$Service-runtime"
$ServiceAccountEmail = "$ServiceAccountName@$Project.iam.gserviceaccount.com"

if ($DryRun) {
    Write-Output "Dry run OK"
    Write-Output "Project: $Project"
    Write-Output "Region: $Region"
    Write-Output "Service: $Service"
    Write-Output "Repo: $Repo"
    Write-Output "Image platform: $ImagePlatform"
    Write-Output "Setup infra: $SetupInfra"
    Write-Output "Sync secrets: $SyncSecrets"
    Write-Output "Images:"
    Write-Output "  $AppImage"
    Write-Output "  $ProxyImage"
    Write-Output "  $PrometheusImage"
    Write-Output "  $GrafanaImage"
    exit 0
}

if ($SetupInfra) {
    & gcloud services enable `
        run.googleapis.com `
        artifactregistry.googleapis.com `
        secretmanager.googleapis.com `
        iam.googleapis.com `
        --project $Project
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud services enable failed"
    }

    $repoExists = Test-GcloudEntity @("artifacts", "repositories", "describe", $Repo, "--project", $Project, "--location", $Region)
    if (-not $repoExists) {
        & gcloud artifacts repositories create $Repo `
            --project $Project `
            --location $Region `
            --repository-format docker `
            --description "gl-doc-capstone Cloud Run images"
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud artifacts repositories create failed for $Repo"
        }
    }

    $saExists = Test-GcloudEntity @("iam", "service-accounts", "describe", $ServiceAccountEmail, "--project", $Project)
    if (-not $saExists) {
        & gcloud iam service-accounts create $ServiceAccountName `
            --project $Project `
            --display-name "$Service Cloud Run runtime"
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud iam service-accounts create failed for $ServiceAccountName"
        }
    }
}

if ($SyncSecrets) {
    foreach ($key in ($AppEnvKeys + @("GRAFANA_ADMIN_PASSWORD", "OPS_BASIC_AUTH_USER", "OPS_BASIC_AUTH_PASSWORD"))) {
        Sync-Secret $EnvValues $key
    }
}

Invoke-LocalDockerBuilds

$serviceYaml = [System.IO.Path]::GetTempFileName()
try {
    Write-ServiceYaml $EnvValues $serviceYaml
    & gcloud run services replace $serviceYaml --project $Project --region $Region
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud run services replace failed for $Service"
    }
}
finally {
    Remove-Item $serviceYaml -Force -ErrorAction SilentlyContinue
}

if ($AllowUnauthenticated) {
    & gcloud run services add-iam-policy-binding $Service `
        --project $Project `
        --region $Region `
        --member allUsers `
        --role roles/run.invoker `
        --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud run services add-iam-policy-binding failed for $Service"
    }
}
else {
    & gcloud run services remove-iam-policy-binding $Service `
        --project $Project `
        --region $Region `
        --member allUsers `
        --role roles/run.invoker `
        --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud run services remove-iam-policy-binding failed for $Service"
    }
}

& gcloud run services describe $Service `
    --project $Project `
    --region $Region `
    --format "value(status.url)"
if ($LASTEXITCODE -ne 0) {
    throw "gcloud run services describe failed for $Service"
}
