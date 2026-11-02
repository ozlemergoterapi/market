#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
market_python=""
for candidate in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 python3; do
  if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,11))' >/dev/null 2>&1; then
    market_python="$candidate"
    break
  fi
done
if [ -z "$market_python" ]; then
  echo 'Sancaklı Market için Python 3.11 veya daha yeni bir sürüm gerekiyor.'
  echo 'Safari ile https://www.python.org/downloads/macos/ adresinden Python yükleyin.'
  echo 'Kurulumdan sonra bu dosyayı tekrar açın.'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
  exit 1
fi
"$market_python" launch_mac.py
result=$?
if [ "$result" -ne 0 ]; then
  echo 'Uygulama açılamadı. Yukarıdaki hata mesajını paylaşabilirsiniz.'
  read -r -p 'Pencereyi kapatmak için Enter tuşuna basın. ' answer
fi
exit "$result"
