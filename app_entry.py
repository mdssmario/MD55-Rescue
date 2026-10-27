"""Punto de entrada de la aplicación empaquetada (PyInstaller).

Sin argumentos abre la interfaz; con argumentos actúa como motor (lo usa la propia
interfaz para lanzar el rescate con permisos de Administrador).
"""
import sys

from xavc_rescue.__main__ import run

if __name__ == '__main__':
    sys.exit(run())
