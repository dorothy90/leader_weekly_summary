<#
.SYNOPSIS
    주간 리포트 전체 파이프라인 자동 실행 (Windows)

.DESCRIPTION
    fetch -> process -> embed -> wiki_build -> wiki_export -> email
    위 6단계를 순서대로 실행한다. 한 단계라도 실패하면 즉시 중단하고
    로그를 남긴다. logs\pipeline_<주차>_<타임스탬프>.log 에 전체 출력 기록.

.PARAMETER Week
    대상 ISO 주차 (예: 2026-11). 미지정 시 오늘 날짜 기준 자동 계산.

.PARAMETER AutoSend
    지정하면 email 단계에서 Outlook 으로 즉시 자동 발송.
    미지정(기본)이면 메일 초안만 열어 사람이 확인 후 발송.

.PARAMETER SkipFetch
    fetch 단계 건너뛰기 (이미 수집된 데이터로 재실행할 때).

.EXAMPLE
    # 이번 주차 자동 계산, 메일 초안만 열기 (안전, 첫 실행 권장)
    powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1

.EXAMPLE
    # 특정 주차, 자동 발송 (스케줄러용)
    powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -Week 2026-11 -AutoSend
#>

param(
    [string]$Week = "",
    [switch]$AutoSend,
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"

# ========== 경로 설정 ==========
# 이 스크립트(scripts\) 의 상위 폴더 = 프로젝트 루트
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# Python 실행 파일: 가상환경(.venv) 우선, 없으면 시스템 python
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

# ========== 주차 계산 ==========
if ([string]::IsNullOrWhiteSpace($Week)) {
    # fetch_mail.py 의 get_week_string 과 동일한 ISO 주차 규칙 사용
    $Week = & $Python -c "import datetime;i=datetime.date.today().isocalendar();print(f'{i[0]}-{i[1]:02d}')"
    $Week = $Week.Trim()
}

# ========== 로그 설정 ==========
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "pipeline_${Week}_${Stamp}.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# 단계 실행 헬퍼: 실패 시 예외 발생 → 전체 중단
function Invoke-Step {
    param(
        [string]$Name,
        [string[]]$Args
    )
    Write-Log "===== [$Name] 시작 ====="
    Write-Log ("실행: {0} {1}" -f $Python, ($Args -join ' '))

    # 자식 프로세스 출력을 콘솔+로그 양쪽에 기록
    & $Python @Args 2>&1 | Tee-Object -FilePath $LogFile -Append

    if ($LASTEXITCODE -ne 0) {
        Write-Log "❌ [$Name] 실패 (exit code $LASTEXITCODE). 파이프라인 중단."
        throw "Step '$Name' failed with exit code $LASTEXITCODE"
    }
    Write-Log "✅ [$Name] 완료"
}

# ========== 파이프라인 시작 ==========
Write-Log "##############################################"
Write-Log "주간 리포트 파이프라인 시작 | 주차=$Week | 자동발송=$AutoSend"
Write-Log "프로젝트 루트: $RepoRoot"
Write-Log "Python: $Python"
Write-Log "로그 파일: $LogFile"
Write-Log "##############################################"

try {
    # 1) fetch — 메일 수집
    if (-not $SkipFetch) {
        Invoke-Step -Name "fetch" -Args @("fetch_mail.py")
    } else {
        Write-Log "⏭  fetch 단계 건너뜀 (-SkipFetch)"
    }

    # 2) process — 이미지 Vision + 첨부파일 텍스트 추출
    Invoke-Step -Name "process(vision)"      -Args @("process_vision.py")
    Invoke-Step -Name "process(attachment)"  -Args @("process_attachment.py")

    # 3) embed — combined.txt 청킹 → OpenSearch weekly_mail
    Invoke-Step -Name "embed" -Args @("embed_vectordb.py")

    # 4) wiki_build — LLM 요약 → OpenSearch wiki_summaries
    Invoke-Step -Name "wiki_build" -Args @("wiki_builder.py", "--week", $Week)

    # 5) wiki_export — OpenSearch → 마크다운 파일
    Invoke-Step -Name "wiki_export" -Args @("wiki_export.py", "--week", $Week, "--type", "overview")

    # 6) email — overview md → Outlook HTML → Outlook 발송
    $OverviewMd   = Join-Path $RepoRoot "wiki\overview\${Week}_전체요약.md"
    $OverviewHtml = Join-Path $RepoRoot "wiki\overview\${Week}_전체요약.html"

    if (-not (Test-Path $OverviewMd)) {
        Write-Log "❌ overview 마크다운이 없습니다: $OverviewMd (wiki_export 결과 확인 필요)"
        throw "Overview markdown not found: $OverviewMd"
    }

    Invoke-Step -Name "report(html)" -Args @("generate_outlook_report.py", $OverviewMd, "-o", $OverviewHtml)

    $sendArgs = @("send_outlook_report.py", $OverviewHtml)
    if ($AutoSend) { $sendArgs += "--send" }
    Invoke-Step -Name "email" -Args $sendArgs

    Write-Log "##############################################"
    Write-Log "🎉 파이프라인 전체 완료 | 주차=$Week"
    Write-Log "##############################################"
    exit 0
}
catch {
    Write-Log "💥 파이프라인 실패: $($_.Exception.Message)"
    Write-Log "   로그 확인: $LogFile"
    exit 1
}
