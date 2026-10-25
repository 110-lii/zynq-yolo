#!/bin/bash
# =============================================================================
# run_axi_bw.sh -- 板上：纯 AXI 带宽标定（axi_bw overlay）
#
# 用法（板上）：
#   bash /home/xilinx/zynq-yolo/board/scripts/run_axi_bw.sh
# 可选环境变量：MB=8 REPS=4 REPEAT=5
#
# 判据：末行 AXI_BW_OK 4 read32=.. read64=.. write32=.. copy32=..
# =============================================================================
set -u

MB="${MB:-8}"
REPS="${REPS:-4}"
REPEAT="${REPEAT:-5}"

if [ -f /etc/profile.d/xrt_setup.sh ]; then . /etc/profile.d/xrt_setup.sh; fi
if [ -f /etc/profile.d/pynq_venv.sh ]; then . /etc/profile.d/pynq_venv.sh; fi
export PYTHONPATH="/home/xilinx${PYTHONPATH:+:$PYTHONPATH}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "NOTE: not root, re-running via sudo (PYNQ needs /dev/mem)"
    exec sudo MB="$MB" REPS="$REPS" REPEAT="$REPEAT" bash "$0"
fi

for f in "$REPO_ROOT/board/overlay/axi_bw.bit" "$REPO_ROOT/board/overlay/axi_bw.hwh" \
         "$REPO_ROOT/src/ps/host/run_axi_bw.py"; do
    if [ ! -f "$f" ]; then
        echo "AXI_BW_FAIL missing $f"
        exit 2
    fi
done

STAMP="$(date +%Y%m%d_%H%M)"
LOG_DIR="$REPO_ROOT/board/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/board_axi_bw_${STAMP}.log"
MD="$LOG_DIR/board_axi_bw_${STAMP}.md"

echo "AXI_BW start $(date -u +%Y-%m-%dT%H:%M:%SZ) mb=$MB reps=$REPS repeat=$REPEAT"
python3 "$REPO_ROOT/src/ps/host/run_axi_bw.py" --mb "$MB" --reps "$REPS" --repeat "$REPEAT" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}

{
    echo "# 板上 AXI 带宽标定（$STAMP）"
    echo ""
    echo "- 命令: \`python3 src/ps/host/run_axi_bw.py --mb $MB --reps $REPS --repeat $REPEAT\`"
    echo "- 判据令牌: AXI_BW_OK / AXI_BW_FAIL"
    echo ""
    echo '```'
    cat "$LOG"
    echo '```'
} > "$MD"

echo "LOG $LOG"
exit $RC
