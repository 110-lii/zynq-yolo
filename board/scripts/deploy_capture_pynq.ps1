# =============================================================================
# deploy_capture_pynq.ps1 -- PC 侧：把 M1 采集链的产物推到板子并（可选）当场跑验收
#
# 跑在哪：Windows PC（pwsh）。板子：领航者 ZYNQ 7020 / PYNQ 2.7（SD 启动，Linux 在跑）。
#
# 为什么单独一个脚本、不复用 deploy_to_board.ps1：
#   deploy_to_board.ps1 是给 conv_engine 单层验收用的（打 tar、带用例 bin）；
#   本脚本只推 M1 采集链要的 4 个文件（overlay 两件 + 板侧脚本两件），秒级完成。
#
# Windows 上的免密问题（实测踩过）：
#   本机只有 OpenSSH，没有 sshpass/pscp/paramiko ⇒ 密码只能在**交互式终端**里敲。
#   可行办法是 OpenSSH 的 SSH_ASKPASS（9.5p2 实测可用）：写一个只输出密码的 .cmd，
#   设 SSH_ASKPASS + SSH_ASKPASS_REQUIRE=force，ssh/scp 就会自动取密码。
#   ⚠️ 这会把板子密码以明文放在临时文件里（本机私有环境可接受）；上板脚本不进 git 的
#      凭据，只有本脚本运行时才生成。
#
# 用法（密码从环境变量 ZYNQ_BOARD_PASS 或 -BoardPass 传入，不写入仓库）：
#   $env:ZYNQ_BOARD_PASS = "<板端密码>"
#   & .\deploy_capture_pynq.ps1 -BoardHost 192.168.2.99                    # 只推送
#   & .\deploy_capture_pynq.ps1 -BoardHost 192.168.2.99 -Run -TestArgs "--check"
#   & .\deploy_capture_pynq.ps1 -BoardHost 192.168.2.99 -Run -TestArgs "--bench 20"
#
# 判据令牌（repo_spec §5：看令牌，不看退出码）：
#   DEPLOY_CAPTURE_OK <n_files> <remote_dir>
#   DEPLOY_CAPTURE_FAIL <reason>
#   -Run 时把板侧输出原样透传，末行仍是板侧的 CAPTURE_BOARD_OK / CAPTURE_BENCH_OK / CAPTURE_BOARD_FAIL
# =============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BoardHost,
    [string]$BoardUser = "xilinx",
    # 不写默认密码：从环境变量或 -BoardPass 传入（避免把凭据提交进仓库）
    [string]$BoardPass = $env:ZYNQ_BOARD_PASS,
    [string]$BoardDir  = "/home/xilinx/zynq-yolo",
    [switch]$Run,
    # ★ 不要用 ValueFromRemainingArguments 收 `--check`：PowerShell 5.1 会把 `--check`
    #   当成参数名解析，最终传给 ssh 的是个裸 `-`，报 "ssh: unknown option -- -"（实测）。
    #   改成显式字符串：-TestArgs "--check" 或 -TestArgs "--bench 20"。
    [string]$TestArgs = "--check"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($BoardPass)) {
    Write-Output "DEPLOY_CAPTURE_FAIL BoardPass_missing (set ZYNQ_BOARD_PASS or pass -BoardPass)"
    exit 1
}

$RepoRoot = Split-Path -Parent $PSScriptRoot | Split-Path -Parent   # board/scripts -> repo root
$Files = @(
    @{ Local = "board\overlay\capture_chain.bit";  Remote = "$BoardDir/board/overlay/" },
    @{ Local = "board\overlay\capture_chain.hwh";  Remote = "$BoardDir/board/overlay/" },
    @{ Local = "src\ps\host\run_capture_test.py";  Remote = "$BoardDir/src/ps/host/" },
    @{ Local = "board\scripts\run_capture_test.sh"; Remote = "$BoardDir/board/scripts/" }
)

foreach ($f in $Files) {
    $p = Join-Path $RepoRoot $f.Local
    if (-not (Test-Path $p)) {
        Write-Output "DEPLOY_CAPTURE_FAIL missing_local $($f.Local)"
        exit 1
    }
}

# ---- ssh/scp 非交互封装（SSH_ASKPASS 提供密码）------------------------------
$AskPass = Join-Path $env:TEMP ("askpass_" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".cmd")
Set-Content -Path $AskPass -Value "@echo $BoardPass" -Encoding ascii
$env:SSH_ASKPASS = $AskPass
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "localhost:0"

$SshOpts = @(
    "-o", "StrictHostKeyChecking=no",
    "-o", "PreferredAuthentications=password",
    "-o", "PubkeyAuthentication=no",
    "-o", "ConnectTimeout=15"
)
$Target = "$BoardUser@$BoardHost"

function Invoke-Ssh([string]$cmd) {
    # ★ PowerShell 5.1 会把原生命令的 stderr 当成 ErrorRecord；本脚本 ErrorActionPreference=Stop
    #   时，「sudo 的密码提示」这类 stderr 会直接把脚本打断（实测：ssh.exe : [sudo] password ...）。
    #   所以在这里临时降级为 Continue，只按内容/退出码判断。
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & ssh @SshOpts $Target $cmd 2>&1
        return ($out | Out-String)
    }
    finally {
        $ErrorActionPreference = $old
    }
}

try {
    # ---- 1) 连通性 + 建目录 --------------------------------------------------
    $mkdirCmd = "mkdir -p '$BoardDir/board/overlay' '$BoardDir/board/scripts' '$BoardDir/board/logs' '$BoardDir/src/ps/host' && echo MKDIR_OK"
    $mk = Invoke-Ssh $mkdirCmd
    if ($mk -notmatch "MKDIR_OK") {
        Write-Output ("DEPLOY_CAPTURE_FAIL ssh_mkdir: " + ($mk.Trim() -replace "`r?`n", " | "))
        exit 1
    }

    # ---- 2) 逐文件 scp -------------------------------------------------------
    $n = 0
    foreach ($f in $Files) {
        $src = Join-Path $RepoRoot $f.Local
        $dst = "${Target}:$($f.Remote)"
        $out = & scp @SshOpts $src $dst 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Output ("DEPLOY_CAPTURE_FAIL scp " + $f.Local + ": " + (($out | Out-String).Trim()))
            exit 1
        }
        $n++
    }
    $probe = Invoke-Ssh "ls -l '$BoardDir/board/overlay/capture_chain.bit' '$BoardDir/src/ps/host/run_capture_test.py' | wc -l"
    if ($probe -notmatch "\b2\b") {
        Write-Output ("DEPLOY_CAPTURE_FAIL remote_listing_unexpected: " + ($probe.Trim() -replace "`r?`n", " | "))
        exit 1
    }
    Write-Output "DEPLOY_CAPTURE_OK $n $BoardDir"

    # ---- 3) 可选：当场跑验收 -------------------------------------------------
    if ($Run) {
        $argline = $TestArgs.Trim()
        $runCmd = "bash '$BoardDir/board/scripts/run_capture_test.sh' $argline"
        Write-Output "RUN_REMOTE $runCmd"
        $out = Invoke-Ssh $runCmd
        Write-Output $out.TrimEnd()
    }
}
finally {
    Remove-Item -LiteralPath $AskPass -Force -ErrorAction SilentlyContinue
}