@echo off
rem Genera dist\MD55 Rescue\MD55 Rescue.exe (requiere Python 3.10+)
cd /d "%~dp0"
python -m venv .venv-build || goto :error
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :error
python tools\make_icons.py || goto :error
pyinstaller --noconfirm MD55Rescue.spec || goto :error
echo.
echo Listo: dist\MD55 Rescue\MD55 Rescue.exe
goto :eof
:error
echo Error al compilar.
exit /b 1
