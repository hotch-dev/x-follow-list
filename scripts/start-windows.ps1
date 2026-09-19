param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [string]$NodePath = "node"
)

$ErrorActionPreference = "Stop"
$workspacePath = Split-Path -Parent $PSScriptRoot
$webPath = Join-Path $workspacePath "web"
$dataPath = Join-Path $workspacePath "data"
$runPath = Join-Path $dataPath "run"
$appOrigin = "http://127.0.0.1:5173"
$apiOrigin = "http://127.0.0.1:8000"
$startedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Resolve-WorkspacePath {
    param([string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $workspacePath $Path))
}

function Start-HiddenService {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )

    $stdoutPath = Join-Path $runPath "$Name.stdout.log"
    $stderrPath = Join-Path $runPath "$Name.stderr.log"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    $startedProcesses.Add($process)
    Set-Content -LiteralPath (Join-Path $runPath "$Name.pid") -Value $process.Id
    Write-Host "[$Name] started (PID $($process.Id))"
}

function Wait-HttpReady {
    param(
        [string]$Name,
        [string]$Uri,
        [int]$TimeoutSeconds = 30
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host "[$Name] ready"
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)

    throw "$Name did not become ready at $Uri within $TimeoutSeconds seconds"
}

try {
    $python = Resolve-WorkspacePath $PythonPath
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python runtime not found: $python. Create .venv and install the project first."
    }

    $nodeCommand = Get-Command $NodePath -ErrorAction Stop
    $node = $nodeCommand.Source
    $apiExecutable = Join-Path (Split-Path -Parent $python) "x-follow-list-api.exe"
    $workerExecutable = Join-Path (Split-Path -Parent $python) "x-follow-list-worker.exe"
    $viteScript = Join-Path $webPath "node_modules\vite\bin\vite.js"
    foreach ($requiredPath in ($apiExecutable, $workerExecutable, $viteScript)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Required runtime file not found: $requiredPath. Install backend and web dependencies first."
        }
    }

    New-Item -ItemType Directory -Path $runPath -Force | Out-Null
    $env:X_FOLLOW_LIST_ENV = "development"
    $env:X_FOLLOW_LIST_DATA_DIR = [System.IO.Path]::GetFullPath($dataPath)
    $env:X_FOLLOW_LIST_APP_ORIGIN = $appOrigin

    Write-Host "[migration] upgrading database to head"
    Push-Location $workspacePath
    try {
        & $python "-m" "alembic" "upgrade" "head"
        if ($LASTEXITCODE -ne 0) {
            throw "Database migration failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    Start-HiddenService `
        -Name "api" `
        -FilePath $apiExecutable `
        -ArgumentList @() `
        -WorkingDirectory $workspacePath
    Wait-HttpReady "api" "$apiOrigin/health/ready"

    Start-HiddenService `
        -Name "worker" `
        -FilePath $workerExecutable `
        -ArgumentList @() `
        -WorkingDirectory $workspacePath
    Start-HiddenService `
        -Name "web" `
        -FilePath $node `
        -ArgumentList @($viteScript) `
        -WorkingDirectory $webPath
    Wait-HttpReady "web" $appOrigin

    $bootstrapTokenPath = Join-Path $dataPath "bootstrap-token"
    Write-Host ""
    Write-Host "X Follow List is running at $appOrigin"
    Write-Host "Logs and PID files: $runPath"
    if (Test-Path -LiteralPath $bootstrapTokenPath -PathType Leaf) {
        Write-Host "First run: bootstrap token is at $bootstrapTokenPath"
        Write-Host "Create the OWNER with Origin $appOrigin, then sign in."
    }
    else {
        Write-Host "No bootstrap-token file is present; sign in with the existing OWNER account."
    }
    Write-Host "If no browser provider is listed, create one DIRECT_CHROME config after signing in."
}
catch {
    foreach ($process in $startedProcesses) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}
