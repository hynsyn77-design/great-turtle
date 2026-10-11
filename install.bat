@echo off
title Great Turtle - install
cd /d "%~dp0"
echo ============================================
echo   Great Turtle - installation
echo ============================================
echo.

rem --- Python ---
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python
if not defined PY goto nopython
echo Python found: %PY%
goto libs

:nopython
echo Python not found. Installing through winget...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo Python is installed. CLOSE this window and run install.bat again.
pause
exit /b

:libs
echo.
echo Installing libraries: PyQt5, yt-dlp, gallery-dl, curl-cffi ...
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade PyQt5 "yt-dlp[default]" gallery-dl curl-cffi
if errorlevel 1 goto piperror
goto ffmpeg

:piperror
echo.
echo Library installation failed. Check the internet connection and run again.
pause
exit /b

:ffmpeg
where ffmpeg >nul 2>&1
if not errorlevel 1 goto deno
echo.
echo Installing ffmpeg...
winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements

:deno
where deno >nul 2>&1
if not errorlevel 1 goto shortcut
echo.
echo Installing Deno (needed for YouTube)...
winget install -e --id DenoLand.Deno --accept-package-agreements --accept-source-agreements

:shortcut
echo.
echo Creating desktop shortcut...
set PYW=
for /f "delims=" %%i in ('where pyw 2^>nul') do if not defined PYW set PYW=%%i
if not defined PYW for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set PYW=%%i
if not defined PYW goto done
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Great Turtle.lnk'); $s.TargetPath='%PYW%'; $s.Arguments='\"%~dp0app.pyw\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0turtle.ico'; $s.Save()"

:done
echo.
echo ============================================
echo   Done. Start: the Great Turtle shortcut on the desktop
echo   or double-click app.pyw.
echo   NOTE: if ffmpeg or Deno were just installed,
echo   close this window and restart the program.
echo ============================================
pause
