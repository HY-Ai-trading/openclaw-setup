# WSL 자동 웨이크업 태스크 등록
# 실행 방법: 관리자 권한 PowerShell에서 실행
#   우클릭 → "관리자 권한으로 실행" → cd 경로 → .\setup_wakeup.ps1

$action = New-ScheduledTaskAction `
    -Execute 'wsl.exe' `
    -Argument '-e /bin/bash -c "service cron status || service cron start"'

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At '08:45AM'

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
    -TaskName 'WSL_Trading_Wakeup' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Done: WSL_Trading_Wakeup registered (weekdays 08:45)"
