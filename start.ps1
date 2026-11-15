# Start from any directory. Keep this terminal running while using the app.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Project virtual environment missing. Follow README.md setup instructions first.'
}
Write-Host 'Open http://127.0.0.1:8000/ in your browser. Do not open index.html directly.'
& $projectPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
