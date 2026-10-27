@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PORT=8000

rem 시연용: 이 PC 의 서버를 인터넷에서 접속할 수 있는 임시 https 주소로 연다 (Cloudflare 빠른 터널).
rem 주소는 실행할 때마다 바뀌고, 이 창을 닫으면 주소도 사라진다.

if not exist ".venv\Scripts\python.exe" (
    echo 가상환경^(.venv^)을 찾을 수 없습니다. 먼저 아래 명령으로 설치하세요.
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem 터널 프로그램을 찾는다: PATH 에 있거나, 이 폴더의 tools\cloudflared.exe.
set CLOUDFLARED=
where cloudflared >nul 2>nul
if not errorlevel 1 set CLOUDFLARED=cloudflared
if not defined CLOUDFLARED if exist "tools\cloudflared.exe" set CLOUDFLARED=tools\cloudflared.exe
if not defined CLOUDFLARED (
    echo 터널 프로그램^(cloudflared^)을 찾을 수 없습니다. 아래 명령으로 설치한 뒤 새 창에서 다시 실행하세요.
    echo   winget install --id Cloudflare.cloudflared
    echo.
    echo 설치하지 않고 쓰려면 cloudflared.exe 를 내려받아 이 폴더의 tools 폴더에 넣어도 됩니다.
    echo   https://github.com/cloudflare/cloudflared/releases/latest
    pause
    exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo 포트 %PORT%번이 이미 사용 중입니다. 서버가 이미 켜져 있을 수 있습니다.
    echo stop.bat 으로 끈 뒤 다시 실행하세요. ^(공개용 설정으로 새로 켜야 합니다.^)
    pause
    exit /b 1
)

rem 공개용 설정. 쿠키를 HTTPS 로만 보내고, 교사 가입에 초대 코드를 요구한다.
set CWA_PUBLIC=1
rem 시연 데이터는 평소 쓰는 data.db 와 섞이지 않게 따로 둔다. 다른 파일을 쓰려면 CWA_DB_PATH 를 미리 정한다.
if not defined CWA_DB_PATH set CWA_DB_PATH=demo.db
rem 초대 코드는 실행할 때마다 새로 만든다. 고정하려면 CWA_SIGNUP_CODE 를 미리 정한다.
if not defined CWA_SIGNUP_CODE for /f %%c in ('.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(4))"') do set CWA_SIGNUP_CODE=%%c

echo ============================================================
echo  시연용 공개 서버를 시작합니다.
echo.
echo  1. 잠시 뒤 아래에 나오는 https://....trycloudflare.com 주소가
echo     공개 주소입니다. 그 주소를 시연할 사람에게 알려 주세요.
echo       학생 화면: 공개 주소 그대로
echo       교사 화면: 공개 주소 뒤에 /teacher.html
echo.
echo  2. 교사 회원 가입에 필요한 초대 코드:  %CWA_SIGNUP_CODE%
echo.
echo  3. 데이터는 %CWA_DB_PATH% 에 저장됩니다 ^(평소의 data.db 와 별개^).
echo.
echo  끝내려면 이 창에서 Ctrl+C 를 누르세요. 서버도 함께 꺼집니다.
echo ============================================================
echo.

rem 서버는 이 PC 안에서만(127.0.0.1) 열고, 바깥에서는 터널을 거쳐서만 들어온다.
start "피드백 웹서비스 서버 (공개용)" /min ".venv\Scripts\python.exe" -m uvicorn app.main:app --port %PORT%
powershell -NoProfile -Command "Start-Sleep -Seconds 3"

%CLOUDFLARED% tunnel --url http://127.0.0.1:%PORT%

echo.
echo 터널이 닫혔습니다. 서버를 끕니다...
powershell -NoProfile -Command "$ids = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -like 'python*' }; foreach ($id in $ids) { taskkill /PID $id /T /F | Out-Null }"
echo 서버를 껐습니다.
pause
