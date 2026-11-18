#Requires -Version 5.1
<#
.SYNOPSIS
    OfferFit 环境自检：工具链、浏览器、GitHub 连通性、磁盘、运行目录。
.DESCRIPTION
    只读检查，不改系统配置。GitHub 检查使用 openssl backend（本机 schannel 不可用）。
    兼容 Windows PowerShell 5.1（本机默认 shell）。
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/check-env.ps1
#>

$root = Split-Path -Parent $PSScriptRoot
$rows = New-Object System.Collections.Generic.List[object]

function Add-Row {
    param([string]$Item, [string]$Status, [string]$Detail)
    $rows.Add([pscustomobject]@{ Item = $Item; Status = $Status; Detail = $Detail })
}

function Get-CmdVersion {
    param([string]$Exe, [string[]]$ExeArgs = @('--version'))
    try {
        $out = & $Exe @ExeArgs 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0 -or -not $out) { return $null }
        return ([string](@($out)[0])).Trim()
    } catch { return $null }
}

# ── 1. 工具链 ─────────────────────────────────────────────
$git = Get-CmdVersion 'git'
if ($git) { Add-Row 'git' 'OK' $git } else { Add-Row 'git' 'MISSING' '未安装：无法管理版本与克隆参考库' }

$py = Get-CmdVersion 'python'
if ($py) { Add-Row 'python' 'OK' $py } else { Add-Row 'python' 'MISSING' '未安装' }

$node = Get-CmdVersion 'node'
if ($node) { Add-Row 'node' 'OK' $node } else { Add-Row 'node' 'WARN' '未安装（非必需）' }

# ── 2. 浏览器 ─────────────────────────────────────────────
$browser = $null
$candidates = @(
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Google\Chrome\Application\chrome.exe'
)
foreach ($c in $candidates) { if (Test-Path $c) { $browser = $c; break } }
if ($browser) { Add-Row '浏览器' 'OK' $browser } else { Add-Row '浏览器' 'WARN' '未找到 Edge/Chrome，截图与端到端验证会受限' }

# ── 3. GitHub 连通性（openssl backend）────────────────────
$env:GIT_TERMINAL_PROMPT = '0'
try {
    $out = & git -c http.sslBackend=openssl ls-remote --heads https://github.com/octocat/Hello-World.git 2>&1
    if ($LASTEXITCODE -eq 0 -and $out) {
        Add-Row 'GitHub（openssl）' 'OK' 'ls-remote 成功，可克隆参考库'
    } else {
        Add-Row 'GitHub（openssl）' 'FAIL' (($out | Out-String).Trim())
    }
} catch {
    Add-Row 'GitHub（openssl）' 'FAIL' $_.Exception.Message
}

try {
    $null = & git ls-remote --heads https://github.com/octocat/Hello-World.git 2>&1
    if ($LASTEXITCODE -eq 0) {
        Add-Row 'GitHub（默认 schannel）' 'OK' '默认配置也可用'
    } else {
        Add-Row 'GitHub（默认 schannel）' 'WARN' '默认 backend 不可用（预期），脚本已改用 openssl'
    }
} catch {
    Add-Row 'GitHub（默认 schannel）' 'WARN' '默认 backend 不可用（预期），脚本已改用 openssl'
}

# ── 4. 磁盘 ───────────────────────────────────────────────
try {
    $drive = [System.IO.DriveInfo]::new('C')
    $freeGb = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    if ($freeGb -gt 2) { Add-Row '磁盘 C:' 'OK' ("可用 {0} GB" -f $freeGb) } else { Add-Row '磁盘 C:' 'WARN' ("可用仅 {0} GB" -f $freeGb) }
} catch {
    Add-Row '磁盘 C:' 'WARN' '无法读取（不影响使用）'
}

# ── 5. 工作区可写 ─────────────────────────────────────────
try {
    $probe = Join-Path $root '.env-check.tmp'
    [System.IO.File]::WriteAllText($probe, 'ok', (New-Object System.Text.UTF8Encoding($false)))
    Remove-Item $probe -Force
    Add-Row '工作区可写' 'OK' $root
} catch {
    Add-Row '工作区可写' 'FAIL' $_.Exception.Message
}

# ── 输出 ──────────────────────────────────────────────────
Write-Host ''
Write-Host '── OfferFit 环境自检 ──' -ForegroundColor Cyan
$rows | Format-Table -AutoSize -Wrap | Out-String | Write-Host

$fails = @($rows | Where-Object { $_.Status -eq 'FAIL' })
if ($fails.Count -gt 0) {
    Write-Host ("有 {0} 项失败，见上表。" -f $fails.Count) -ForegroundColor Red
    exit 1
}
Write-Host '全部关键项通过。' -ForegroundColor Green
exit 0
