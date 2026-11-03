#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"

market_brew=""
for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew brew; do
  if "$candidate" --version >/dev/null 2>&1; then
    market_brew="$candidate"
    break
  fi
done

if [ -z "$market_brew" ]; then
  echo 'Homebrew resmi kaynaktan indiriliyor. macOS parola isteyebilir...'
  brew_installer="$(mktemp -t sancakli-homebrew.XXXXXX)"
  trap 'rm -f "$brew_installer"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL \
    'https://raw.githubusercontent.com/Homebrew/install/d6a2423c5973e04b45a5f17dc1948f6c9e5e651a/install.sh' \
    -o "$brew_installer"
  expected_hash='5f333bbe53bc490e51e7ccb1df8779b3dd6ee73a1a7379efda216edb08ccb148'
  actual_hash="$(shasum -a 256 "$brew_installer" | awk '{print $1}')"
  if [ "$actual_hash" != "$expected_hash" ]; then
    echo 'Homebrew kurulum dosyasının güvenlik doğrulaması başarısız oldu.'
    exit 1
  fi
  /bin/bash "$brew_installer"
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if "$candidate" --version >/dev/null 2>&1; then
      market_brew="$candidate"
      break
    fi
  done
fi

if [ -z "$market_brew" ]; then
  echo 'Homebrew kurulamadı. Yukarıdaki hata mesajını paylaşın.'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
  exit 1
fi

market_python=""
for candidate in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/opt/python@3.13/bin/python3.13 /usr/local/opt/python@3.13/bin/python3.13 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if "$candidate" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))' >/dev/null 2>&1; then
    market_python="$candidate"
    break
  fi
done

if [ -z "$market_python" ]; then
  echo 'Python ve masaüstü pencere desteği otomatik kuruluyor...'
  "$market_brew" install python@3.13 python-tk@3.13
  for candidate in /opt/homebrew/opt/python@3.13/bin/python3.13 /usr/local/opt/python@3.13/bin/python3.13 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
    if "$candidate" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))' >/dev/null 2>&1; then
      market_python="$candidate"
      break
    fi
  done
fi

if [ -z "$market_python" ]; then
  echo 'Python ve Tk desteği otomatik kurulamadı. Yukarıdaki hata mesajını paylaşın.'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
  exit 1
fi

brew_prefix="$($market_brew --prefix)"
market_tesseract="$brew_prefix/bin/tesseract"
if [ ! -x "$market_tesseract" ]; then
  echo 'Tesseract belge okuma motoru otomatik kuruluyor...'
  "$market_brew" install tesseract
fi
if ! "$market_tesseract" --list-langs 2>/dev/null | grep -qx 'tur'; then
  echo 'Türkçe belge okuma paketi otomatik kuruluyor...'
  tessdata_directory="$brew_prefix/share/tessdata"
  mkdir -p "$tessdata_directory"
  tur_download="$(mktemp -t sancakli-tur.XXXXXX)"
  curl --proto '=https' --tlsv1.2 -fsSL \
    'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/tur.traineddata' \
    -o "$tur_download"
  expected_tur_hash='7393381111e1152420fc4092cb44eef4237580d21b92bf30d7d221aad192c6b7'
  actual_tur_hash="$(shasum -a 256 "$tur_download" | awk '{print $1}')"
  if [ "$actual_tur_hash" != "$expected_tur_hash" ]; then
    rm -f "$tur_download"
    echo 'Türkçe OCR dosyasının güvenlik doğrulaması başarısız oldu.'
    exit 1
  fi
  mv "$tur_download" "$tessdata_directory/tur.traineddata"
fi
if ! "$market_tesseract" --list-langs 2>/dev/null | grep -qx 'tur'; then
  echo 'Türkçe belge okuma paketi kurulamadı. Yukarıdaki hata mesajını paylaşın.'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
  exit 1
fi

echo 'Sancaklı Market Mac uygulaması hazırlanıyor...'
"$market_python" -m venv --clear .mac-build-env
. .mac-build-env/bin/activate
python -m pip install --quiet --upgrade pyinstaller pillow
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

