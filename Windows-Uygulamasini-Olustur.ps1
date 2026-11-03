$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$pythonCommand = $null
$pythonPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c "import sys, tkinter; raise SystemExit(sys.version_info < (3,11))"
    if ($LASTEXITCODE -eq 0) { $pythonCommand = 'py'; $pythonPrefix = @('-3') }
}
if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c "import sys, tkinter; raise SystemExit(sys.version_info < (3,11))"
    if ($LASTEXITCODE -eq 0) { $pythonCommand = 'python' }
}
if (-not $pythonCommand) {
    throw 'Python 3.11 veya daha yeni bir sürüm gerekiyor. https://www.python.org/downloads/windows/'
}

Write-Host 'Sancaklı Market Windows uygulaması hazırlanıyor...'
& $pythonCommand @pythonPrefix -m venv .windows-build-env
$venvPython = Join-Path $PSScriptRoot '.windows-build-env\Scripts\python.exe'
& $venvPython -m pip install --quiet --upgrade pyinstaller pillow
& $venvPython -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name 'Sancakli-Market' `
    --icon 'static\logo.png' `
    --add-data 'static;static' `
    desktop_launcher.py

$installDirectory = Join-Path $env:LOCALAPPDATA 'Programs\Sancakli Market'
New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
$executable = Join-Path $installDirectory 'Sancakli-Market.exe'
Copy-Item 'dist\Sancakli-Market.exe' $executable -Force

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = $shell.CreateShortcut((Join-Path $desktop 'Sancaklı Market.lnk'))
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = $installDirectory
$shortcut.IconLocation = $executable
$shortcut.Save()

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$startShortcut = $shell.CreateShortcut((Join-Path $startMenu 'Sancaklı Market.lnk'))
$startShortcut.TargetPath = $executable
$startShortcut.WorkingDirectory = $installDirectory
$startShortcut.IconLocation = $executable
$startShortcut.Save()

Write-Host 'Kurulum tamamlandı. Masaüstündeki Sancaklı Market simgesini çift tıklayabilirsiniz.'
Start-Process $executable

