<#
Install terminal dependencies for Windows developers.
Run from an elevated or normal PowerShell session with winget available.
Installs/checks: Azure CLI, Git, Python, Make, jq. Then installs Bicep via Azure CLI.
#>
$ErrorActionPreference = "Stop"

function Have($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

function Install-WingetPackage($id, $name) {
    if (winget list --id $id --exact | Select-String $id) {
        Write-Host "$name already installed"
    } else {
        Write-Host "Installing $name"
        winget install --exact --id $id --accept-package-agreements --accept-source-agreements
    }
}

Install-WingetPackage "Microsoft.AzureCLI" "Azure CLI"
Install-WingetPackage "Git.Git" "Git"
Install-WingetPackage "Python.Python.3.12" "Python 3"
Install-WingetPackage "GnuWin32.Make" "Make"
Install-WingetPackage "jqlang.jq" "jq"

Write-Host "Installing/upgrading Bicep through Azure CLI"
az bicep install | Out-Null
az bicep upgrade | Out-Null
az bicep version

$repoRoot = Resolve-Path "$PSScriptRoot\.."
$labDir = Join-Path $repoRoot "sample-ingestion"
Set-Location $labDir

Write-Host "Creating Python virtual environment"
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt
& .\.venv\Scripts\python.exe validate_local.py

Write-Host "Done. Next steps:"
Write-Host "1. Local exercises: cd sample-ingestion; .\.venv\Scripts\Activate.ps1; make local"
Write-Host "2. Azure: az login; make deploy RESOURCE_GROUP=rg-aisearch-private-dev LOCATION=westeurope"
