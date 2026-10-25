[CmdletBinding()]
param([string]$DistDirectory = '.local\dist')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $distRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $DistDirectory))
    $localRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.local'))
    if (-not $distRoot.StartsWith($localRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Le dossier de construction doit rester dans .local/.'
    }
    $destination = Join-Path $distRoot 'RobloxScriptExplorer'
    if (Test-Path -LiteralPath $destination) { throw 'Ce paquet existe déjà. Choisissez un nouveau -DistDirectory pour préserver ses données locales.' }
    & python -m PyInstaller --noconfirm --onedir --windowed --name RobloxScriptExplorer --hidden-import compression.zstd --distpath $distRoot --workpath .local\pyinstaller --specpath .local explorer_gui.py
    if ($LASTEXITCODE -ne 0) { throw "La construction de l'application a échoué." }
    foreach ($file in @('Extract-RobloxClient.ps1','RobloxReadOnlyProbe.cs','Build-Backend.ps1','backend.lock.json','README.md','LICENSE','THIRD_PARTY.md')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $destination -Force
    }
    if (Test-Path -LiteralPath .\tools) {
        New-Item -ItemType Directory -Path (Join-Path $destination 'tools') -Force | Out-Null
        Get-ChildItem -LiteralPath .\tools -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination 'tools') -Force
        }
    }
    Write-Output "Application construite : $destination"
} finally { Pop-Location }
