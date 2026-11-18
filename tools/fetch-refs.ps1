#Requires -Version 5.1
<#
.SYNOPSIS
    按 refs/manifest.json 批量克隆 / 更新 GitHub 参考仓库（浅克隆）。

.DESCRIPTION
    本机 git 默认走 Windows schannel，访问 GitHub 会报 SEC_E_NO_CREDENTIALS。
    本脚本统一使用 -c http.sslBackend=openssl，已实测可正常 clone。
    成功后把 manifest 中对应条目 status 更新为 cloned，并记录 last_fetched。
    兼容 Windows PowerShell 5.1（本机默认 shell）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/fetch-refs.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/fetch-refs.ps1 -Update

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/fetch-refs.ps1 -Only owner__repo
#>
[CmdletBinding()]
param(
    [switch]$Update,
    [string[]]$Only = @()
)

$ErrorActionPreference = 'Stop'
$env:GIT_TERMINAL_PROMPT = '0'

$root         = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $root 'refs\manifest.json'
$reposDir     = Join-Path $root 'refs\repos'
$logPath      = Join-Path $root 'refs\fetch-log.txt'
$utf8NoBom    = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path $manifestPath)) {
    Write-Host "找不到 manifest：$manifestPath" -ForegroundColor Red
    exit 2
}

New-Item -ItemType Directory -Force -Path $reposDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null

function Write-Log {
    param([string]$Message, [string]$Color = 'Gray')
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    [System.IO.File]::AppendAllText($logPath, $line + "`r`n", $utf8NoBom)
    Write-Host $line -ForegroundColor $Color
}

function Set-Prop {
    param($Object, [string]$Name, $Value)
    if ($null -eq $Object.PSObject.Properties[$Name]) {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    } else {
        $Object.$Name = $Value
    }
}

function Invoke-Git {
    param([string[]]$Arguments)
    # PS5.1 下 $ErrorActionPreference='Stop' 会把 git 写入 stderr 的进度行当成终止错误，
    # 这里临时降为 Continue，只以 $LASTEXITCODE 判定成败。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & git @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return @{ ExitCode = $code; Output = (($output | Out-String).Trim()) }
}

$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $manifest.repos) {
    $manifest | Add-Member -NotePropertyName repos -NotePropertyValue @() -Force
}
$repos = @($manifest.repos)

if ($repos.Count -eq 0) {
    Write-Host ''
    Write-Host 'manifest 目前为空 —— 先把概念文档里的 GitHub 项目加进 refs/manifest.json。' -ForegroundColor Yellow
    Write-Host '格式见 refs/README.md，或把链接告诉我，我来加。'
    exit 0
}

$ok = 0; $failed = 0; $skipped = 0

foreach ($repo in $repos) {
    $name = [string]$repo.name
    if ([string]::IsNullOrWhiteSpace($name) -and $repo.url) {
        $parts = ($repo.url -replace '\.git$', '') -split '/'
        $name = ($parts[-2..-1] -join '__')
    }
    if ([string]::IsNullOrWhiteSpace($name)) {
        Write-Log ("条目缺少 name 与 url，跳过：{0}" -f ($repo | ConvertTo-Json -Compress)) 'Red'
        $failed++
        continue
    }
    if ($Only.Count -gt 0 -and ($Only -notcontains $name)) { continue }

    $url  = [string]$repo.url
    $dest = Join-Path $reposDir $name

    if ([string]::IsNullOrWhiteSpace($url)) {
        Write-Log "跳过（待人工确认地址）：$name" 'DarkYellow'
        $skipped++
        continue
    }

    try {
        if (Test-Path $dest) {
            if ($Update) {
                Write-Log "更新 $name ..."
                $r = Invoke-Git @('-C', $dest, '-c', 'http.sslBackend=openssl', 'fetch', '--depth', '1', 'origin')
                if ($r.ExitCode -ne 0) { throw "git fetch 失败：$($r.Output)" }
                $r = Invoke-Git @('-C', $dest, '-c', 'http.sslBackend=openssl', 'reset', '--hard', 'FETCH_HEAD')
                if ($r.ExitCode -ne 0) { throw "git reset 失败：$($r.Output)" }
            } else {
                Write-Log "已存在，跳过 $name（加 -Update 可更新）" 'DarkYellow'
                if ($repo.status -ne 'noted') { Set-Prop $repo 'status' 'cloned' }
                if (-not $repo.last_fetched) { Set-Prop $repo 'last_fetched' (Get-Date -Format 'yyyy-MM-dd') }
                $skipped++
                continue
            }
        } else {
            Write-Log "克隆 $name <- $url"
            $r = Invoke-Git @('-c', 'http.sslBackend=openssl', 'clone', '--depth', '1', '--single-branch', $url, $dest)
            if ($r.ExitCode -ne 0) { throw "git clone 失败：$($r.Output)" }
        }

        if ($repo.status -ne 'noted') { Set-Prop $repo 'status' 'cloned' }
        Set-Prop $repo 'last_fetched' (Get-Date -Format 'yyyy-MM-dd')
        Write-Log "完成 $name" 'Green'
        $ok++
    }
    catch {
        Write-Log "失败 $name：$($_.Exception.Message)" 'Red'
        $failed++
    }
}

Set-Prop $manifest 'updated_at' (Get-Date -Format 'yyyy-MM-dd')
$json = $manifest | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($manifestPath, $json, $utf8NoBom)

Write-Host ''
Write-Host ("结果：成功 {0} · 跳过 {1} · 失败 {2}" -f $ok, $skipped, $failed) -ForegroundColor Cyan
Write-Host "日志：$logPath"
Write-Host "仓库：$reposDir"
if ($failed -gt 0) { exit 1 } else { exit 0 }
