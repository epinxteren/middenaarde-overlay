@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 64-bit from python.org with the Python launcher first.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
if errorlevel 1 goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
.venv\Scripts\python.exe download_models.py
if errorlevel 1 goto error
.venv\Scripts\python.exe -m PyInstaller --noconfirm MiddenaardeOverlay.spec
if errorlevel 1 goto error
copy /y README.md dist\MiddenaardeOverlay\README.md >nul
copy /y THIRD_PARTY_NOTICES.md dist\MiddenaardeOverlay\THIRD_PARTY_NOTICES.md >nul
copy /y LICENSE dist\MiddenaardeOverlay\LICENSE >nul
copy /y glossary.json dist\MiddenaardeOverlay\glossary.json >nul
echo Ready: dist\MiddenaardeOverlay\MiddenaardeOverlay.exe
pause
exit /b 0
:error
echo Build failed. Read the error above.
pause
exit /b 1
