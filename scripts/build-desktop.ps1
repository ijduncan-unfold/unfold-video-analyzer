param(
    [string]$PythonPath = "",
    [string]$InnoPath = "",
    [string]$Version = "0.1.0-preview",
    [switch]$SkipDependencies,
    [switch]$SkipFrontend
)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (!$PythonPath) { $PythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $PythonPath)) { throw "Create .venv first or pass -PythonPath." }
if (!$InnoPath) {
    $compilerCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe')
    )
    $InnoPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (!$InnoPath) { throw "Install Inno Setup 6 or pass -InnoPath." }
Push-Location $repoRoot
try {
    if (!$SkipDependencies) {
        & $PythonPath -m pip install -r requirements-desktop.txt
        if ($LASTEXITCODE -ne 0) { throw "Desktop dependency installation failed." }
    }
    if (!$SkipFrontend) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    }
    & $PythonPath scripts/package-resolve.py
    if ($LASTEXITCODE -ne 0) { throw "Resolve plugin packaging failed." }
    & $PythonPath scripts/package-premiere.py
    if ($LASTEXITCODE -ne 0) { throw "Premiere plugin packaging failed." }
    & $PythonPath -m PyInstaller --noconfirm --distpath dist/desktop --workpath build/desktop packaging/desktop.spec
    if ($LASTEXITCODE -ne 0) { throw "Frozen desktop build failed." }
    $bundleDir = Join-Path $repoRoot 'dist/desktop/VideoAnalyzer'
    $smokeDir = Join-Path $repoRoot 'build/desktop/smoke-data'
    $smokeArgs = @('--smoke-test', '--no-browser', '--port', '18741', '--data-dir', ('"{0}"' -f $smokeDir))
    $smokeProcess = Start-Process -FilePath (Join-Path $bundleDir 'VideoAnalyzer.exe') -ArgumentList $smokeArgs -PassThru -WindowStyle Hidden
    if (!$smokeProcess.WaitForExit(60000)) {
        # Only the process this script started is stopped, never another engine.
        Stop-Process -Id $smokeProcess.Id -Force
        throw "Desktop smoke test timed out; inspect build/desktop/smoke-data/desktop.log."
    }
    if ($smokeProcess.ExitCode -ne 0) { throw "Desktop smoke test failed; inspect build/desktop/smoke-data/desktop.log." }
    $smokeReport = Get-Content -LiteralPath (Join-Path $smokeDir 'smoke-test.json') -Raw | ConvertFrom-Json
    if ($smokeReport.google_configured) { throw "Smoke test discovered an inherited Gemini key; investigate desktop isolation." }
    if ('resolve' -notin $smokeReport.editor_hosts) { throw "The frozen Resolve API is missing." }
    & $InnoPath ("/DAppVersion={0}" -f $Version) ("/DBundleDir={0}" -f $bundleDir) packaging/windows.iss
    if ($LASTEXITCODE -ne 0) { throw "Windows installer compilation failed." }
    & $PythonPath packaging/build_inventory.py (Join-Path $repoRoot 'dist/installers/build-environment.json')
    if ($LASTEXITCODE -ne 0) { throw "Dependency inventory failed." }
    Get-ChildItem -LiteralPath (Join-Path $repoRoot 'dist/installers') -Filter '*.exe' | Select-Object Name,Length,FullName
} finally { Pop-Location }
