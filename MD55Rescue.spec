# PyInstaller: genera MD55 Rescue.exe (Windows) o MD55 Rescue.app (macOS).
#   pyinstaller MD55Rescue.spec
import os
import sys

name = 'MD55 Rescue'
icon_ico = os.path.join('assets', 'icon.ico')
icon_icns = os.path.join('assets', 'icon.icns')
icon_png = os.path.join('assets', 'icon.png')

a = Analysis(
    ['app_entry.py'],
    pathex=['.'],
    datas=[(os.path.join('assets', 'logo.png'), 'assets')],
    hiddenimports=['xavc_rescue.qtapp', 'xavc_rescue.cli', 'xavc_rescue.gui'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.Qt3DCore',
              'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtMultimedia', 'PySide6.QtCharts',
              'PySide6.QtDataVisualization', 'PySide6.QtPdf', 'numpy'],
)
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=False,
              icon=icon_icns if os.path.exists(icon_icns) else icon_png)
    coll = COLLECT(exe, a.binaries, a.datas, name=name)
    app = BUNDLE(
        coll,
        name=name + '.app',
        icon=icon_icns if os.path.exists(icon_icns) else icon_png,
        bundle_identifier='com.md55rescue.app',
        info_plist={
            'CFBundleShortVersionString': '0.2.1',
            'NSHighResolutionCapable': True,
            'NSRemovableVolumesUsageDescription':
                'MD55 Rescue lee la tarjeta de la cámara para recuperar los clips.',
        },
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=False,
              icon=icon_ico if os.path.exists(icon_ico) else None)
    coll = COLLECT(exe, a.binaries, a.datas, name=name)
