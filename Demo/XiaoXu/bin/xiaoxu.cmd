@echo off
setlocal

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PROJECT_ROOT=%~dp0.."
if defined PYTHONPATH (
    set "PYTHONPATH=%PROJECT_ROOT%\src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%PROJECT_ROOT%\src"
)

pushd "%PROJECT_ROOT%"
"D:\Anaconda3\envs\langchain1.2\python.exe" -m private_agent.interfaces.cli.app %*
set "EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %EXIT_CODE%
