param (
    [Parameter(Mandatory = $true)]
    [string] $MediaTools
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$MediaTools = (Resolve-Path $MediaTools).Path
foreach ($Name in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Test-Path (Join-Path $MediaTools $Name) -PathType Leaf)) {
        throw "找不到 $Name"
    }
}

python -m PyInstaller --noconfirm --clean --noupx --onedir --name Xingmu-Studio `
    --add-data 'production/workbench.html:.' `
    --add-binary "$MediaTools/ffmpeg.exe:media-tools" `
    --add-binary "$MediaTools/ffprobe.exe:media-tools" `
    production/server.py
if ($LASTEXITCODE -ne 0) {
    throw "Windows 打包失敗：$LASTEXITCODE"
}
