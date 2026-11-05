# Builds dist\PixelPanel.exe (one file, no console).
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    uv venv --python 3.13 .venv
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
}
$py = ".venv\Scripts\python.exe"
if (Get-Process PixelPanel -ErrorAction SilentlyContinue) {
    # (ASCII only: Windows PowerShell 5.1 reads this file as ANSI)
    throw "PixelPanel.exe is still running - quit it first (tray icon > Beenden), otherwise the exe cannot be replaced."
}

# app icon (.ico) from the same pixel art the window uses
& $py -c "from PIL import Image; from pixelpanel.core import icons; h = icons.builtin_icon('heart'); img = Image.new('RGBA', (16, 16), (0, 0, 0, 0)); img.paste(Image.new('RGBA', (16, 12), (20, 20, 28, 255)), (0, 2)); img.paste(h, (4, 4), h); img.resize((256, 256), Image.Resampling.NEAREST).save('icon.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name PixelPanel --icon icon.ico `
    --collect-submodules bleak --collect-submodules winrt --hidden-import win32timezone `
    --exclude-module tkinter --exclude-module pytest `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)." }
$exe = Get-Item dist\PixelPanel.exe
if ($exe.LastWriteTime -lt (Get-Date).AddMinutes(-5)) { throw "dist\PixelPanel.exe was not rewritten." }
Write-Host "Done: $($exe.FullName) ($($exe.LastWriteTime))"
