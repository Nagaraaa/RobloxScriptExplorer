[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & python -m PyInstaller --noconfirm --onedir --windowed --name RobloxScriptExplorer --hidden-import compression.zstd --distpath .local\dist --workpath .local\pyinstaller --specpath .local explorer_gui.py
    if ($LASTEXITCODE -ne 0) { throw "La construction de l'application a échoué." }
    $destination = Join-Path $PSScriptRoot '.local\dist\RobloxScriptExplorer'
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
