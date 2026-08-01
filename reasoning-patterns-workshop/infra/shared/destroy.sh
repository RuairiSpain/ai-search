#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PREFIX="$(python3 -c "import json;print(json.load(open('main.parameters.json'))['parameters']['resourcePrefix']['value'])")"
RG="${PREFIX}-rg"
read -r -p "Delete resource group $RG and EVERYTHING in it? [y/N] " a
[[ "$a" == "y" ]] && az group delete -n "$RG" --yes --no-wait && echo "Deletion started."
