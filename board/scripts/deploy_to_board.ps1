# =============================================================================
# deploy_to_board.ps1 -- PC 侧：打包仓库真源 + 数据，推到板子并解包
#
# 跑在哪：Windows PC（pwsh）。板子：正点原子领航者 ZYNQ 7020 / PYNQ 2.7。
#
# 用法：
#   & .\deploy_to_board.ps1 -BoardHost 192.168.1.99 -BoardUser xilinx
#   可选：-BoardDir /home/xilinx/zynq-yolo   （板上落点，默认即此）
#         -TarName deploy                    （tar 文件名，不带扩展名）
#
# 判据（repo_spec §5 精神：令牌，不看中间输出）：
#   末行 DEPLOY_OK <tar 字节数> <远程文件数>
#   任一环节失败：DEPLOY_FAIL <原因>，退出码 1。
#
# 为什么不用 git clone 上板：
#   ① 板上仓库只需运行真源，不需要 .git 历史（省几十 MB）；
#   ② data/layers/cases/*.bin 被 .gitignore 排除（60 行 *.bin），clone 后用例不完整，
#      必须从本地磁盘直接打包；
#   ③ scp 一次传一个 tar 比递归传几十个小文件快得多（板上是 SD 卡 rootfs）。
# =============================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BoardHost,
    [string]$BoardUser = "xilinx",
    [string]$BoardDir  = "/home/xilinx/zynq-yolo",
    [string]$TarName   = "deploy"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot | Split-Path -Parent   # board/scripts -> repo root
$Stage    = Join-Path $env:TEMP ("zynq_yolo_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$TarPath  = Join-Path $RepoRoot ("build", "$TarName.tar")

# ---- 1) 本地产物齐套检查（少一个就别浪费一次 scp） ---------------------------
$Required = @(
    "board\overlay\conv_engine.bit",
    "board\overlay\conv_engine.hwh",
    "src\ps\pynq_driver\conv_engine.py",
    "src\ps\host\run_layer_test.py",
    "board\scripts\run_board_test.sh",
    "data\layers\cases\k3s1_c3_40\meta.txt"
)
$Missing = @($Required | Where-Object { -not (Test-Path (Join-Path $RepoRoot $_)) })
if ($Missing.Count -gt 0) {
    Write-Output ("DEPLOY_FAIL missing_files: " + ($Missing -join ", "))
    exit 1
}

# 用例 .bin 是被 .gitignore 排除的运行必需件，逐用例验证四件套齐全
$CasesRoot = Join-Path $RepoRoot "data\layers\cases"
$Cases = Get-ChildItem $CasesRoot -Directory | Sort-Object Name
foreach ($c in $Cases) {
    foreach ($f in @("meta.txt", "in.bin", "w.bin", "params.bin", "lut.bin", "out_ref.bin")) {
        if (-not (Test-Path (Join-Path $c.FullName $f))) {
            Write-Output ("DEPLOY_FAIL incomplete_case: " + $c.Name + "\" + $f)
            exit 1
        }
    }
}
Write-Output ("CHECK local files OK: overlay + driver + " + $Cases.Count + " cases complete")

# ---- 2) 临时目录拼包（结构 = 板上落点结构） -----------------------------------
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
$Include = @(
    "src\ps",
    "board\overlay",
    "board\scripts",
    "data\layers\cases",
    "docs\accel_interface.md"
)
foreach ($rel in $Include) {
    $src = Join-Path $RepoRoot $rel
    $dst = Join-Path $Stage $rel
    $dstParent = Split-Path -Parent $dst
    if (-not (Test-Path $dstParent)) { New-Item -ItemType Directory -Path $dstParent -Force | Out-Null }
    Copy-Item $src $dst -Recurse -Force
}
Copy-Item (Join-Path $RepoRoot ".gitattributes") $Stage -Force

# 排除 __pycache__ / .pyc（板上禁入，check d 同口径）
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
Get-ChildItem $Stage -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force

# ---- 3) tar（Windows 10+ 自带 bsdtar；LF 由 .gitattributes 保证源文件口径） ----
if (Test-Path $TarPath) { Remove-Item $TarPath -Force }
tar -cf $TarPath -C $Stage .
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $TarPath)) {
    Write-Output "DEPLOY_FAIL tar_create"
    exit 1
}
$TarBytes = (Get-Item $TarPath).Length
Write-Output ("TAR $TarName.tar $TarBytes bytes")

# ---- 4) scp 推板 ----------------------------------------------------------------
& scp -O "$TarPath" "${BoardUser}@${BoardHost}:/tmp/${TarName}.tar"
if ($LASTEXITCODE -ne 0) {
    Write-Output "DEPLOY_FAIL scp"
    exit 1
}

# ---- 5) 远程解包（ssh 一条命令；-O 防止落到 sftp 的旧实现差异） ----------------
$RemoteCmd = "mkdir -p $BoardDir && tar -xf /tmp/${TarName}.tar -C $BoardDir && " +
             "find $BoardDir -type f | wc -l"
$Remote = & ssh -O "${BoardUser}@${BoardHost}" $RemoteCmd
if ($LASTEXITCODE -ne 0) {
    Write-Output "DEPLOY_FAIL remote_untar"
    exit 1
}

Remove-Item $Stage -Recurse -Force
Write-Output ("DEPLOY_OK $TarBytes " + ($Remote | Select-Object -Last 1))
exit 0
