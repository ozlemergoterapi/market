#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"

market_python=""
for candidate in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if "$candidate" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))' >/dev/null 2>&1; then
    market_python="$candidate"
    break
  fi
done

if [ -z "$market_python" ]; then
  echo 'Python 3.11 veya daha yeni bir sürüm ve Tk desteği gerekiyor.'
  echo 'Resmi yükleyici: https://www.python.org/downloads/macos/'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
  exit 1
fi

echo 'Sancaklı Market Mac uygulaması hazırlanıyor...'
"$market_python" -m venv .mac-build-env
. .mac-build-env/bin/activate
python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --windowed \
  --name 'Sancakli Market' \
  --osx-bundle-identifier 'tr.sancaklimarket.cari' \
  --icon static/logo.png \
  --add-data 'static:static' \
  desktop_launcher.py

mkdir -p "$HOME/Applications"
target="$HOME/Applications/Sancakli Market.app"
if [ -e "$target" ]; then
  mv "$target" "$HOME/Applications/Sancakli Market eski $(date +%Y%m%d-%H%M%S).app"
fi
ditto "dist/Sancakli Market.app" "$target"
codesign --force --deep --sign - "$target"

echo
echo 'Kurulum tamamlandı: ~/Applications/Sancakli Market.app'
echo 'Bundan sonra uygulamayı çift tıklayarak açabilirsiniz.'
open "$target"

