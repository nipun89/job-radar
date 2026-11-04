# Job Radar one-step setup for Windows. From this folder, run:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Creates a virtual environment in .venv, installs the packages, creates config.yaml
# and the database, and offers to schedule a daily check.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($c in @("py -3", "python", "python3")) {
    $parts = $c -split " "; $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        & $exe @rest -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    }
}
if (-not $py) { Write-Host "Job Radar needs Python 3.10 or newer: https://www.python.org/downloads/"; exit 1 }

$parts = $py -split " "; $exe = $parts[0]; $rest = @($parts | Select-Object -Skip 1)
Write-Host "Using $(& $exe @rest --version)"
if (-not (Test-Path .venv)) { & $exe @rest -m venv .venv }
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
& .venv\Scripts\python.exe -m radar setup @args
Write-Host ""
Write-Host "Next time, double-click 'Job Radar.bat' (or run: .venv\Scripts\python -m radar serve)"
Write-Host "Just looking?  Run: .venv\Scripts\python -m radar demo"
