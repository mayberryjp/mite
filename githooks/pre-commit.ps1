#!/usr/bin/env pwsh
# PowerShell pre-commit hook for Windows

# Use the project virtualenv's Python when present so flake8/isort/black work
# regardless of whether the venv is activated in the current shell. Falls back
# to whatever "python" is on PATH otherwise.
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $py = $venvPython
} else {
    $py = "python"
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
