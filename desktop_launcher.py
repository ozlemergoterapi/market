"""Sancaklı Market'in Mac ve Windows masaüstü başlatıcısı."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.request import urlopen
import webbrowser


def storage_directory(platform=None, home=None, local_app_data=None):
    """Return the per-user data folder without depending on the app location."""
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'Sancakli Market'
    if platform == 'win32':
        base = Path(local_app_data or os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        return base / 'Sancakli Market'
    return Path(os.environ.get('XDG_DATA_HOME') or home / '.local' / 'share') / 'sancakli-market'


def add_ocr_paths():
    """Finder and Windows shortcuts start with a small PATH; include common OCR locations."""
    candidates = []
    if sys.platform == 'darwin':
        candidates = ['/opt/homebrew/bin', '/usr/local/bin']
    elif sys.platform == 'win32':
        candidates = [
            str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tesseract-OCR'),
            str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Tesseract-OCR'),
        ]
        language_directory = storage_directory() / 'tessdata'
        if language_directory.is_dir():
            os.environ.setdefault('TESSDATA_PREFIX', str(language_directory))
    existing = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join([path for path in candidates if path and Path(path).is_dir()] + [existing])


def resource_path(relative):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / relative


def main():
    from tkinter import Button, Label, PhotoImage, Tk, messagebox

    storage = storage_directory()
    storage.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MARKET_DB', str(storage / 'market.sqlite3'))
    add_ocr_paths()

    import app

    backup_message = 'Henüz yedeklenecek kayıt yok.'
    try:
        created = app.automatic_backup()
        if created:
            backup_message = 'Açılış yedeği oluşturuldu: ' + created.name
    except (OSError, app.sqlite3.Error) as error:
        backup_message = 'Yedek oluşturulamadı: ' + str(error)

    app.init()
    error_log = storage / 'startup-error.log'

    class LoggedHTTPServer(app.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            with error_log.open('a', encoding='utf-8') as log:
                log.write('\nYerel sunucu isteği işlenemedi:\n')
                traceback.print_exc(file=log)

    try:
        server = LoggedHTTPServer(('127.0.0.1', 0), app.Handler)
    except OSError as error:
        raise RuntimeError('Yerel uygulama sunucusu başlatılamadı: ' + str(error)) from error

    address = 'http://127.0.0.1:' + str(server.server_port)
    worker = threading.Thread(target=server.serve_forever, name='sancakli-market', daemon=True)
    worker.start()

    window = Tk()
    window.title('Sancaklı Market')
    window.geometry('460x300')
    window.resizable(False, False)
    window.configure(padx=28, pady=24)
    try:
        icon = PhotoImage(file=str(resource_path('static/logo.png')))
        window.iconphoto(True, icon)
        window._sancakli_icon = icon
    except Exception:
        pass

    Label(window, text='SANCAKLI MARKET', font=('Arial', 22, 'bold')).pack(pady=(8, 4))
    Label(window, text='Kaliteli Ürün, Güvenilir Hizmet', font=('Arial', 12)).pack(pady=(0, 18))
    Label(window, text='Uygulama bu bilgisayarda çalışıyor.\nBu pencereyi küçültebilirsiniz; kapatmak için aşağıdaki düğmeyi kullanın.',
          justify='center').pack(pady=(0, 12))
    Label(window, text=backup_message, wraplength=400, fg='#52645a').pack(pady=(0, 16))

    opening = False
    def open_application():
        nonlocal opening
        if opening:
            return
        opening = True

        def wait_until_ready():
            nonlocal opening
            last_error = None
            for _ in range(30):
                try:
                    with urlopen(address, timeout=2) as response:
                        beginning = response.read(100).lower()
                        status = response.status
                    if status == 200 and b'<!doctype html' in beginning:
                        webbrowser.open(address)
                        opening = False
                        return
                except Exception as error:
                    last_error = error
                time.sleep(.2)
            with error_log.open('a', encoding='utf-8') as log:
                log.write('\nUygulama açılış kontrolü başarısız: ' + repr(last_error) + '\n')
            opening = False
            window.after(0, lambda: messagebox.showerror(
                'Sancaklı Market',
                'Uygulama sayfası hazırlanamadı. Hata kaydı:\n' + str(error_log),
            ))

        threading.Thread(target=wait_until_ready, name='sancakli-ready', daemon=True).start()

    closing = False
    def close_application():
        nonlocal closing
        if closing:
            return
        closing = True
        server.shutdown()
        server.server_close()
        window.destroy()

    Button(window, text='Uygulamayı Aç', command=open_application, width=22).pack(pady=4)
    Button(window, text='Uygulamayı Kapat', command=close_application, width=22).pack(pady=4)
    # The browser remains visible after the local server stops. Treat the
    # title-bar close action as minimize so users do not unknowingly keep a
    # disconnected, cached page open. The explicit button performs shutdown.
    window.protocol('WM_DELETE_WINDOW', window.iconify)
    window.after(700, open_application)
    window.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        try:
            from tkinter import Tk, messagebox
            root = Tk(); root.withdraw()
            messagebox.showerror('Sancaklı Market', 'Uygulama açılamadı.\n\n' + str(error))
            root.destroy()
        except Exception:
            raise

