@echo off
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -m doppel_agent %*
exit /b %ERRORLEVEL%
