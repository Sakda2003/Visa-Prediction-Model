param([switch]$ExternalMongo,[switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$visaRoot = $PSScriptRoot
$visaPython = Join-Path $visaRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $visaPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
if (-not $SkipInstall) {
    & $visaPython -m pip install -r backend\requirements.txt -r ai_service\requirements-local.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (-not (Test-Path -LiteralPath 'frontend\node_modules')) {
        npm --prefix frontend ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
if ($ExternalMongo) { & $visaPython scripts\prepare_local.py --external-mongo }
else { & $visaPython scripts\prepare_local.py }
if ($LASTEXITCODE -ne 0) { throw 'Local runtime preparation failed.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend before using -SkipInstall.' }
$visaOwnedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
function Test-VisaPort([int]$Port) { return [bool](Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
try {
    if (-not $ExternalMongo -and -not (Test-VisaPort 27018)) {
        $visaDbPath = Join-Path $visaRoot '.runtime\db'
        $visaDbLog = Join-Path $visaRoot '.runtime\mongo.log'
        $visaMongo = Start-Process -FilePath (Join-Path $visaRoot '.runtime\mongodb\mongod.exe') -WorkingDirectory $visaRoot -ArgumentList @('--dbpath',('"'+$visaDbPath+'"'),'--bind_ip','127.0.0.1','--port','27018','--logpath',('"'+$visaDbLog+'"')) -WindowStyle Hidden -PassThru
        $visaOwnedProcesses.Add($visaMongo)
    }
    $env:OCR_PRIMARY_ENGINE = 'rapidocr'
    $env:OCR_TESSERACT_FALLBACK = 'false'
    if (-not (Test-VisaPort 8002)) {
        $visaOcr = Start-Process -FilePath $visaPython -WorkingDirectory $visaRoot -ArgumentList @('-m','uvicorn','app.main:app','--app-dir','ai_service','--host','127.0.0.1','--port','8002','--no-access-log') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $visaRoot '.runtime\ocr-out.log') -RedirectStandardError (Join-Path $visaRoot '.runtime\ocr-error.log') -PassThru
        $visaOwnedProcesses.Add($visaOcr)
    }
    if (-not (Test-VisaPort 8003)) {
        $visaApi = Start-Process -FilePath $visaPython -WorkingDirectory $visaRoot -ArgumentList @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8003','--no-access-log') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $visaRoot '.runtime\api-out.log') -RedirectStandardError (Join-Path $visaRoot '.runtime\api-error.log') -PassThru
        $visaOwnedProcesses.Add($visaApi)
    }
    $visaReady = $false
    for ($visaAttempt = 0; $visaAttempt -lt 30; $visaAttempt++) {
        try { $visaResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/api/ready' -TimeoutSec 2; if ($visaResponse.success) { $visaReady = $true; break } } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $visaReady) { throw 'The API is not ready. Check .runtime\api-error.log and the MongoDB connection in .env.' }
    Write-Host 'Visa workspace is ready: http://127.0.0.1:8003'
    Write-Host 'On a new empty local installation, choose your first administrator in the browser.'
    Write-Host 'Keep this terminal open. Ctrl+C stops only services started by this launcher.'
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    foreach ($visaProcess in $visaOwnedProcesses) {
        if (-not $visaProcess.HasExited) { Stop-Process -Id $visaProcess.Id -ErrorAction SilentlyContinue }
    }
}
