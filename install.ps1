<#
.SYNOPSIS
    Install Holo-Blender into Blender, or build a .zip to hand to someone.

.DESCRIPTION
    By default this links the extension into Blender's user extensions
    folder with a directory junction, so the extension runs from this
    repository and an edit takes effect on the next Blender restart with
    nothing to copy. A junction needs no administrator rights, unlike a
    symbolic link.

    Blender keeps extensions in a versioned folder, for example
    %APPDATA%\Blender Foundation\Blender\5.1\extensions\user_default, and
    does not create it until something does. The version is found by asking
    Blender itself rather than by guessing.

    -Build instead produces a .zip next to the repository, which is what
    someone else installs through Preferences, Get Extensions, Install from
    Disk. That is the path to use for anyone who is not editing the code.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Build
    .\install.ps1 -Uninstall
    .\install.ps1 -Blender "D:\Blender\blender.exe"
#>

[CmdletBinding()]
param(
    [string]$Blender,
    [switch]$Build,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$source = Join-Path $root "blender_extension\holo_blender"
$id = "holo_blender"

function Find-Blender {
    if ($Blender) {
        if (-not (Test-Path $Blender)) { throw "No blender.exe at $Blender" }
        return $Blender
    }
    $candidates = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $dir = Join-Path $base "Blender Foundation"
        if (-not (Test-Path $dir)) { continue }
        $candidates += Get-ChildItem -Path $dir -Filter "Blender*" -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "blender.exe" }
    }
    # @() matters: a single match would otherwise be a string, and indexing
    # a string hands back its first character.
    $found = @($candidates | Where-Object { Test-Path $_ } | Sort-Object -Descending)
    if ($found.Count -eq 0) {
        throw "Could not find Blender. Pass -Blender with the path to blender.exe."
    }
    return $found[0]
}

$exe = Find-Blender
Write-Host "Blender: $exe"

if ($Build) {
    $out = Join-Path $root "dist"
    if (-not (Test-Path $out)) { New-Item -ItemType Directory $out | Out-Null }
    # Blender builds the zip itself, so the manifest is validated and the
    # excluded paths in it are honoured.
    & $exe --command extension build --source-dir $source --output-dir $out
    Write-Host ""
    Write-Host "Built into $out"
    Write-Host "Install it in Blender: Edit, Preferences, Get Extensions,"
    Write-Host "the dropdown at the top right, Install from Disk."
    exit 0
}

# Ask Blender where its extensions live, rather than guessing the version.
$probe = Join-Path $env:TEMP "holo_blender_probe.py"
@'
import bpy, sys
sys.stdout.write("EXTDIR=" + bpy.utils.user_resource("EXTENSIONS", create=True) + "\n")
sys.stdout.flush()
'@ | Out-File -FilePath $probe -Encoding utf8

$outer = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $probeOut = & $exe --background --factory-startup --python $probe 2>$null | Out-String
} finally {
    $ErrorActionPreference = $outer
    Remove-Item $probe -ErrorAction SilentlyContinue
}

$match = [regex]::Match($probeOut, "EXTDIR=(.+)")
if (-not $match.Success) {
    throw "Blender did not report its extensions folder. Output was:`n$probeOut"
}
$extensions = $match.Groups[1].Value.Trim()
$target = Join-Path (Join-Path $extensions "user_default") $id
Write-Host "Extensions: $extensions"

if ($Uninstall) {
    if (Test-Path $target) {
        # Remove-Item on a junction removes the link, not the target, but
        # only when the junction is treated as a file rather than walked.
        (Get-Item $target).Delete()
        Write-Host "Removed $target"
    } else {
        Write-Host "Nothing installed at $target"
    }
    Write-Host "Restart Blender."
    exit 0
}

$parent = Split-Path $target -Parent
if (-not (Test-Path $parent)) { New-Item -ItemType Directory $parent -Force | Out-Null }
if (Test-Path $target) { (Get-Item $target).Delete() }

New-Item -ItemType Junction -Path $target -Target $source | Out-Null
Write-Host "Linked $target"
Write-Host "   -> $source"
Write-Host ""
Write-Host "Now in Blender: Edit, Preferences, Add-ons, search for Holo-Blender,"
Write-Host "and tick it. Then look for the Holo tab in the 3D viewport sidebar (N)."
