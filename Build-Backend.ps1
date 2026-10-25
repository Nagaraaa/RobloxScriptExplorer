[CmdletBinding()]
param([switch]$WithCompiler)
$ErrorActionPreference = 'Stop'
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'backend.lock.json') -Raw | ConvertFrom-Json
$source = Join-Path $PSScriptRoot 'third_party\luaudec'
$build = Join-Path $PSScriptRoot '.local\backend-build'
$tools = Join-Path $PSScriptRoot 'tools'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program a échoué ($LASTEXITCODE)." }
}
$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if ($cmake) { $cmake = $cmake.Source } else {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $cmake = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
    }
}
if (-not $cmake -or -not (Test-Path -LiteralPath $cmake)) { throw 'CMake et les Visual Studio C++ Build Tools sont requis.' }
if (-not (Test-Path -LiteralPath $source)) {
    Invoke-Checked 'git' @('clone','--no-checkout',$lock.repository,$source)
}
Invoke-Checked 'git' @('-C',$source,'fetch','origin',$lock.commit)
Invoke-Checked 'git' @('-C',$source,'checkout','--detach',$lock.commit)
Invoke-Checked 'git' @('-C',$source,'submodule','update','--init','--depth','1')
$actualLuau = & git -C (Join-Path $source 'third_party\luau') rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualLuau -ne $lock.luauCommit) { throw 'Le sous-module Luau ne correspond pas à la version verrouillée.' }
Invoke-Checked 'python' @((Join-Path $PSScriptRoot 'patch_backend.py'),$source)
$toolOption = if ($WithCompiler) { 'ON' } else { 'OFF' }
Invoke-Checked $cmake @('-S',$source,'-B',$build,'-A','x64',"-DLUAUDEC_WITH_LUAU_TOOLS=$toolOption")
Invoke-Checked $cmake @('--build',$build,'--config','Release','--target','luaudec','--parallel','4')
New-Item -ItemType Directory -Path $tools -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $build 'Release\luaudec.exe') -Destination (Join-Path $tools 'luaudec.exe') -Force
if ($WithCompiler) {
    Invoke-Checked $cmake @('--build',(Join-Path $build 'third_party\luau'),'--config','Release','--target','Luau.Compile.CLI','--parallel','4')
    Invoke-Checked $cmake @('--build',(Join-Path $build 'third_party\luau'),'--config','Release','--target','Luau.Repl.CLI','--parallel','4')
    Copy-Item -LiteralPath (Join-Path $build 'third_party\luau\Release\luau-compile.exe') -Destination (Join-Path $tools 'luau-compile.exe') -Force
    Copy-Item -LiteralPath (Join-Path $build 'third_party\luau\Release\luau.exe') -Destination (Join-Path $tools 'luau.exe') -Force
}
@{ commit=$lock.commit; luauCommit=$lock.luauCommit; patchSet=$lock.patchSet; sha256=(Get-FileHash -LiteralPath (Join-Path $tools 'luaudec.exe') -Algorithm SHA256).Hash } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $tools 'build-info.json') -Encoding utf8
Write-Output 'Backend local prêt dans tools/.'
