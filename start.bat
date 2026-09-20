@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=8000

if not exist ".venv\Scripts\python.exe" (
    echo 가상환경^(.venv^)을 찾을 수 없습니다. 먼저 아래 명령으로 설치하세요.
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo 포트 %PORT%번이 이미 사용 중입니다. 서버가 이미 켜져 있을 수 있습니다.
    echo   http://127.0.0.1:%PORT%
    echo 끄려면 stop.bat 을 실행하세요.
    pause
    exit /b 1
)

echo 서버를 시작합니다.
echo   학생 화면: http://127.0.0.1:%PORT%
echo   교사 화면: http://127.0.0.1:%PORT%/teacher.html
echo.
echo 끄려면 이 창에서 Ctrl+C 를 누르거나 stop.bat 을 실행하세요.
echo ^(Ctrl+C 후 "일괄 작업을 끝내시겠습니까" 라고 물으면 Y 를 입력하세요.^)
echo.

rem 서버가 뜰 시간을 3초 준 뒤 브라우저를 연다. NO_BROWSER 를 정의하면 열지 않는다.
if not defined NO_BROWSER start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process http://127.0.0.1:%PORT%"

".venv\Scripts\python.exe" -m uvicorn app.main:app --port %PORT%

echo.
echo 서버가 종료되었습니다.
pause
