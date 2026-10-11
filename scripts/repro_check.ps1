# scripts/repro_check.ps1 — 공개 후보 폴더만으로 재현 시험 (작업지시서 26 §1-4, Windows).
# 새 가상환경 → requirements-lock 설치 → pytest → CLI → eval_decision 2회 해시 비교.
# 네트워크·.env 불필요. push 하지 않는다. 사용: powershell -File scripts\repro_check.ps1 [DEST]
param([string]$Dest = "..\tap-legalrag-public")

if (-not (Test-Path $Dest)) { Write-Host "[중단] 폴더 없음: $Dest"; exit 2 }
Set-Location $Dest
Write-Host "[repro] 대상: $((Get-Location).Path)"

python -m venv .venv_repro; if (-not $?) { exit 1 }
& .\.venv_repro\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv_repro\Scripts\python.exe -m pip install --quiet -r requirements-lock.txt
if (-not $?) { Write-Host "[실패] 설치"; exit 1 }

Write-Host "[repro] pytest"
& .\.venv_repro\Scripts\python.exe -m pytest -q; if (-not $?) { Write-Host "[실패] pytest"; exit 1 }

Write-Host "[repro] CLI"
$env:PYTHONPATH = "src"
& .\.venv_repro\Scripts\python.exe -m law_rag ask "시행령 제64조 제2항은 무엇을 규정하나요?"
if (-not $?) { Write-Host "[실패] CLI"; exit 1 }

Write-Host "[repro] eval_decision 2회 해시"
& .\.venv_repro\Scripts\python.exe scripts\eval_decision.py *> e3_a.txt
& .\.venv_repro\Scripts\python.exe scripts\eval_decision.py *> e3_b.txt
$ha = (Get-FileHash e3_a.txt).Hash; $hb = (Get-FileHash e3_b.txt).Hash
if ($ha -eq $hb) { Write-Host "[repro] 결정성 OK (2회 동일)" } else { Write-Host "[경고] 2회 출력 다름" }

Write-Host "[repro] 완료 — .venv_repro 는 수동 삭제 가능"
