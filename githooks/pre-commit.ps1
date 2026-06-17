#!/usr/bin/env pwsh
# PowerShell pre-commit hook for Windows

Write-Host "Running flake8..."
flake8 --ignore=E501,W503,W203,E203,E722,W191 --exclude=.venv,tests src
if ($LASTEXITCODE -ne 0) {
   Write-Host "flake8 failed. Commit aborted."
   exit 1
}

Write-Host "Running isort..."
isort src
if ($LASTEXITCODE -ne 0) {
    Write-Host "isort failed. Commit aborted."
    exit 1
}

Write-Host "Running black..."
black src
if ($LASTEXITCODE -ne 0) {
    Write-Host "black formatting failed. Commit aborted."
    exit 1
}

Write-Host "All checks passed. Proceeding with commit."
exit 0
