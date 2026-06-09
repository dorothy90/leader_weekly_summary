@echo off
REM ============================================================
REM  주간 리포트 파이프라인 런처 (더블클릭 / 작업 스케줄러용)
REM  fetch -> process -> embed -> wiki_build -> wiki_export -> email
REM
REM  사용법:
REM    run_pipeline.bat                 : 이번 주차, 메일 초안만 열기(안전)
REM    run_pipeline.bat 2026-11         : 특정 주차 지정
REM    run_pipeline.bat 2026-11 send    : 특정 주차 + 자동 발송
REM    run_pipeline.bat "" send         : 이번 주차 + 자동 발송
REM ============================================================

setlocal
chcp 65001 >nul

REM 이 배치파일이 있는 폴더(scripts\) 기준으로 ps1 경로 구성
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%run_pipeline.ps1"

REM 인자 파싱: %1 = 주차(선택), %2 = send(선택)
set "WEEK=%~1"
set "EXTRA="
if /I "%~2"=="send"     set "EXTRA=-AutoSend"
if /I "%~1"=="send"     ( set "EXTRA=-AutoSend" & set "WEEK=" )

if "%WEEK%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %EXTRA%
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Week "%WEEK%" %EXTRA%
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [오류] 파이프라인이 실패했습니다. logs\ 폴더의 최신 로그를 확인하세요.
)

REM 스케줄러가 아닌 더블클릭 실행 시 창이 바로 닫히지 않도록 대기
if "%EXTRA%"=="" pause
exit /b %RC%
