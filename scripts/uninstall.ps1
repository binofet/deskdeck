param([string]$Python)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'install.ps1') -Action uninstall -Python $Python
