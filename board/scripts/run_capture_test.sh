#!/bin/bash
# =============================================================================
# run_capture_test.sh -- 板上 M1 采集链验收（PYNQ 路径，真板）
#
# 跑在哪：板子（领航者 ZYNQ 7020 / PYNQ 2.7，用户 xilinx，密码 xilinx）
# 依赖：board/overlay/capture_chain.bit + .hwh、src/ps/host/run_capture_test.py
#       都必须先由 PC 侧 scp 到 /home/xilinx/zynq-yolo（见 board/scripts/README.md）
#
# 为什么要 sudo + PYTHONPATH：
#   ① PYNQ 要 root 才能 mmap /dev/mem、用 CMA；
#   ② 本镜像的 pynq 包装在 /home/xilinx/pynq（不在 /usr/lib 里），sudo 重置环境后
#      root 的 sys.path 不含 /home/xilinx ⇒ 必须显式 PYTHONPATH=/home/xilinx，
#      否则报 "No module named 'pynq'"（2026-10-20 实测踩到，不是 pynq 没装）。
#   ③ sudo 需要密码：用 `sudo -S` 从 stdin 喂（本镜像 xilinx 用户在 sudoers 里，
#      但 sudoers 未设 NOPASSWD）。
#
# 用法（板上，仓库根目录或任意目录；密码不写入仓库）：
#   export BOARD_PW='<板端密码>'
#   bash board/scripts/run_capture_test.sh --check
#   bash board/scripts/run_capture_test.sh --bench 20
#
# 判据令牌（与 PC 侧 jtag_capture_test.tcl 同一组语义）：
#   CAPTURE_BOARD_OK ...    / CAPTURE_BENCH_OK ...   / CAPTURE_BOARD_FAIL <reason>
# =============================================================================
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BOARD_PW="${BOARD_PW:-}"
PY="${PY:-python3}"
PYTHONPATH_DIR="${PYTHONPATH_DIR:-/home/xilinx}"

echo "REPO_ROOT=${REPO_ROOT}"
echo "PY=${PY}  PYTHONPATH=${PYTHONPATH_DIR}"

# 不把默认密码写进仓库：由调用方 export BOARD_PW=...
if [ -z "${BOARD_PW}" ]; then
  echo "CAPTURE_BOARD_FAIL BOARD_PW_missing (export BOARD_PW=...)"
  exit 1
fi

if [ ! -f "${REPO_ROOT}/board/overlay/capture_chain.bit" ]; then
  echo "CAPTURE_BOARD_FAIL bitfile_missing ${REPO_ROOT}/board/overlay/capture_chain.bit"
  exit 1
fi
if [ ! -f "${REPO_ROOT}/src/ps/host/run_capture_test.py" ]; then
  echo "CAPTURE_BOARD_FAIL test_script_missing"
  exit 1
fi

# sudo -S 从 stdin 读密码；用 **bash -lc（login shell）** 让 /etc/profile.d/ 里的
# pynq_venv.sh + xrt_setup.sh 生效 —— 否则 sudo 重置环境后 XRT 环境缺失，
# PYNQ 报 "No devices found, is the XRT environment sourced?"（2026-10-20 实测）。
# ★ 必须 cd 到仓库根：测试脚本的默认 --bitfile 是相对路径，ssh 登录后的 cwd 是
#   /home/xilinx，不 cd 就会报 bitfile_missing（同轮实测踩到）。
BITFILE_ABS="${REPO_ROOT}/board/overlay/capture_chain.bit"
TEST_PY="${REPO_ROOT}/src/ps/host/run_capture_test.py"
CMD="cd '${REPO_ROOT}' && python3 '${TEST_PY}' --bitfile '${BITFILE_ABS}' $*"
echo "${BOARD_PW}" | sudo -S bash -lc "${CMD}"
rc=$?
echo "CAPTURE_BOARD_EXIT ${rc}"
exit ${rc}