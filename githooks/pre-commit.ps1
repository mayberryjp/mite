#!/usr/bin/env pwsh
# PowerShell pre-commit hook for Windows

# Use the project virtualenv's Python when present so flake8/isort/black work
# regardless of whether the venv is activated in the current shell. Falls back
# to whatever "python" is on PATH otherwise.
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$py = $null
if (Test-Path $venvPython) {
    & $venvPython --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $py = $venvPython
    } else {
        Write-Host "Project .venv Python is broken; falling back to PATH Python."
    }
}
if (-not $py) {
    foreach ($candidate in @("python", "python3", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                $py = $candidate
                break
            }
        }
    }
}
if (-not $py) {
    Write-Host "No working Python interpreter found on PATH. Commit aborted."
    exit 1
}

Write-Host "Running flake8..."
& $py -m flake8 --ignore=E501,W503,W203,E203,E722,W191 --exclude=.venv,tests src
if ($LASTEXITCODE -ne 0) {
   Write-Host "flake8 failed. Commit aborted."
   exit 1
}

Write-Host "Running isort..."
& $py -m isort src
if ($LASTEXITCODE -ne 0) {
    Write-Host "isort failed. Commit aborted."
    exit 1
}

Write-Host "Running black..."
& $py -m black src
if ($LASTEXITCODE -ne 0) {
    Write-Host "black formatting failed. Commit aborted."
    exit 1
}

Write-Host "All checks passed. Proceeding with commit."
exit 0
