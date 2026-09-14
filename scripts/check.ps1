param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [string]$NodePath = "node"
)

$ErrorActionPreference = "Stop"
$workspacePath = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param(
        [string]$Label,
        [string]$Command,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )

    Write-Host "[$Label]"
    Push-Location $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

$python = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    (Join-Path $workspacePath $PythonPath)
)
$webPath = Join-Path $workspacePath "web"

Invoke-Checked "backend coverage" $python @(
    "-m", "pytest", "--cov=x_follow_list", "--cov-branch", "--cov-report=term-missing"
) $workspacePath
Invoke-Checked "backend lint" $python @("-m", "ruff", "check", ".") $workspacePath
Invoke-Checked "backend types" $python @("-m", "mypy", "src", "tests") $workspacePath
Invoke-Checked "frontend coverage" $NodePath @(
    ".\node_modules\vitest\vitest.mjs", "run", "--coverage"
) $webPath
Invoke-Checked "frontend lint" $NodePath @(
    ".\node_modules\eslint\bin\eslint.js", "."
) $webPath
Invoke-Checked "frontend types" $NodePath @(
    ".\node_modules\typescript\bin\tsc", "-b"
) $webPath
Invoke-Checked "frontend build" $NodePath @(
    ".\node_modules\vite\bin\vite.js", "build"
) $webPath

Write-Host "All quality checks passed."
