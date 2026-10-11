param(
    [Parameter(Mandatory=$true)][string]$Sdk,
    [string]$Output = 'build/native/VideoAnalyzerRED.dll'
)
$ErrorActionPreference = 'Stop'
# Run from an x64 Visual Studio Developer PowerShell. No developer tools are
# needed on destination machines; the bridge links the Microsoft CRT statically.
$repo = Split-Path -Parent $PSScriptRoot
$sdkPath = (Resolve-Path -LiteralPath $Sdk).Path
$outputPath = [IO.Path]::GetFullPath((Join-Path $repo $Output))
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
$includePath = Join-Path $sdkPath 'Include'
$libraryPath = Join-Path $sdkPath 'Lib/win64/R3DSDK-2017MT.lib'
if (!(Test-Path -LiteralPath $libraryPath)) { throw 'RED SDK 9.2.1 Windows x64 developer package is required' }
& cl.exe /nologo /LD /MT /EHsc /O2 /I $includePath (Join-Path $repo 'native/red_bridge.cpp') "/Fo:$outputPath.obj" /link "/OUT:$outputPath" $libraryPath /NODEFAULTLIB:oldnames.lib
if ($LASTEXITCODE -ne 0) { throw 'RED bridge compilation failed' }
Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
Write-Host 'Pin this build hash in native_camera.py before bundling. SDK headers stay outside the repository.'
