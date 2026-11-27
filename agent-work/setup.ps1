# Bootstrap for Windows PowerShell. Safe to re-run.
# If scripts are blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($d in "tasks\todo","tasks\doing","tasks\done","tasks\failed","repos","worktrees","logs") {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
$agents = Join-Path $HOME ".copilot\agents"
New-Item -ItemType Directory -Force -Path $agents | Out-Null
Copy-Item -Force "agents\*.agent.md" $agents
Write-Host "Installed custom agents into $agents"

$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }
& $py doctor.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nSetup incomplete: fix the FAIL items above (see SETUP.md), then re-run .\setup.ps1"
    exit 1
}
Write-Host "`nReady. Next: put a task in tasks\todo\ and run:  $py run.py once"
