import sys


def run():
    if len(sys.argv) > 1:
        from .cli import main
        return main()
    try:
        from .qtapp import main
    except ImportError:             # sin PySide6: interfaz básica de tkinter
        from .gui import main
    return main()


if __name__ == '__main__':
    sys.exit(run())
