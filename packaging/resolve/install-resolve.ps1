# Installs only public plugin files and the matching bridge from your own Resolve SDK.
[CmdletBinding()]
param(
    [string]$PluginPath = (Join-Path $PSScriptRoot 'VideoAnalyzer'),
    [string]$DeveloperPath,
    [string]$Destination,
    [switch]$NoUI,
    [switch]$ElevationAttempted
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$pluginId = 'com.ijduncan.videoanalyzer'
$publicFiles = @('manifest.xml', 'package.json', 'main.js', 'preload.js', 'host.js',
    'service.js', 'panel.js', 'controller.js', 'index.html', 'styles.css')

function Show-Result([string]$Message, [bool]$Failed) {
    if ($NoUI) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $icon = if ($Failed) { 'Error' } else { 'Information' }
        [void][System.Windows.Forms.MessageBox]::Show($Message, 'Video Analyzer for Resolve', 'OK', $icon)
    } catch { Write-Warning 'The installer message window could not be opened.' }
}

function Get-SafePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\r\n"]') {
        throw 'The installer requires a valid filesystem path.'
    }
    $full = [System.IO.Path]::GetFullPath($Value)
    if ($full.StartsWith('\\')) { throw 'Use local filesystem paths for the plugin and SDK.' }
    return $full.TrimEnd('\', '/')
}

function Assert-NoReparse([string]$Path) {
    $cursor = $Path
    while ($cursor) {
        # Get-Item also sees dangling links that Test-Path reports as absent.
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -ne $item) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Symbolic links and reparse points are not accepted for installation paths.'
            }
        }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName
    }
}

function Read-PluginId([string]$Manifest) {
    Assert-NoReparse $Manifest
    $settings = New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver = $null
    $reader = [System.Xml.XmlReader]::Create($Manifest, $settings)
    try {
        $xml = New-Object System.Xml.XmlDocument
        $xml.XmlResolver = $null
        $xml.Load($reader)
        $node = $xml.SelectSingleNode('/BlackmagicDesign/Plugin/Id')
        if ($null -eq $node) { return '' }
        return $node.InnerText
    } finally { $reader.Close() }
}

function Assert-X64Bridge([string]$Path) {
    Assert-NoReparse $Path
    $stream = [System.IO.File]::OpenRead($Path)
    $reader = New-Object System.IO.BinaryReader($stream)
    try {
        if ($stream.Length -lt 70 -or $reader.ReadUInt16() -ne 0x5a4d) {
            throw 'The Resolve SDK bridge is not a Windows x64 PE module.'
        }
        $stream.Position = 0x3c
        $offset = $reader.ReadUInt32()
        if ($offset -lt 64 -or $offset -gt ($stream.Length - 6)) {
            throw 'The Resolve SDK bridge has an invalid PE header.'
        }
        $stream.Position = $offset
        if ($reader.ReadUInt32() -ne 0x00004550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw 'Install the Windows x64 Resolve SDK; the native bridge must be AMD64.'
        }
    } finally { $reader.Close() }
}

function Assert-NoResolve {
    if (@(Get-Process -Name Resolve -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'Close DaVinci Resolve before installing or updating Video Analyzer, then retry.'
    }
}

function Test-OwnDestination([string]$Path) {
    Assert-NoReparse $Path
    if (Test-Path -LiteralPath $Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw 'The plugin destination is not a directory.'
        }
        $manifest = Join-Path $Path 'manifest.xml'
        if (-not (Test-Path -LiteralPath $manifest -PathType Leaf) -or
            (Read-PluginId $manifest) -cne $pluginId) {
            throw 'The existing destination does not identify itself as the Video Analyzer plugin. It was not changed.'
        }
    }
}

function Invoke-Install([string]$Source, [string]$Target, [string]$Bridge) {
    Assert-NoResolve
    Test-OwnDestination $Target
    $createdDestination = -not (Test-Path -LiteralPath $Target)
    $completed = $false
    [void][System.IO.Directory]::CreateDirectory($Target)
    $temporaryFiles = New-Object 'System.Collections.Generic.List[string]'
    $staged = @{}
    try {
        # Stage every file first. No source folders or unknown destination files are copied or deleted.
        foreach ($name in @($publicFiles) + @('WorkflowIntegration.node')) {
            $sourceFile = if ($name -eq 'WorkflowIntegration.node') { $Bridge } else { Join-Path $Source $name }
            $targetFile = Join-Path $Target $name
            Assert-NoReparse $sourceFile
            Assert-NoReparse $targetFile
            if ((Test-Path -LiteralPath $targetFile) -and
                -not (Test-Path -LiteralPath $targetFile -PathType Leaf)) {
                throw "The plugin destination has a directory where $name should be a file. It was not overwritten."
            }
            $temporary = Join-Path $Target ('.video-analyzer-' + [Guid]::NewGuid().ToString('N') + '.tmp')
            $temporaryFiles.Add($temporary)
            Copy-Item -LiteralPath $sourceFile -Destination $temporary
            $staged[$name] = $temporary
        }
        Assert-NoResolve
        foreach ($name in @($publicFiles) + @('WorkflowIntegration.node')) {
            $targetFile = Join-Path $Target $name
            Assert-NoReparse $targetFile
            Move-Item -LiteralPath $staged[$name] -Destination $targetFile -Force
        }
        $completed = $true
    } finally {
        foreach ($temporary in $temporaryFiles) {
            if (Test-Path -LiteralPath $temporary -PathType Leaf) {
                Remove-Item -LiteralPath $temporary -Force
            }
        }
        # Remove only our newly created empty directory, so an elevated retry
        # is not blocked by an unidentifiable empty plugin folder. Never recurse.
        if ($createdDestination -and -not $completed -and (Test-Path -LiteralPath $Target -PathType Container)) {
            Assert-NoReparse $Target
            if ([System.IO.Directory]::GetFileSystemEntries($Target).Length -eq 0) {
                [System.IO.Directory]::Delete($Target, $false)
            }
        }
    }
}

try {
    if ($env:OS -ne 'Windows_NT') { throw 'This installer runs on Windows. Use Install Resolve on Mac.command on Apple Silicon.' }
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    if ($architecture -ne 'X64') { throw 'This preview requires Windows x64 and the matching x64 DaVinci Resolve Studio SDK.' }
    Assert-NoResolve
    $PluginPath = Get-SafePath $PluginPath
    Assert-NoReparse $PluginPath
    if (-not (Test-Path -LiteralPath $PluginPath -PathType Container)) {
        throw 'The public VideoAnalyzer plugin folder is missing. Extract the entire integration ZIP, then run the installer.'
    }
    foreach ($name in $publicFiles) {
        $file = Join-Path $PluginPath $name
        Assert-NoReparse $file
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "The public plugin file $name is missing." }
    }
    if ((Read-PluginId (Join-Path $PluginPath 'manifest.xml')) -cne $pluginId) {
        throw 'The source manifest is not the Video Analyzer Resolve plugin.'
    }
    if ([string]::IsNullOrWhiteSpace($DeveloperPath)) {
        $DeveloperPath = Join-Path $env:PROGRAMDATA 'Blackmagic Design\DaVinci Resolve\Support\Developer'
    }
    $DeveloperPath = Get-SafePath $DeveloperPath
    Assert-NoReparse $DeveloperPath
    $bridge = $null
    foreach ($relative in @('Workflow Integrations\Examples\SamplePromisePlugin\WorkflowIntegration.node',
                           'Workflow Integrations\Examples\SamplePlugin\WorkflowIntegration.node')) {
        $candidate = Join-Path $DeveloperPath $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $bridge = $candidate; break }
    }
    if ($null -eq $bridge) {
        throw 'The Resolve SDK bridge was not found. Install Resolve Studio 20.1 or later, or provide -DeveloperPath pointing to its Developer folder. The bridge is copied from your own installation and is not included in this download.'
    }
    Assert-X64Bridge $bridge
    $defaultDestination = [string]::IsNullOrWhiteSpace($Destination)
    if ($defaultDestination) {
        $Destination = Join-Path $env:PROGRAMDATA ('Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\' + $pluginId)
    }
    $Destination = Get-SafePath $Destination
    $root = [System.IO.Path]::GetPathRoot($Destination).TrimEnd('\', '/')
    if ($Destination -eq $root -or $Destination -eq $PluginPath -or
        $PluginPath.StartsWith($Destination + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $Destination.StartsWith($PluginPath + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $DeveloperPath.StartsWith($Destination + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $Destination -eq $DeveloperPath -or
        $Destination.StartsWith($DeveloperPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Use a separate plugin destination; source and SDK directories cannot be overwritten.'
    }
    Test-OwnDestination $Destination
    try { Invoke-Install $PluginPath $Destination $bridge }
    catch [System.UnauthorizedAccessException] {
        if ($NoUI -or $ElevationAttempted -or -not $defaultDestination) {
            throw 'The plugin folder is not writable. Run the Windows installer as Administrator after closing Resolve.'
        }
        # Only our already validated standard destination may request elevation.
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
            '-PluginPath', ('"' + $PluginPath + '"'), '-DeveloperPath', ('"' + $DeveloperPath + '"'), '-ElevationAttempted')
        $child = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        exit $child.ExitCode
    }
    $message = "Video Analyzer was installed for Resolve.`n`nOpen DaVinci Resolve Studio, then choose Workspace > Workflow Integrations > Video Analyzer.`n`nStart the Video Analyzer companion and paste its editor pairing token into the plugin. Original media and your analysis library were not changed."
    Write-Output $message
    Show-Result $message $false
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Error $message -ErrorAction Continue
    Show-Result $message $true
    exit 1
}
