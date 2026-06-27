<# PowerShell deploy helper that builds, pushes, and provisions per-service Cloud Run stack. #>

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
    [string]$PrometheusBaseImage = "prom/prometheus:v2.54.1",
    [string]$GrafanaBaseImage = "grafana/grafana-oss:11.2.0",
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$AppSecretEnvKeys = @(
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "FIELD_EXTRACTOR_MODE",
    "LLMOPS_PROMPT_VERSION",
    "LLMOPS_SCHEMA_VERSION",
    "LLMOPS_TRACE_TEXT",
    "PROMETHEUS_METRICS_PORT",
    "LLMOPS_PRICING_FILE",
    "EASYOCR_MODEL_DIR",
    "QWEN_MODEL_DIR",
    "LAYOUT_WORKER_PYTHON"
)

$RequiredEnvKeys = @(
    "OPENAI_API_KEY",
    "GRAFANA_ADMIN_PASSWORD"
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

function Get-HostFromUrl {
    param([string]$Url)
    $uri = [System.Uri]$Url
    return $uri.Host
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

function Write-PushgatewayServiceYaml {
    param([string]$Path)
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.AppendLine("apiVersion: serving.knative.dev/v1")
    [void]$builder.AppendLine("kind: Service")
    [void]$builder.AppendLine("metadata:")
    [void]$builder.AppendLine("  name: $PushgatewayService")
    [void]$builder.AppendLine("  annotations:")
    [void]$builder.AppendLine("    run.googleapis.com/ingress: all")
    [void]$builder.AppendLine("spec:")
    [void]$builder.AppendLine("  template:")
    [void]$builder.AppendLine("    metadata:")
    [void]$builder.AppendLine("      annotations:")
    [void]$builder.AppendLine("        autoscaling.knative.dev/minScale: `"$MinInstances`"")
    [void]$builder.AppendLine("        run.googleapis.com/cpu-throttling: `"false`"")
    [void]$builder.AppendLine("    spec:")
    [void]$builder.AppendLine("      serviceAccountName: $ServiceAccountEmail")
    [void]$builder.AppendLine("      containers:")
    [void]$builder.AppendLine("        - name: pushgateway")
    [void]$builder.AppendLine("          image: prom/pushgateway:v1.8.0")
    [void]$builder.AppendLine("          ports:")
    [void]$builder.AppendLine("            - name: http1")
    [void]$builder.AppendLine("              containerPort: 9091")
    [void]$builder.AppendLine("          args:")
    [void]$builder.AppendLine("            - --web.listen-address=0.0.0.0:9091")
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 512Mi")
    [System.IO.File]::WriteAllText($Path, $builder.ToString())
}

function Write-AppServiceYaml {
    param(
        [hashtable]$Values,
        [string]$PushgatewayUrl,
        [string]$Path
    )
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.AppendLine("apiVersion: serving.knative.dev/v1")
    [void]$builder.AppendLine("kind: Service")
    [void]$builder.AppendLine("metadata:")
    [void]$builder.AppendLine("  name: $AppService")
    [void]$builder.AppendLine("  annotations:")
    [void]$builder.AppendLine("    run.googleapis.com/ingress: all")
    [void]$builder.AppendLine("spec:")
    [void]$builder.AppendLine("  template:")
    [void]$builder.AppendLine("    metadata:")
    [void]$builder.AppendLine("      annotations:")
    [void]$builder.AppendLine("        autoscaling.knative.dev/minScale: `"$MinInstances`"")
    [void]$builder.AppendLine("        run.googleapis.com/cpu-throttling: `"false`"")
    [void]$builder.AppendLine("    spec:")
    [void]$builder.AppendLine("      serviceAccountName: $ServiceAccountEmail")
    [void]$builder.AppendLine("      containers:")
    [void]$builder.AppendLine("        - name: app")
    [void]$builder.AppendLine("          image: $AppImage")
    [void]$builder.AppendLine("          ports:")
    [void]$builder.AppendLine("            - name: http1")
    [void]$builder.AppendLine("              containerPort: 8501")
    [void]$builder.AppendLine("          env:")
    Add-SecretEnvYaml $builder $Values $AppSecretEnvKeys "            "
    Add-LiteralEnvYaml $builder "PROMETHEUS_PUSHGATEWAY_URL" $PushgatewayUrl "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"2`"")
    [void]$builder.AppendLine("              memory: 4Gi")
    [System.IO.File]::WriteAllText($Path, $builder.ToString())
}

function Write-PrometheusServiceYaml {
    param(
        [string]$AppMetricsHost,
        [string]$PushgatewayHost,
        [string]$Path
    )
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.AppendLine("apiVersion: serving.knative.dev/v1")
    [void]$builder.AppendLine("kind: Service")
    [void]$builder.AppendLine("metadata:")
    [void]$builder.AppendLine("  name: $PrometheusService")
    [void]$builder.AppendLine("  annotations:")
    [void]$builder.AppendLine("    run.googleapis.com/ingress: all")
    [void]$builder.AppendLine("spec:")
    [void]$builder.AppendLine("  template:")
    [void]$builder.AppendLine("    metadata:")
    [void]$builder.AppendLine("      annotations:")
    [void]$builder.AppendLine("        autoscaling.knative.dev/minScale: `"$MinInstances`"")
    [void]$builder.AppendLine("        run.googleapis.com/cpu-throttling: `"false`"")
    [void]$builder.AppendLine("    spec:")
    [void]$builder.AppendLine("      serviceAccountName: $ServiceAccountEmail")
    [void]$builder.AppendLine("      containers:")
    [void]$builder.AppendLine("        - name: prometheus")
    [void]$builder.AppendLine("          image: $PrometheusImage")
    [void]$builder.AppendLine("          ports:")
    [void]$builder.AppendLine("            - name: http1")
    [void]$builder.AppendLine("              containerPort: 9090")
    [void]$builder.AppendLine("          env:")
    Add-LiteralEnvYaml $builder "APP_METRICS_HOST" $AppMetricsHost "            "
    Add-LiteralEnvYaml $builder "PUSHGATEWAY_HOST" $PushgatewayHost "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 512Mi")
    [System.IO.File]::WriteAllText($Path, $builder.ToString())
}

function Write-GrafanaServiceYaml {
    param(
        [hashtable]$Values,
        [string]$PrometheusUrl,
        [string]$Path
    )
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.AppendLine("apiVersion: serving.knative.dev/v1")
    [void]$builder.AppendLine("kind: Service")
    [void]$builder.AppendLine("metadata:")
    [void]$builder.AppendLine("  name: $GrafanaService")
    [void]$builder.AppendLine("  annotations:")
    [void]$builder.AppendLine("    run.googleapis.com/ingress: all")
    [void]$builder.AppendLine("spec:")
    [void]$builder.AppendLine("  template:")
    [void]$builder.AppendLine("    metadata:")
    [void]$builder.AppendLine("      annotations:")
    [void]$builder.AppendLine("        autoscaling.knative.dev/minScale: `"$MinInstances`"")
    [void]$builder.AppendLine("        run.googleapis.com/cpu-throttling: `"false`"")
    [void]$builder.AppendLine("    spec:")
    [void]$builder.AppendLine("      serviceAccountName: $ServiceAccountEmail")
    [void]$builder.AppendLine("      containers:")
    [void]$builder.AppendLine("        - name: grafana")
    [void]$builder.AppendLine("          image: $GrafanaImage")
    [void]$builder.AppendLine("          ports:")
    [void]$builder.AppendLine("            - name: http1")
    [void]$builder.AppendLine("              containerPort: 3000")
    [void]$builder.AppendLine("          env:")
    Add-SecretEnvYaml $builder $Values @("GRAFANA_ADMIN_PASSWORD") "            "
    Add-LiteralEnvYaml $builder "GF_SECURITY_ADMIN_USER" "admin" "            "
    Add-LiteralEnvYaml $builder "GF_USERS_ALLOW_SIGN_UP" "false" "            "
    Add-LiteralEnvYaml $builder "GF_SERVER_HTTP_PORT" "3000" "            "
    Add-LiteralEnvYaml $builder "GF_PROMETHEUS_URL" $PrometheusUrl "            "
    [void]$builder.AppendLine("          resources:")
    [void]$builder.AppendLine("            limits:")
    [void]$builder.AppendLine("              cpu: `"1`"")
    [void]$builder.AppendLine("              memory: 512Mi")
    [System.IO.File]::WriteAllText($Path, $builder.ToString())
}

function Invoke-LocalDockerBuilds {
    $registryHost = "$Region-docker.pkg.dev"
    & gcloud auth configure-docker $registryHost --quiet *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud auth configure-docker failed for $registryHost"
    }

    $builds = @(
        @{ Image = $AppImage; Dockerfile = "Dockerfile"; BuildArg = "PYTHON_BASE_IMAGE=$PythonBaseImage" },
        @{ Image = $PrometheusImage; Dockerfile = "docker/cloudrun/prometheus/Dockerfile"; BuildArg = "PROMETHEUS_BASE_IMAGE=$PrometheusBaseImage" },
        @{ Image = $GrafanaImage; Dockerfile = "docker/cloudrun/grafana/Dockerfile"; BuildArg = "GRAFANA_BASE_IMAGE=$GrafanaBaseImage" }
    )

    foreach ($build in $builds) {
        $image = $build["Image"]
        $dockerfile = $build["Dockerfile"]
        $buildArg = $build["BuildArg"]
        & docker build --platform $ImagePlatform --build-arg $buildArg -t $image -f $dockerfile . *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "docker build failed for $image"
        }
        & docker push $image *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "docker push failed for $image"
        }
    }
}

function Set-ServicePublicAccess {
    param([string]$ServiceName)
    if ($AllowUnauthenticated) {
        & gcloud run services add-iam-policy-binding $ServiceName `
            --project $Project `
            --region $Region `
            --member allUsers `
            --role roles/run.invoker `
            --quiet *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud run services add-iam-policy-binding failed for $ServiceName"
        }
    }
    else {
        & gcloud run services remove-iam-policy-binding $ServiceName `
            --project $Project `
            --region $Region `
            --member allUsers `
            --role roles/run.invoker `
            --quiet *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "gcloud run services remove-iam-policy-binding failed for $ServiceName"
        }
    }
}

function Get-ServiceUrl {
    param([string]$ServiceName)
    $output = & gcloud run services describe $ServiceName `
        --project $Project `
        --region $Region `
        --format "value(status.url)"
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud run services describe failed for $ServiceName"
    }
    return ($output | Out-String).Trim()
}

function Deploy-ServiceYaml {
    param(
        [string]$ServiceName,
        [string]$YamlPath
    )
    & gcloud run services replace $YamlPath --project $Project --region $Region *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud run services replace failed for $ServiceName"
    }
    Set-ServicePublicAccess $ServiceName
    return Get-ServiceUrl $ServiceName
}

$EnvValues = Read-EnvFile $EnvFile
Set-Default $EnvValues "OPENAI_API_BASE" "https://aibe.mygreatlearning.com/openai/v1"
Set-Default $EnvValues "FIELD_EXTRACTOR_MODE" "auto"
Set-Default $EnvValues "LLMOPS_PROMPT_VERSION" "v2"
Set-Default $EnvValues "LLMOPS_SCHEMA_VERSION" "v2"
Set-Default $EnvValues "LLMOPS_TRACE_TEXT" "false"
Set-Default $EnvValues "PROMETHEUS_METRICS_PORT" "9108"
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
$PrometheusImage = "$ImageBase/gl-doc-capstone-prometheus:$Tag"
$GrafanaImage = "$ImageBase/gl-doc-capstone-grafana:$Tag"
$AppService = "$Service-app"
$PrometheusService = "$Service-prometheus"
$GrafanaService = "$Service-grafana"
$PushgatewayService = "$Service-pushgateway"
$ServiceAccountName = "$Service-runtime"
$ServiceAccountEmail = "$ServiceAccountName@$Project.iam.gserviceaccount.com"

if ($DryRun) {
    Write-Output "Dry run OK"
    Write-Output "Project: $Project"
    Write-Output "Region: $Region"
    Write-Output "Service prefix: $Service"
    Write-Output "Repo: $Repo"
    Write-Output "Image platform: $ImagePlatform"
    Write-Output "Setup infra: $SetupInfra"
    Write-Output "Sync secrets: $SyncSecrets"
    Write-Output "App service: $AppService"
    Write-Output "Grafana service: $GrafanaService"
    Write-Output "Prometheus service: $PrometheusService"
    Write-Output "Pushgateway service: $PushgatewayService"
    Write-Output "App image: $AppImage"
    Write-Output "Prometheus image: $PrometheusImage"
    Write-Output "Grafana image: $GrafanaImage"
    Write-Output "Pushgateway image: prom/pushgateway:v1.8.0"
    Write-Output "Pushgateway target URL: resolved after deploy"
    Write-Output "Prometheus targets: app /metrics + pushgateway /metrics"
    Write-Output "Grafana target: direct Prometheus service URL"
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
    foreach ($key in ($AppSecretEnvKeys + @("GRAFANA_ADMIN_PASSWORD"))) {
        Sync-Secret $EnvValues $key
    }
}

Invoke-LocalDockerBuilds

$pushgatewayYaml = [System.IO.Path]::GetTempFileName()
$appYaml = [System.IO.Path]::GetTempFileName()
$prometheusYaml = [System.IO.Path]::GetTempFileName()
$grafanaYaml = [System.IO.Path]::GetTempFileName()

try {
    Write-PushgatewayServiceYaml $pushgatewayYaml
    $PushgatewayUrl = Deploy-ServiceYaml $PushgatewayService $pushgatewayYaml

    Write-AppServiceYaml $EnvValues $PushgatewayUrl $appYaml
    $AppUrl = Deploy-ServiceYaml $AppService $appYaml

    $AppMetricsHost = Get-HostFromUrl $AppUrl
    $PushgatewayHost = Get-HostFromUrl $PushgatewayUrl
    Write-PrometheusServiceYaml $AppMetricsHost $PushgatewayHost $prometheusYaml
    $PrometheusUrl = Deploy-ServiceYaml $PrometheusService $prometheusYaml

    Write-GrafanaServiceYaml $EnvValues $PrometheusUrl $grafanaYaml
    $GrafanaUrl = Deploy-ServiceYaml $GrafanaService $grafanaYaml
}
finally {
    Remove-Item $pushgatewayYaml, $appYaml, $prometheusYaml, $grafanaYaml -Force -ErrorAction SilentlyContinue
}

Write-Output "App URL: $AppUrl"
Write-Output "Grafana URL: $GrafanaUrl"
Write-Output "Prometheus URL: $PrometheusUrl"
Write-Output "Pushgateway URL: $PushgatewayUrl"
