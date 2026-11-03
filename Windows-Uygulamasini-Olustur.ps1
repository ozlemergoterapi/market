$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $resolved = (& py -3 -c "import sys; raise SystemExit(sys.version_info < (3,11)); print(sys.executable)" 2>$null | Select-Object -Last 1)
        if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Path $resolved)) { return $resolved }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $resolved = (& python -c "import sys; raise SystemExit(sys.version_info < (3,11)); print(sys.executable)" 2>$null | Select-Object -Last 1)
        if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Path $resolved)) { return $resolved }
    }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            & $candidate -c "import sys; raise SystemExit(sys.version_info < (3,11))" 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}

function Find-Tesseract {
    $command = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Require-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Windows Uygulama Yükleyicisi (winget) bulunamadı. Windows Update tamamlandıktan sonra kurulumu yeniden çalıştırın.'
    }
}

function Install-WingetPackage([string]$Id, [switch]$UserScope) {
    $arguments = @('install', '--id', $Id, '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements', '--silent', '--disable-interactivity')
    if ($UserScope) { $arguments += @('--scope', 'user') }
    & winget @arguments
    if ($LASTEXITCODE -ne 0) { throw "$Id otomatik kurulamadı. Windows kurulum kodu: $LASTEXITCODE" }
}

function Download-Verified([string]$Url, [string]$Destination, [string]$Sha256) {
    $temporary = "$Destination.download"
    Remove-Item $temporary -Force -ErrorAction SilentlyContinue
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $temporary
    $actual = (Get-FileHash -Algorithm SHA256 $temporary).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item $temporary -Force -ErrorAction SilentlyContinue
        throw "İndirilen OCR dil dosyasının güvenlik doğrulaması başarısız: $Destination"
    }
    Move-Item $temporary $Destination -Force
}

Write-Host 'Sancaklı Market için gerekli bileşenler kontrol ediliyor...'
$python = Find-Python
if (-not $python) {
    Require-Winget
    Write-Host 'Python otomatik indiriliyor ve kuruluyor...'
    Install-WingetPackage 'Python.Python.3.12' -UserScope
    $python = Find-Python
}
if (-not $python) { throw 'Python kurulumu tamamlandı ancak çalıştırma dosyası bulunamadı.' }

& $python -c "import sys; raise SystemExit(sys.version_info < (3,11))"
if ($LASTEXITCODE -ne 0) { throw 'Kurulan Python sürümü 3.11 veya daha yeni olmalıdır.' }

$tesseract = Find-Tesseract
if (-not $tesseract) {
    Require-Winget
    Write-Host 'Tesseract belge okuma motoru otomatik indiriliyor ve kuruluyor...'
    Install-WingetPackage 'UB-Mannheim.TesseractOCR'
    $tesseract = Find-Tesseract
}
if (-not $tesseract) { throw 'Tesseract kurulumu tamamlandı ancak çalıştırma dosyası bulunamadı.' }

$dataDirectory = Join-Path $env:LOCALAPPDATA 'Sancakli Market'
$languageDirectory = Join-Path $dataDirectory 'tessdata'
New-Item -ItemType Directory -Force -Path $languageDirectory | Out-Null
Write-Host 'Türkçe ve İngilizce belge okuma dosyaları doğrulanıyor...'
Download-Verified `
    'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/tur.traineddata' `
    (Join-Path $languageDirectory 'tur.traineddata') `
    '7393381111e1152420fc4092cb44eef4237580d21b92bf30d7d221aad192c6b7'
Download-Verified `
    'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata' `
    (Join-Path $languageDirectory 'eng.traineddata') `
    '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'

Write-Host 'Sancaklı Market Windows uygulaması hazırlanıyor...'
& $python -m venv --clear .windows-build-env
$venvPython = Join-Path $PSScriptRoot '.windows-build-env\Scripts\python.exe'
& $venvPython -m pip install --quiet --upgrade pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw 'Uygulama paketleme araçları indirilemedi.' }
& $venvPython -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name 'Sancakli-Market' `
    --icon 'static\logo.png' `
    --add-data 'static;static' `
    desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Sancaklı Market uygulama paketi oluşturulamadı.' }

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

