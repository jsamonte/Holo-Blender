<#
.SYNOPSIS
    Run every Holo-Blender test.

.DESCRIPTION
    Three suites, all on Blender's own Python with nothing installed, which
    is the interpreter the extension actually runs on.

      test_server.py        the embedded HTTP and WebSocket server. Runs on
                            Blender's python.exe, with no Blender at all,
                            because the server never touches Blender's data.
      test_exporter.py      Blender objects to GLB, and the sizing. Needs
                            Blender, since it builds real objects.
      test_end_to_end.py    a Blender object through service.send() to a
                            WebSocket client and a downloaded GLB.
      test_operators.py     the buttons, through the real install path: the
                            extension enabled the way Blender enables it,
                            which is the only way to catch a preferences
                            lookup that silently finds nothing. Skips
                            itself, passing, when it is not linked in.

    Both Blender suites run with --factory-startup, so they are unaffected
    by whatever addons and preferences happen to be set up here.

.EXAMPLE
    .\tools\run_tests.ps1
#>

[CmdletBinding()]
param(
    [string]$Blender
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

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
$blenderDir = Split-Path $exe -Parent
# Blender's bundled interpreter, for the suite that does not need Blender.
$python = @(Get-ChildItem -Path $blenderDir -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName)
if ($python.Count -eq 0) {
    throw "Could not find Blender's bundled python.exe under $blenderDir"
}
Write-Host "Blender: $exe"
Write-Host "Python : $($python[0])"
Write-Host ""

$suites = @(
    @{ Name = "server";      Exe = $python[0]; Args = @("tools\test_server.py") },
    @{ Name = "exporter";    Exe = $exe; Args = @("--background", "--factory-startup", "--python", "tools\test_exporter.py") },
    @{ Name = "end to end";  Exe = $exe; Args = @("--background", "--factory-startup", "--python", "tools\test_end_to_end.py") },
    @{ Name = "operators";   Exe = $exe; Args = @("--background", "--factory-startup", "--python", "tools\test_operators.py") }
)

$failed = @()
foreach ($suite in $suites) {
    Write-Host ("=" * 60)
    Write-Host $suite.Name
    Write-Host ("=" * 60)
    Push-Location $root
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        # Two separate PowerShell 5.1 traps, and both of them made a passing
        # suite look broken.
        #
        # Not "2>&1": redirecting a native command's stderr inside the
        # pipeline wraps every line in an error record and sets $? to false,
        # so a suite that passed while printing anything to stderr is
        # reported as a failure. Blender prints plenty.
        #
        # And "Stop" has to come off for the call itself. Those same error
        # records are still raised with 2>$errFile, and under
        # $ErrorActionPreference = "Stop" the first one is terminating.
        $outer = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & $suite.Exe @($suite.Args) 2>$errFile | Out-String
        } finally {
            $ErrorActionPreference = $outer
        }
        $stderr = Get-Content $errFile -Raw -ErrorAction SilentlyContinue
    } finally {
        Pop-Location
        Remove-Item $errFile -ErrorAction SilentlyContinue
    }

    # Blender exits 0 whatever a script returns, so the suites print a final
    # line and that is what decides the result.
    $output -split "`n" | Where-Object { $_ -match "^\s{2}(ok|FAIL)|^all checks passed|failure\(s\)" } | ForEach-Object {
        Write-Host $_.TrimEnd()
    }
    if ($output -notmatch "all checks passed") {
        $failed += $suite.Name
        if ($stderr) {
            Write-Host "stderr:"
            Write-Host ($stderr.Trim() -split "`n" | Select-Object -Last 15)
        }
    }
    Write-Host ""
}

Write-Host ("=" * 60)
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + ($failed -join ", "))
    exit 1
}
Write-Host "every suite passed"
exit 0
