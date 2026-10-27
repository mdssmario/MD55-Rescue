#!/bin/bash
# Genera "dist/MD55 Rescue.app" y un .dmg. Ejecutar en un Mac (Apple Silicon o Intel).
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip > /dev/null
python -m pip install -r requirements.txt pyinstaller
python tools/make_icons.py
pyinstaller --noconfirm MD55Rescue.spec
hdiutil create -volname "MD55 Rescue" -srcfolder "dist/MD55 Rescue.app" -ov -format UDZO "dist/MD55-Rescue-mac-$(uname -m).dmg"
echo
echo "Listo: dist/MD55 Rescue.app y dist/MD55-Rescue-mac-$(uname -m).dmg"
echo "La app no está firmada: la primera vez, clic derecho → Abrir."
