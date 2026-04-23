# ============================================================
#  iOA 非PE文件频率过滤 测试脚本 v4 (终极兜底版)
#  所有步骤同时写入 %USERPROFILE%\Desktop\test_log.txt
#  即使闪退, 也能看日志定位问题
# ============================================================

$LogFile = Join-Path $env:USERPROFILE 'Desktop\test_log.txt'
"=== 脚本启动 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $LogFile -Encoding UTF8 -Force

function Log {
    param([string]$Msg, [string]$Color = 'White')
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $Msg"
    try { Write-Host $line -ForegroundColor $Color } catch { Write-Output $line }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Pause-Any {
    Log '按回车继续 (或等 30 秒自动继续)...' 'Green'
    try {
        # 先试 ReadKey, 某些终端不支持就 fallback
        if ($Host.UI.RawUI -and $Host.Name -ne 'Windows PowerShell ISE Host') {
            [void]$Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
        } else {
            Read-Host | Out-Null
        }
    } catch {
        Log "ReadKey 失败, 改用 Read-Host: $_" 'DarkYellow'
        try { Read-Host | Out-Null } catch { Start-Sleep -Seconds 30 }
    }
}

function Make-Files {
    param([string]$Dir, [int]$Count, [string]$Prefix, [string]$Ext = '')
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
    for ($i = 1; $i -le $Count; $i++) {
        $name = if ($Ext) { "${Prefix}_$i.$Ext" } else { "${Prefix}_$i" }
        $path = Join-Path $Dir $name
        try {
            [System.IO.File]::WriteAllText($path, "data_$i", [System.Text.Encoding]::ASCII)
        } catch {
            Log "写文件失败 $path : $_" 'Red'
        }
    }
}

try {
    Log '============================================================' 'Cyan'
    Log ' iOA 非PE文件频率过滤测试脚本 v4' 'Cyan'
    Log '============================================================' 'Cyan'
    Log "PID          : $PID" 'Magenta'
    Log "PS 版本      : $($PSVersionTable.PSVersion)" 'White'
    Log "OS           : $([System.Environment]::OSVersion.VersionString)" 'White'
    Log "用户         : $env:USERNAME" 'White'
    Log "TEMP         : $env:TEMP" 'White'
    Log "日志文件     : $LogFile" 'White'
    Log ''

    # 检查 TEMP 目录可写
    $testWrite = Join-Path $env:TEMP "__write_test_$PID.tmp"
    try {
        Set-Content -Path $testWrite -Value 'ok' -Encoding ASCII
        Remove-Item $testWrite -Force
        Log 'TEMP 目录可写: OK' 'Green'
    } catch {
        Log "TEMP 目录不可写: $_" 'Red'
        throw
    }

    $TestDir = Join-Path $env:TEMP 'nonpe_freq_test'
    Log "测试目录: $TestDir"
    if (Test-Path $TestDir) { Remove-Item $TestDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $TestDir -Force | Out-Null
    Log '测试目录已准备就绪' 'Green'
    Log ''
    Pause-Any

    # ---------------- 场景1 ----------------
    Log '[场景1] 200 个无后缀文件' 'Yellow'
    Make-Files -Dir "$TestDir\scene1" -Count 200 -Prefix 'datafile'
    Log '  已创建 200 个无后缀文件' 'Green'
    Log '  预期: [NonPeFreq] learned 50 + filtering'
    Pause-Any

    # ---------------- 场景2 ----------------
    Log '[场景2] 200 个非PE后缀 (.dat/.ini/.log)' 'Yellow'
    Make-Files -Dir "$TestDir\scene2" -Count 70 -Prefix 'config'  -Ext 'dat'
    Make-Files -Dir "$TestDir\scene2" -Count 70 -Prefix 'setting' -Ext 'ini'
    Make-Files -Dir "$TestDir\scene2" -Count 60 -Prefix 'app'     -Ext 'log'
    Log '  已创建 200 个非PE后缀文件' 'Green'
    Log '  预期: [NonPeFreq] learned 50 + filtering'
    Pause-Any

    # ---------------- 场景3 ----------------
    Log '[场景3] 40 个 PE 后缀 (.exe/.dll)' 'Yellow'
    Make-Files -Dir "$TestDir\scene3" -Count 20 -Prefix 'app' -Ext 'exe'
    Make-Files -Dir "$TestDir\scene3" -Count 20 -Prefix 'lib' -Ext 'dll'
    Log '  已创建 40 个PE后缀文件' 'Green'
    Log '  预期: 无 [NonPeFreq] 日志'
    Pause-Any

    # ---------------- 场景4 ----------------
    Log '[场景4] 10 个无后缀文件' 'Yellow'
    Make-Files -Dir "$TestDir\scene4" -Count 10 -Prefix 'small'
    Log '  已创建 10 个无后缀文件' 'Green'
    Log '  预期: 无过滤日志 (数量不足50)'
    Pause-Any

    # ---------------- 场景5 ----------------
    Log '[场景5] 300 个混合后缀' 'Yellow'
    Make-Files -Dir "$TestDir\scene5" -Count 100 -Prefix 'file' -Ext 'txt'
    Make-Files -Dir "$TestDir\scene5" -Count 100 -Prefix 'file' -Ext 'doc'
    Make-Files -Dir "$TestDir\scene5" -Count 100 -Prefix 'file'
    Log '  已创建 300 个混合后缀文件' 'Green'
    Log '  预期: [PidScan] total=200 nosuffix=xx'
    Pause-Any

    # ---------------- 场景6 ----------------
    Log '[场景6] 窗口过期重置' 'Yellow'
    Make-Files -Dir "$TestDir\scene6" -Count 80 -Prefix 'phase1'
    Log '  已写入 80 个 (进入过滤期)' 'Green'
    Log '  等待 65 秒让窗口过期...' 'DarkYellow'
    for ($s = 65; $s -ge 1; $s--) {
        try { Write-Host ("`r  倒计时: {0,2} 秒  " -f $s) -NoNewline -ForegroundColor DarkYellow } catch {}
        Start-Sleep -Seconds 1
    }
    Log ''
    Make-Files -Dir "$TestDir\scene6" -Count 30 -Prefix 'phase2'
    Log '  窗口过期后再写入 30 个' 'Green'
    Log '  预期: [NonPeFreq] filter window expired + re-entering learn phase'

    Log ''
    Log '============================================================' 'Cyan'
    Log ' 所有场景测试完成' 'Cyan'
    Log ' DebugView 搜索: [NonPeFreq]   [PidScan]' 'Cyan'
    Log '============================================================' 'Cyan'
    Log "记住这个 PID: $PID (用于 verify 脚本 --pid 参数)" 'Magenta'
    Log ''
    Log '按回车清理测试文件...' 'Green'
    Pause-Any

    Remove-Item $TestDir -Recurse -Force -ErrorAction SilentlyContinue
    Log '测试文件已清理' 'Green'
}
catch {
    Log '==================== 发生异常 ====================' 'Red'
    Log "错误: $($_.Exception.Message)" 'Red'
    Log "位置: $($_.InvocationInfo.PositionMessage)" 'Red'
    Log "堆栈: $($_.ScriptStackTrace)" 'DarkRed'
}
finally {
    Log ''
    Log "完整日志已保存到: $LogFile" 'Cyan'
    Log '按回车关闭窗口...' 'Green'
    try {
        Read-Host | Out-Null
    } catch {
        Start-Sleep -Seconds 60
    }
}
