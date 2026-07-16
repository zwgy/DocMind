param(
    [Parameter(Position = 0)]
    [string]$TargetEnvironment = "",
    [Parameter(Position = 1)]
    [string]$Action = "help",
    [Parameter(Position = 2)]
    [string]$Service = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# DocMind Docker Compose 统一管理入口；与 manage.sh 保持相同命令契约。
#
# - dev  使用 .env 和 docker-compose.yml，保留前端热更新。
# - prod 使用 .env.prod 和 docker-compose.prod.yml；首次部署会交互生成启动必需安全配置。
# - Service 是 Compose 服务名，例如 api、web、chat-iframe，不是容器名。
# - 需要 GPU OCR 等 profile=all 服务时，先设置 $env:COMPOSE_PROFILES='all'。
function Show-Usage {
    Write-Host @"
用法: .\scripts\manage.ps1 <dev|prod> <action> [service]

操作:
  init     初始化环境文件；prod 会交互生成 .env.prod 的启动必需安全配置
  deploy   校验配置、构建镜像并后台启动服务
  start    不重新构建镜像，启动已有服务
  stop     停止服务，但保留容器
  restart  重启服务
  down     删除容器和 Compose 网络，保留命名卷
  status   查看服务状态
  logs     跟随最近 200 行日志
  build    只构建镜像，不启动服务
  config   只校验环境变量和 Compose 最终配置
  help     显示本帮助

示例:
  # 初始化生产配置；首次 prod deploy 缺少 .env.prod 时也会自动执行此步骤
  .\scripts\manage.ps1 prod init

  # 开发、生产一键部署
  .\scripts\manage.ps1 dev deploy
  .\scripts\manage.ps1 prod deploy

  # 管理单个 Compose 服务
  .\scripts\manage.ps1 prod logs api

  # 启动包含 GPU OCR 的全部服务
  `$env:COMPOSE_PROFILES='all'; .\scripts\manage.ps1 prod deploy
"@
}

if ($TargetEnvironment -eq "help" -or $Action -eq "help") {
    Show-Usage
    return
}

# 生成十六进制随机值，避免密码中的 Compose 或 PowerShell 特殊字符造成解析歧义。
function New-RandomHex([int]$ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

# 写入 .env.prod 的一个值：模板中被注释的 KEY= 行会原地启用；变量不存在才追加，
# 从而保证配置文件中每个关键变量只有一个明确的最终值。
function Set-ProductionEnvValue([string]$EnvFile, [string]$Name, [string]$Value) {
    $path = (Resolve-Path $EnvFile).Path
    $lines = [System.IO.File]::ReadAllLines($path)
    $pattern = '^\s*#?\s*' + [regex]::Escape($Name) + '='
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match $pattern) {
            $lines[$index] = "$Name=$Value"
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $lines += "$Name=$Value"
    }
    [System.IO.File]::WriteAllText($path, (($lines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
}

# 生产配置只生成启动和安全必需项，密钥不写入终端。
# 模型、跨域和 GPU 等取决于真实部署拓扑，脚本只提示运维后续按需填写，避免擅自猜测。
function Initialize-ProductionEnv([string]$EnvFile) {
    if (Test-Path $EnvFile) {
        Write-Host "$EnvFile 已存在，保留现有配置。" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path ".env.template")) {
        throw ".env.template 不存在，无法创建 $EnvFile。"
    }
    if ([Console]::IsInputRedirected) {
        throw "生产配置初始化需要交互式终端，无法自动创建 $EnvFile。"
    }

    $answer = Read-Host "将创建 $EnvFile 并生成生产密码，是否继续？[y/N]"
    if ($answer -notin @("y", "Y", "yes", "YES")) {
        throw "已取消生产配置初始化。"
    }

    Copy-Item ".env.template" $EnvFile
    Set-ProductionEnvValue $EnvFile "YUXI_ENV" "production"
    Set-ProductionEnvValue $EnvFile "JWT_SECRET_KEY" (New-RandomHex 32)
    Set-ProductionEnvValue $EnvFile "YUXI_INSTANCE_ID" ("instance-" + (New-RandomHex 8))
    Set-ProductionEnvValue $EnvFile "POSTGRES_PASSWORD" (New-RandomHex 32)
    Set-ProductionEnvValue $EnvFile "NEO4J_PASSWORD" (New-RandomHex 32)
    Set-ProductionEnvValue $EnvFile "MINIO_ACCESS_KEY" ("minio-" + (New-RandomHex 12))
    Set-ProductionEnvValue $EnvFile "MINIO_SECRET_KEY" (New-RandomHex 32)

    $apiKey = Read-Host "现在填写 SILICONFLOW_API_KEY 吗？直接回车跳过"
    if (-not [string]::IsNullOrWhiteSpace($apiKey)) {
        Set-ProductionEnvValue $EnvFile "SILICONFLOW_API_KEY" $apiKey
    }

    Write-Host "✅ 已生成 $EnvFile 的启动必需安全配置（密钥不会打印到终端）。" -ForegroundColor Green
    Write-Host "部署前请按实际环境检查或补充：" -ForegroundColor Yellow
    Write-Host "  - SILICONFLOW_API_KEY 或其他模型提供商密钥（不填可启动，但无法正常调用模型）"
    Write-Host "  - YUXI_CORS_ORIGINS（存在跨域浏览器访问时）"
    Write-Host "  - CHAT_IFRAME_*（启用外部用户自助换票时）"
    Write-Host "  - 端口、GPU OCR、Sandbox、MinerU 等部署相关配置"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$previousLocation = Get-Location

try {
    Set-Location $projectRoot

    switch ($TargetEnvironment) {
        "dev" {
            $envFile = ".env"
            $composeFile = "docker-compose.yml"
        }
        "prod" {
            $envFile = ".env.prod"
            $composeFile = "docker-compose.prod.yml"
        }
        default {
            throw "Environment must be dev or prod."
        }
    }

    # init 不依赖 Docker；开发环境复用既有初始化脚本，生产环境只创建 .env.prod。
    if ($Action -eq "init") {
        if ($TargetEnvironment -eq "dev") {
            & "$PSScriptRoot\init.ps1"
            if ($LASTEXITCODE -ne 0) {
                throw "Development initialization failed."
            }
        } else {
            Initialize-ProductionEnv $envFile
        }
        return
    }

    if (-not (Test-Path $envFile)) {
        if ($TargetEnvironment -eq "dev" -and $Action -eq "deploy") {
            Write-Host "缺少 .env，正在运行现有开发环境初始化脚本..." -ForegroundColor Yellow
            & "$PSScriptRoot\init.ps1"
            if ($LASTEXITCODE -ne 0) {
                throw "Development initialization failed."
            }
        } elseif ($TargetEnvironment -eq "prod" -and $Action -eq "deploy") {
            Write-Host "缺少 .env.prod，正在初始化生产环境配置..." -ForegroundColor Yellow
            Initialize-ProductionEnv $envFile
        } else {
            throw "$envFile not found. 请先执行：.\scripts\manage.ps1 prod init"
        }
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker Engine and Docker Compose v2 are required."
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engine and Docker Compose v2 are required."
    }

    # 每个操作都固定携带当前环境文件，避免生产命令误读取开发变量。
    $composeArgs = @("compose", "--env-file", $envFile, "-f", $composeFile)
    $targetArgs = if ([string]::IsNullOrWhiteSpace($Service)) { @() } else { @($Service) }

    function Invoke-Compose([string[]]$CommandArgs) {
        # 打印命令便于复制排查，但不打印 .env 中的具体敏感值。
        $allArgs = $composeArgs + $CommandArgs
        Write-Host ("+ docker " + ($allArgs -join " ")) -ForegroundColor DarkGray
        & docker @allArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose command failed with exit code $LASTEXITCODE."
        }
    }

    function Get-DotEnvValue([string]$Name) {
        # 读取最后一个同名变量，和 Compose 对重复变量的覆盖顺序保持一致；仅用于安全校验。
        $pattern = "^" + [regex]::Escape($Name) + "=(.*)$"
        $match = Get-Content $envFile | Select-String -Pattern $pattern | Select-Object -Last 1
        if ($null -eq $match) {
            return ""
        }
        $value = $match.Matches[0].Groups[1].Value.Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            return $value.Substring(1, $value.Length - 2)
        }
        return $value
    }

    function Assert-ProductionEnv {
        # 发布前拒绝模板空值、占位符和公开默认密码；自助换票开启时强制要求白名单。
        $failed = $false
        $required = @("JWT_SECRET_KEY", "YUXI_INSTANCE_ID", "POSTGRES_PASSWORD", "NEO4J_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")
        foreach ($name in $required) {
            $value = Get-DotEnvValue $name
            if ([string]::IsNullOrWhiteSpace($value) -or $value.StartsWith("#") -or $value.StartsWith("__REPLACE_ME__")) {
                Write-Host "Error: $name must be configured in .env.prod." -ForegroundColor Red
                $failed = $true
            }
        }

        $weakValues = @{
            JWT_SECRET_KEY = "yuxi_know_secure_key"
            POSTGRES_PASSWORD = "postgres"
            NEO4J_PASSWORD = "0123456789"
            MINIO_ACCESS_KEY = "minioadmin"
            MINIO_SECRET_KEY = "minioadmin"
        }
        foreach ($name in $weakValues.Keys) {
            if ((Get-DotEnvValue $name) -eq $weakValues[$name]) {
                Write-Host "Error: $name uses the public default." -ForegroundColor Red
                $failed = $true
            }
        }

        if (@("1", "true", "yes", "on") -contains (Get-DotEnvValue "CHAT_IFRAME_AUTO_LOGIN_ENABLED").ToLowerInvariant()) {
            foreach ($name in @("CHAT_IFRAME_ALLOWED_SOURCES", "CHAT_IFRAME_ALLOWED_ORIGINS")) {
                if ([string]::IsNullOrWhiteSpace((Get-DotEnvValue $name))) {
                    Write-Host "Error: $name is required when chat-iframe auto login is enabled." -ForegroundColor Red
                    $failed = $true
                }
            }
        }

        if ($failed) {
            throw "Production environment validation failed."
        }
    }

    switch ($Action) {
        "deploy" {
            # 先解析配置，避免镜像构建或容器变更后才发现变量错误。
            if ($TargetEnvironment -eq "prod") { Assert-ProductionEnv }
            Invoke-Compose @("config", "--quiet")
            Invoke-Compose (@("up", "-d", "--build") + $targetArgs)
            Invoke-Compose @("ps")
        }
        "start" { Invoke-Compose (@("up", "-d", "--no-build") + $targetArgs) }
        "stop" { Invoke-Compose (@("stop") + $targetArgs) }
        "restart" { Invoke-Compose (@("restart") + $targetArgs) }
        "down" {
            # down 是整个 Compose 项目的销毁操作，单服务场景应使用 stop，防止误解行为。
            if (-not [string]::IsNullOrWhiteSpace($Service)) {
                throw "down does not accept a service; use stop for one service."
            }
            Invoke-Compose @("down")
        }
        "status" { Invoke-Compose (@("ps") + $targetArgs) }
        "logs" { Invoke-Compose (@("logs", "--tail", "200", "-f") + $targetArgs) }
        "build" { Invoke-Compose (@("build") + $targetArgs) }
        "config" {
            if ($TargetEnvironment -eq "prod") { Assert-ProductionEnv }
            Invoke-Compose @("config", "--quiet")
            Write-Host "Compose configuration is valid." -ForegroundColor Green
        }
        default { throw "Unsupported action: $Action" }
    }
} catch {
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Set-Location $previousLocation
}
