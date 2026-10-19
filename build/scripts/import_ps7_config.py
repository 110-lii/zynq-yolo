"""把旧项目的 PS7 配置片段转成本仓库可用的 UTF-8/LF 版本。

为什么需要它
------------
`D:\\FPGA\\oi\\project\\build\\field_judge_ps\\ps7_config.tcl` 是本队**实测能起来**的
Zynq PS7 配置（源自正点原子为领航者 ZYNQ 提供的 PYNQ 2.7 例程，Session 008 实机启动
成功）。PS 配置错一处 PYNQ 就起不来，且现象是"串口零输出"，极难定位 —— 所以这一
571 项 `CONFIG.PCW_*` 必须逐字带过来，绝不能重打。

但原文件有两个问题：
  1) **GBK 编码**（本仓库要求纯 UTF-8，见 ref/pitfalls_from_oi.md P9）；
  2) 里面大量中文注释。

本脚本只做编码/行尾转换与来源标注，**不动任何 `set_property` 行**；转换后校验
`CONFIG.PCW_` 条数与源文件一致，不一致就报错退出（防静默丢行）。

用法
----
    python build/scripts/import_ps7_config.py
"""
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
SRC = Path(r"D:\FPGA\oi\project\build\field_judge_ps\ps7_config.tcl")
DST = REPO / "build" / "scripts" / "ps7_config.tcl"

HEADER = """# =============================================================================
# ps7_config.tcl —— Zynq PS7 逐项配置（571 项 CONFIG.PCW_*）
#
# 来源（逐字复制，未做任何修改）：
#   D:\\FPGA\\oi\\project\\build\\field_judge_ps\\ps7_config.tcl
#   该文件又源自 ZYNQ_7020_PYNQ.zip :: 2_axi_gpio/vivado/axi_gpio/
#     axi_gpio.srcs/sources_1/bd/system/hw_handoff/system_bd.tcl  原第 191..764 行
#   ⇒ 正点原子为领航者 ZYNQ (XC7Z020) 提供的 PYNQ 2.7 例程配置，
#      已在旧项目 Session 008 于本队板卡上实机启动成功（DDR/MIO/时钟配置已实测可用）。
#
# ⚠️ 不要手改这个文件。PS 侧配置错一处，PYNQ 就起不来，且现象是「串口零输出」，
#    极难定位。要改就重新从例程抽取。
#
# 本副本由 build/scripts/import_ps7_config.py 生成：
#   GBK -> UTF-8，CRLF -> LF，其余逐字未改（脚本会校验 CONFIG.PCW_ 条数不变）。
# =============================================================================
"""


def decode_any(raw):
    """先试 UTF-8，再试 GBK。返回 (文本, 用了哪种编码)。

    ★ 这里必须按顺序试：源文件其实**已经是 UTF-8**（只是里面的中文注释本身是
      「UTF-8 字节被当 GBK 读过一次」的乱码，见 ref/pitfalls_from_oi.md P9 的同类坑），
      直接按 GBK 解会把好好的 UTF-8 再搞坏一层。判据是能否无损解码，不是扩展名。
    """
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8/replace"


def main():
    if not SRC.exists():
        print("IMPORT_PS7_FAIL 源文件不存在：%s" % SRC)
        return 1
    raw = SRC.read_bytes()
    txt, enc = decode_any(raw)
    n_src = len(re.findall(r"CONFIG\.PCW_", txt))
    body = txt.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    out = HEADER + body + "\n"
    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)

    # ---- 校验：**只数正文**（HEADER 里也提到 CONFIG.PCW_，第一版忘了排除自己，
    #      于是 571 被数成 573 报了假 FAIL —— 判据必须只覆盖它要证的那部分）----
    n_dst = len(re.findall(rb"CONFIG\.PCW_", body.encode("utf-8")))
    back = DST.read_bytes()
    cr = back.count(b"\r")
    try:
        back.decode("utf-8")
        enc_ok = True
    except UnicodeDecodeError:
        enc_ok = False
    ok = (n_src == n_dst) and (cr == 0) and enc_ok
    print("源编码判定=%s  CONFIG.PCW_ 条数 源=%d 目标正文=%d" % (enc, n_src, n_dst))
    print("CR 字节=%d  UTF-8 可解码=%s" % (cr, enc_ok))
    print("-> %s（%d B）" % (DST.relative_to(REPO), len(back)))
    print(("IMPORT_PS7_OK %d" % n_dst) if ok else "IMPORT_PS7_FAIL 校验未过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
