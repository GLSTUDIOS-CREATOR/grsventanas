@echo off
REM ===========================
REM  BAT DE INICIO GLBINGO
REM ===========================

REM Cambiar a la carpeta donde está app.py
cd /d "%~dp0sistema"

REM Activar entorno virtual si tienes uno (descomenta la línea correcta)
REM call venv\Scripts\activate
REM call .\venv\Scripts\activate

REM Lanzar el servidor Flask (ajusta si el archivo no es app.py)
python app.py

pause
