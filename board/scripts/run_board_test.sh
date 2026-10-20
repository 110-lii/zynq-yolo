#!/bin/bash
# =============================================================================
# run_board_test.sh -- 板上：跑 conv_engine 单层验收并把证据落盘
#
# 跑在哪：领航者 ZYNQ 7020（PYNQ 2.7）。PC 上没有任何分支：这里不模拟。
#
# 用法（板上，任意目录均可，脚本自己定位仓库根）：
#   bash /home/xilinx/zynq-yolo/board/scripts/run_board_test.sh
#   可选环境变量：REPEAT=5   （每用例重复次数，默认 3）
#
# 判据（repo_spec §5：令牌，不看退出码）：
#   末行 BOARD_LAYER_OK all_<n>_cases <elapsed_ns> <gops>
#   失败：BOARD_LAYER_FAIL <reason>
#
# 证据落盘（§4：不可重建的必须落盘）：
#   board/logs/board_layer_test_<YYYYMMDD_HHMM>.log   run_layer_test.py 的完整 stdout
#   board/logs/board_layer_test_<YYYYMMDD_HHMM>.md    同内容的 .md 摘录（git 放行 .md）
#   data/results/board_layer_test.json                机器可读结果（PC 拉回后 commit）
# =============================================================================
set -u

REPEAT="${REPEAT:-3}"

# ---- 定位仓库根（脚本在 <root>/board/scripts/ 下） ---------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PY="python3"
if command -v python3 >/dev/null 2>&1; then :; else
    echo "BOARD_LAYER_FAIL no_python3"
    exit 2
fi

# ---- sudo 自检：PYNQ MMIO / /dev/mem 需要根权限 ------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "NOTE: not root, re-running via sudo (PYNQ needs /dev/mem)"
    exec sudo REPEAT="$REPEAT" bash "$0"
fi

# ---- 齐套检查（缺东西就明确说，不让 python 去猜） ----------------------------
BIT="$REPO_ROOT/board/overlay/conv_engine.bit"
HWH="$REPO_ROOT/board/overlay/conv_engine.hwh"
TEST="$REPO_ROOT/src/ps/host/run_layer_test.py"
for f in "$BIT" "$HWH" "$TEST"; do
    if [ ! -f "$f" ]; then
        echo "BOARD_LAYER_FAIL missing $f"
        exit 2
    fi
done

STAMP="$(date +%Y%m%d_%H%M)"
LOG_DIR="$REPO_ROOT/board/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/board_layer_test_${STAMP}.log"

echo "BOARD_TEST start $(date -u +%Y-%m-%dT%H:%M:%SZ) repeat=$REPEAT"
echo "REPO_ROOT $REPO_ROOT"

# ---- 跑验收（stdout 三写：终端 + .log 全量 + .md 摘录） -----------------------
# .md 与 .log 同内容：git 只放行 board/logs/*.md，原始 .log 留在板上/归档用。
$PY "$TEST" --repeat "$REPEAT" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}

MD="$LOG_DIR/board_layer_test_${STAMP}.md"
{
    echo "# 板上单层验收 conv_layer_test（$STAMP）"
    echo ""
    echo "- 命令: \`python3 src/ps/host/run_layer_test.py --repeat $REPEAT\`"
    echo "- 完整日志: 同名 \`.log\`（板上与 tar 归档）"
    echo "- 判据令牌: BOARD_LAYER_OK / BOARD_LAYER_FAIL"
    echo ""
    echo '```'
    cat "$LOG"
    echo '```'
} > "$MD"

echo "LOG $LOG"
echo "LOG_MD $MD"
echo "JSON $REPO_ROOT/data/results/board_layer_test.json"

# 末行令牌由 run_layer_test.py 已经打印（tee 转发）；这里只透传退出码语义。
# 注意：令牌才是判据，退出码只是 shell 便利。
exit $RC
