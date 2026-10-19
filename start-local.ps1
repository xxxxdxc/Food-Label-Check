param([int]$Port = 5182)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskSrc = Join-Path $taskRoot 'src'
$taskPython = Join-Path $taskSrc '.venv\Scripts\python.exe'
$taskLogs = Join-Path $taskSrc 'data\local-runtime'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '缺少 src/.venv Python 环境，请先按 src/README.md 安装依赖。' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'frontend\dist\index.html'))) { throw '请先在 frontend 目录运行 npm run build。' }
$taskListener = [System.Net.Sockets.TcpClient]::new()
try { $taskListener.Connect('127.0.0.1', $Port); $taskPortUsed = $true } catch { $taskPortUsed = $false } finally { $taskListener.Dispose() }
if ($taskPortUsed) { throw "端口 $Port 已占用，请先检查现有服务，或使用 -Port 指定其他端口。" }
New-Item -ItemType Directory -Force -Path $taskLogs | Out-Null
$env:LOCAL_ONLY = '1'
$env:OCR_MODE = 'live'
$env:VLM_ENABLED = '1'
$taskApi = Start-Process -FilePath $taskPython -ArgumentList @('-B','-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port",'--workers','1','--no-proxy-headers') -WorkingDirectory $taskSrc -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'api.out.log') -RedirectStandardError (Join-Path $taskLogs 'api.err.log')
try {
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 40; $taskAttempt++) {
        $taskApi.Refresh()
        if ($taskApi.HasExited) { throw 'API 启动失败，请查看 src/data/local-runtime/api.err.log。' }
        try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/audits?limit=1" -TimeoutSec 1 -NoProxy | Out-Null; $taskReady = $true; break } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $taskReady) { throw 'API 启动超时，请查看本地日志。' }
    $taskWorker = Start-Process -FilePath $taskPython -ArgumentList @('-B','-m','app.worker') -WorkingDirectory $taskSrc -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'worker.out.log') -RedirectStandardError (Join-Path $taskLogs 'worker.err.log')
    Start-Sleep -Milliseconds 700
    $taskWorker.Refresh()
    if ($taskWorker.HasExited) { throw 'Worker 启动失败，请查看 src/data/local-runtime/worker.err.log。' }
    @{api_pid=$taskApi.Id; worker_pid=$taskWorker.Id; port=$Port; api_started=$taskApi.StartTime.ToString('o'); worker_started=$taskWorker.StartTime.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogs 'processes.json') -Encoding utf8
    Write-Output "工作台已启动：http://127.0.0.1:$Port/（无需登录）"
    Write-Output "API PID: $($taskApi.Id); Worker PID: $($taskWorker.Id)"
} catch {
    if (-not $taskApi.HasExited) { & taskkill.exe /PID $taskApi.Id /T /F | Out-Null }
    throw
}
