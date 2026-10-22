@echo off
rem Monitoring satellite FSH4 + IRD : double-cliquer pour lancer (Python 3.11 ou plus recent).
rem La page s ouvre dans le navigateur ; fermer cette fenetre pour arreter.
cd /d "%~dp0"
echo Monitoring satellite - page : http://localhost:8080 (fermer cette fenetre pour arreter)
where py >nul 2>nul
if %errorlevel%==0 (py -3 -m monitoring.server --open) else (python -m monitoring.server --open)
pause
