# WSL 자동 웨이크업 + 절전 방지 태스크 등록
# 실행 방법: 관리자 권한 PowerShell에서 실행
#   우클릭 → "관리자 권한으로 실행" → cd 경로 → .\setup_wakeup.ps1

# ── 1. WSL keepalive 태스크 (8:44 기상 후 keepalive 백그라운드 실행) ──
$actionKeep = New-ScheduledTaskAction `
    -Execute 'wsl.exe' `
    -Argument '-d Ubuntu -u root -- bash /home/hyunho/openclaw-setup/python-only/keepalive.sh'

$triggerKeep = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At '08:44AM'

$settingsKeep = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask `
    -TaskName 'WSL_Trading_Keepalive' `
    -Action $actionKeep `
    -Trigger $triggerKeep `
    -Settings $settingsKeep `
    -RunLevel Highest `
    -Force

Write-Host "Done: WSL_Trading_Keepalive registered (weekdays 08:44, runs until 15:35)"

# ── 2. 절전 방지 태스크 (장 시작 전 절전 끄기) ──
$actionCafe = New-ScheduledTaskAction `
    -Execute 'powercfg.exe' `
    -Argument '/change standby-timeout-ac 0'

$triggerCafe = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At '08:43AM'

$settingsCafe = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun

Register-ScheduledTask `
    -TaskName 'WSL_Trading_NoSleep' `
    -Action $actionCafe `
    -Trigger $triggerCafe `
    -Settings $settingsCafe `
    -RunLevel Highest `
    -Force

Write-Host "Done: WSL_Trading_NoSleep registered (AC 절전 비활성화)"

# ── 3. 절전 복구 태스크 (장 마감 후 절전 다시 켜기) ──
$actionResume = New-ScheduledTaskAction `
    -Execute 'powercfg.exe' `
    -Argument '/change standby-timeout-ac 30'

$triggerResume = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At '15:40PM'

Register-ScheduledTask `
    -TaskName 'WSL_Trading_ResumeSleep' `
    -Action $actionResume `
    -Trigger $triggerResume `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) `
    -RunLevel Highest `
    -Force

Write-Host "Done: WSL_Trading_ResumeSleep registered (15:40 절전 복구 30분)"
Write-Host ""
Write-Host "=== 등록 완료 ==="
Write-Host "08:43  절전 비활성화"
Write-Host "08:44  WSL keepalive 시작 (cron 감시, 15:35 자동 종료)"
Write-Host "15:40  절전 다시 30분으로 복구"
