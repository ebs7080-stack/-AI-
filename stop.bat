@echo off
chcp 65001 >nul
set PORT=8000

echo 포트 %PORT%번에서 실행 중인 서버를 찾는 중...

rem 안전을 위해 파이썬 프로세스만 종료한다 (다른 프로그램이 같은 포트를 쓰는 경우 건드리지 않음).
powershell -NoProfile -Command "$ids = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName -like 'python*' }; if (-not $ids) { exit 1 }; foreach ($id in $ids) { taskkill /PID $id /T /F | Out-Null }; exit 0"

if errorlevel 1 (
    echo 실행 중인 서버가 없습니다.
) else (
    echo 서버를 껐습니다.
)
pause
