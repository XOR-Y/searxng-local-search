# stop.ps1 —— 停止 SearXNG（按命令行匹配 run.py 的 python 进程）
# 注意：自动拉起走的是 pythonw.exe（无窗口），手动 run.bat 走的是 python.exe —— 两者都要匹配
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'run\.py' }

if ($procs) {
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "已停止 SearXNG  (PID $($p.ProcessId))"
    }
} else {
    Write-Host "没有找到正在运行的 SearXNG"
}
