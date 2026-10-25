[CmdletBinding()]
param([int]$ProcessId = 0, [string]$OutputDirectory = '', [switch]$SkipValidation)
$ErrorActionPreference = 'Stop'
$supportedBuild = 'version-9ac8595712b447a7'
$clients = @(Get-Process -Name RobloxPlayerBeta -ErrorAction SilentlyContinue)
if ($ProcessId) { $clients = @($clients | Where-Object Id -eq $ProcessId) }
if ($clients.Count -ne 1) { throw 'Sélectionnez une instance Roblox unique avec -ProcessId, ou fermez les clients supplémentaires.' }
$client = $clients[0]
$build = Split-Path -Leaf (Split-Path -Parent $client.Path)
if ($build -ne $supportedBuild -or $client.MainModule.FileVersionInfo.FileVersion -ne '0, 744, 0, 7441080') {
    throw "Build non prise en charge : $build. Les adresses doivent être revérifiées."
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot ('.local\captures\capture-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Le dossier de sortie existe déjà.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
if (-not ('RobloxReadOnlyProbe' -as [type])) { Add-Type -Path (Join-Path $PSScriptRoot 'RobloxReadOnlyProbe.cs') }
$probe = [RobloxReadOnlyProbe]::new($client.Id)
$probe.OutputDirectory = $OutputDirectory
$probe.Export = $true
try {
    $status = $probe.Run($client.MainModule.BaseAddress.ToInt64())
    [ordered]@{
        Build = $supportedBuild
        CapturedAt = [DateTimeOffset]::Now.ToString('o')
        Status = $status
        Scope = @('ReplicatedStorage','ReplicatedFirst','StarterPlayer','StarterGui','StarterPack','Players')
        Visited = $probe.Visited
        Scripts = $probe.ScriptCount
        ReadFailures = $probe.ReadFailures
        Limited = $probe.Limited
        Findings = @($probe.Findings)
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'manifest.json') -Encoding utf8
    if ($status -notlike 'DataModel validated*' -or $probe.ScriptCount -eq 0) { throw "Capture non validée : $status" }
} finally { $probe.Dispose() }
if (-not $SkipValidation) {
    & python (Join-Path $PSScriptRoot 'validate_extraction.py') $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Validation incomplète : consultez le manifeste.' }
}
Write-Output $OutputDirectory
