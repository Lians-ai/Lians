@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem The hook configuration supplies one of these two fixed actions. Treat any
rem other invocation as an additive hook failure and remain silent.
if /i "%~1"=="hook" goto :hook
if /i "%~1"=="prewarm" goto :prewarm
exit /b 0

:hook
set "liansAction=hook"
goto :run

:prewarm
set "liansAction=prewarm"

:run
rem Keep Python imports and plugin state outside the active project. Empty SET
rem assignments remove inherited values from the child environment.
set "LIANS_MEMORY_HOME="
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONNOUSERSITE=1"
set "PYTHONSAFEPATH=1"

rem Delayed expansion prevents metacharacters from hostile environment values
rem from being reparsed as cmd syntax. No prompt bytes are read by this script;
rem the trusted Python process inherits the original stdin handle directly.
setlocal EnableDelayedExpansion
set "liansNativeBase=!LOCALAPPDATA!"
call :validateNativeBase
if defined liansNativeBaseValid goto :baseReady

set "liansNativeBase=!USERPROFILE!"
call :validateNativeBase
if not defined liansNativeBaseValid exit /b 0
set "liansNativeBase=!liansNativeBase!\AppData\Local"

:baseReady
set "liansPython=!liansNativeBase!\Lians\CodexMemory\venv\Scripts\python.exe"
if not exist "!liansPython!" exit /b 0

"!liansPython!" -B "%~dp0lians_plugin.py" !liansAction!
set "liansExit=!errorlevel!"
exit /b !liansExit!

:validateNativeBase
set "liansNativeBaseValid="
if not defined liansNativeBase exit /b 0

rem Accept a drive-rooted path only when the drive prefix is an ASCII letter.
if "!liansNativeBase:~1,2!"==":\" goto :validateDrive
if "!liansNativeBase:~1,2!"==":/" goto :validateDrive

rem Accept a conventional UNC server/share path, but not device namespaces.
if not "!liansNativeBase:~0,2!"=="\\" exit /b 0
if "!liansNativeBase:~0,4!"=="\\?\" exit /b 0
if "!liansNativeBase:~0,4!"=="\\.\" exit /b 0
set "liansUncTail=!liansNativeBase:~2!"
if not defined liansUncTail exit /b 0
if "!liansUncTail:~0,1!"=="\" exit /b 0
set "liansUncShare=!liansUncTail:*\=!"
if "!liansUncShare!"=="!liansUncTail!" exit /b 0
if not defined liansUncShare exit /b 0
if "!liansUncShare:~0,1!"=="\" exit /b 0
set "liansNativeBaseValid=1"
exit /b 0

:validateDrive
set "liansDrive=!liansNativeBase:~0,1!"
for %%D in (A B C D E F G H I J K L M N O P Q R S T U V W X Y Z) do if /i "!liansDrive!"=="%%D" set "liansNativeBaseValid=1"
exit /b 0
