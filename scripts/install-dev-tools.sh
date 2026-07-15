#!/usr/bin/env bash
set -euo pipefail

# Install terminal dependencies for the Azure AI Search Bicep + Python lab.
# Supported best-effort paths:
# - Ubuntu/Debian/WSL: apt
# - macOS: Homebrew
# - Other Linux: prints exact missing commands to install manually
#
# Installs/checks: git, curl, unzip, jq, make, Python 3, pip, Azure CLI, Azure Bicep CLI.

say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

install_apt() {
  say "Installing base packages with apt"
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl apt-transport-https lsb-release gnupg git unzip jq make python3 python3-venv python3-pip
}

install_brew() {
  if ! have brew; then
    say "Homebrew is not installed. Install Homebrew first, then rerun this script."
    echo "Homebrew: https://brew.sh"
    exit 1
  fi
  say "Installing base packages with Homebrew"
  brew update
  brew install git curl unzip jq make python azure-cli || true
}

install_azure_cli_linux() {
  if have az; then
    say "Azure CLI already installed: $(az version --query azure-cli -o tsv 2>/dev/null || az version | head -1)"
    return
  fi
  if [[ -f /etc/debian_version ]]; then
    say "Installing Azure CLI using Microsoft install script"
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
  else
    say "Azure CLI not installed and automatic install is only scripted for Debian/Ubuntu here."
    echo "Install manually from Microsoft Learn, then rerun: az version"
    exit 1
  fi
}

install_bicep() {
  if ! have az; then
    say "Azure CLI is required before installing Bicep."
    exit 1
  fi
  say "Installing/upgrading Bicep through Azure CLI"
  az bicep install >/dev/null 2>&1 || true
  az bicep upgrade >/dev/null 2>&1 || true
  az bicep version
}

case "${OSTYPE:-unknown}" in
  linux*)
    if [[ -f /etc/debian_version ]]; then
      install_apt
      install_azure_cli_linux
    else
      say "Non-Debian Linux detected. Please install: git curl unzip jq make python3 python3-pip python3-venv azure-cli"
    fi
    ;;
  darwin*)
    install_brew
    ;;
  msys*|cygwin*)
    say "Windows Git Bash detected. Prefer running scripts/install-dev-tools.ps1 from PowerShell."
    ;;
  *)
    say "Unknown OS. Please install: git curl unzip jq make python3 python3-pip python3-venv azure-cli"
    ;;
esac

install_bicep

say "Creating Python virtual environment for sample-ingestion"
cd "$(dirname "$0")/../sample-ingestion"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

say "Validation"
python validate_local.py

say "Done. Next steps"
echo "1. For local exercises: cd sample-ingestion && source .venv/bin/activate && make local"
echo "2. For Azure deployment: az login && make deploy RESOURCE_GROUP=rg-aisearch-private-dev LOCATION=westeurope"
