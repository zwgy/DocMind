# 在 Windows 上实际执行初始化函数，不调用 Docker，也不访问真实环境文件。
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../../.."))
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) ("docmind-env-" + [Guid]::NewGuid().ToString("N"))
$previousDirectory = Get-Location
New-Item -ItemType Directory -Path $testDirectory | Out-Null

try {
    foreach ($name in @("init.ps1", "manage.ps1")) {
        $scriptPath = Join-Path $repositoryRoot "scripts/$name"
        $tokens = $null
        $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
        if ($errors.Count -ne 0) { throw "$name 语法检查失败：$errors" }
        # 只加载原脚本函数，不执行主流程中的联网和容器操作。
        foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
            Invoke-Expression $definition.Extent.Text
        }
    }
    Set-Location $testDirectory
    $credential = 'test&key|with\slash'
    [IO.File]::WriteAllText((Join-Path $testDirectory ".env"), "# before`n# SILICONFLOW_API_KEY=`n# after`n")
    Update-EnvVar "SILICONFLOW_API_KEY" $credential
    $expected = "# before`nSILICONFLOW_API_KEY=$credential`n# after`n"
    if ([IO.File]::ReadAllText((Join-Path $testDirectory ".env")) -ne $expected) {
        throw "开发凭据未在注释原位置启用"
    }
    Update-EnvVar "SILICONFLOW_API_KEY" "do-not-replace"
    if ([IO.File]::ReadAllText((Join-Path $testDirectory ".env")) -ne $expected) {
        throw "已有开发凭据被覆盖"
    }

    Copy-Item (Join-Path $repositoryRoot ".env.prod.template") ".env.prod.template"
    $script:promptCount = 0
    function Read-Host([string]$Prompt) {
        if ($Prompt -match "SILICONFLOW") { throw "生产初始化不应询问云供应商密钥" }
        $script:promptCount++
        return "y"
    }
    # 测试由本机 PowerShell 控制台运行；仅覆盖交互检测，保留原初始化函数全部写入逻辑。
    $production = (Get-Command Initialize-ProductionEnv).ScriptBlock.ToString()
    $production = $production.Replace('[Console]::IsInputRedirected', '$false')
    Invoke-Expression ("function Initialize-ProductionEnv {" + $production + "}")
    Initialize-ProductionEnv ".env.prod"
    if ($script:promptCount -ne 1) { throw "生产初始化不应要求额外输入" }
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $testDirectory ".env.prod"))) {
        if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
            if ($values.ContainsKey($Matches[1])) { throw "生产配置有重复变量" }
            $values[$Matches[1]] = $Matches[2]
        }
    }
    foreach ($key in @("JWT_SECRET_KEY", "YUXI_INSTANCE_ID", "SANDBOX_PROVISIONER_TOKEN", "POSTGRES_PASSWORD", "NEO4J_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")) {
        if (-not $values.ContainsKey($key) -or $values[$key].Length -lt 22) { throw "$key 未正确生成" }
    }
    $before = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $testDirectory ".env.prod")))
    Initialize-ProductionEnv ".env.prod"
    $after = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $testDirectory ".env.prod")))
    if ($before -ne $after -or $script:promptCount -ne 1) { throw "已有生产配置未原样保留" }
    Write-Output "PowerShell：语法、注释项原位启用、已有值保留、生产七项生成和单次确认均通过。"
} finally {
    Set-Location $previousDirectory
    $resolved = [IO.Path]::GetFullPath($testDirectory)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($temporaryRoot) -or (Split-Path $resolved -Leaf) -notlike "docmind-env-*") {
        throw "临时测试清理路径不正确"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
