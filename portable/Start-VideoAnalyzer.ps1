param([switch]$VerifyOnly, [switch]$ExtractOnly)
$ErrorActionPreference = 'Stop'

$archivePath = Join-Path $PSScriptRoot 'VideoAnalyzer-windows-x64.zip'
$checksumPath = "$archivePath.sha256"
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw 'The portable bundle is missing. Install Git LFS and run git lfs pull in this repository.'
}
if ((Get-Item -LiteralPath $archivePath).Length -lt 1024) {
    throw 'This download contains a Git LFS pointer. Install Git LFS and run git lfs pull in this repository.'
}
$expectedHash = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0]
if ($expectedHash -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid bundle checksum.' }
Write-Host 'Verifying Video Analyzer bundle...'
$actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash.ToLowerInvariant()) { throw 'The bundle checksum does not match. Download it again.' }
if ($VerifyOnly) { Write-Host 'Bundle verified.'; exit 0 }

$bundleRoot = Join-Path $env:LOCALAPPDATA 'VideoAnalyzer\portable-bundles'
$destination = Join-Path $bundleRoot $actualHash
$markerPath = Join-Path $destination '.extracted'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    if (Test-Path -LiteralPath $destination) { throw "Incomplete bundle folder: $destination. Remove that folder and retry." }
    New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
    $stagingPath = Join-Path $bundleRoot ([guid]::NewGuid().ToString('N'))
    Write-Host 'Extracting the app, FFmpeg, and camera libraries (first launch only)...'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingPath
    if (-not (Test-Path -LiteralPath (Join-Path $stagingPath 'app\VideoAnalyzer.exe') -PathType Leaf)) {
        throw 'The archive does not contain the Windows app.'
    }
    Set-Content -LiteralPath (Join-Path $stagingPath '.extracted') -Value $actualHash -Encoding Ascii
    Move-Item -LiteralPath $stagingPath -Destination $destination
}
if ($ExtractOnly) { Write-Host "Extracted to $destination"; exit 0 }
$env:VIDEO_ANALYZER_DECODER_DIR = Join-Path $destination 'camera-decoders'
$env:PATH = (Join-Path $destination 'ffmpeg') + [IO.Path]::PathSeparator + $env:PATH
Start-Process -FilePath (Join-Path $destination 'app\VideoAnalyzer.exe') -WorkingDirectory $destination
