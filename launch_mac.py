"""Mac yerel başlangıcı. Python 3.11+; ağ veya paket indirmesi yapmaz."""
import os
from pathlib import Path
import sys
import threading
import webbrowser


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 veya daha yeni bir sürüm gerekiyor.')
    storage = Path.home() / 'Library' / 'Application Support' / 'Sancakli Market'
    storage.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MARKET_DB', str(storage / 'market.sqlite3'))
    import app
    app.init()
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    address = 'http://127.0.0.1:' + str(server.server_port)
    print('\nSancaklı Market açılıyor. Bu pencereyi kullanım boyunca açık bırakın.', flush=True)
    print('Kapatmak için bu pencerede Control + C tuşlarına basın.', flush=True)
    print('Tarayıcı açılmazsa şu adresi kopyalayın: ' + address, flush=True)
    print('Veri dosyası: ' + str(app.DB), flush=True)
    timer = threading.Timer(0.8, webbrowser.open, args=(address,))
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nUygulama kapatıldı. Kayıtlarınız bilgisayarda saklanıyor.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
