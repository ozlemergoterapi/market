@echo off
chcp 65001 >nul
title Sancakli Market Kurulumu
cd /d "%~dp0"
echo Sancakli Market kurulumu baslatiliyor...
if not exist "%~dp0Windows-Uygulamasini-Olustur.ps1" (
  echo.
  echo HATA: Kurulum dosyalari ayni klasorde bulunamadi.
  echo ZIP dosyasini acip icinden calistirmayin.
  echo ZIP dosyasina sag tiklayin, Tumunu Ayikla secin ve cikarilan klasordeki
  echo Windows-Kurulum.cmd dosyasini yeniden calistirin.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Windows-Uygulamasini-Olustur.ps1"
if errorlevel 1 (
  echo.
  echo Kurulum tamamlanamadi. Yukaridaki hata mesajinin ekran goruntusunu paylasin.
  pause
  exit /b 1
)
echo.
echo Kurulum basariyla tamamlandi.
pause

