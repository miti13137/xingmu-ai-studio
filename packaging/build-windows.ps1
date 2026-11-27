$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
python -m PyInstaller --noconfirm --clean --noupx --onedir --name Xingmu-Studio `
    --add-data 'production/workbench.html:.' `
    production/server.py
if ($LASTEXITCODE -ne 0) {
    throw "Windows build failed: $LASTEXITCODE"
}
Copy-Item packaging/Start-Xingmu.cmd dist/Xingmu-Studio/
Copy-Item -LiteralPath 'packaging/開始使用.txt' -Destination 'dist/Xingmu-Studio/'
