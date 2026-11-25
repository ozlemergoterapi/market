"""Launch the shared desktop app from the Mac command file."""
import sys

from desktop_launcher import main


if __name__ == '__main__':
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 veya daha yeni bir sürüm gerekiyor.')
    main()
