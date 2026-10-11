# Copyright (C) 2026 - Joseph M Wilcox
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'scripts/forge.py') @args
exit $LASTEXITCODE
