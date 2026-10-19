# ============================================================================
# zynq-yolo —— 顶层 Makefile（薄封装 / thin wrapper）
#
# 设计原则：**本文件不写任何构建逻辑**。
#   真正的逻辑只在三个地方，git 里都能看到：
#     build/scripts/   构建真源（综合 / 实现 / 出比特流）
#     sim/scripts/     仿真回归入口
#     tools/           仓库自检、度量采集、文档渲染
#   本文件只负责「把命令转发过去」，脚本还不存在时打印一行 TODO，不报错。
#
# 成功判据（docs/repo_spec.md §5）：
#   **不要用退出码判断成功。** Vivado 的 vivado.bat 内部报 error 也可能返回 0。
#   综合 / 实现 / 出比特流 ⇒ 日志必须出现 DONE_OK
#   JTAG 下载            ⇒ 日志必须出现 PROGRAM_OK
#
# 用法（Windows 本机：mingw32-make；Linux/WSL：make）：
#   mingw32-make help / check / sim / bit / report / clean
#
# 注 1：recipe 里 echo 的字符串一律保持纯 ASCII —— 在 Windows cmd 下按 GBK 解码，
#       中文会乱码（承旧项目踩坑）。中文说明只写在注释里（make 读注释但不执行）。
# 注 2：echo 文本里**禁止出现 '>' 与 '<'** —— cmd 会把它们当重定向，凭空生成文件。
#       因此下面用 ':' 代替 '->'，不写裸 @echo（cmd 下会打印 "ECHO is off."）。
# ============================================================================

# --- 工具链路径：全部用变量，允许环境覆盖，禁止硬编码机器绝对路径 -------------
VIVADO   ?= vivado
PYTHON   ?= python

# --- 约定入口脚本：仓库当前可能尚未创建，可被覆盖 -----------------------------
#     （覆盖示例：mingw32-make sim SIM_SCRIPT=sim/scripts/run_xsim.py）
CHECK_SCRIPT  ?= tools/check_repo.py
SIM_SCRIPT    ?= sim/scripts/run_sim.py
BIT_SCRIPT    ?= build/scripts/build_bitstream.tcl
REPORT_SCRIPT ?= tools/md_to_docx.py
CLEAN_SCRIPT  ?= build/scripts/clean.py

.DEFAULT_GOAL := help
.PHONY: help check sim bit report clean

# ---------------------------------------------------------------------------
# help：默认目标。列出可用命令 + 每个入口脚本当前是 READY 还是 TODO。
# ---------------------------------------------------------------------------
help:
	@echo zynq-yolo top-level Makefile (thin wrapper, no build logic here)
	@echo ---------------------------------------------------------------------
	@echo   make check    repo self-check   : $(PYTHON) $(CHECK_SCRIPT)   [$(if $(wildcard $(CHECK_SCRIPT)),READY,TODO)]
	@echo   make sim      simulation        : $(PYTHON) $(SIM_SCRIPT)   [$(if $(wildcard $(SIM_SCRIPT)),READY,TODO)]
	@echo   make bit      bitstream         : $(VIVADO) -mode batch -source $(BIT_SCRIPT)   [$(if $(wildcard $(BIT_SCRIPT)),READY,TODO)]
	@echo   make report   render report     : $(PYTHON) $(REPORT_SCRIPT)   [$(if $(wildcard $(REPORT_SCRIPT)),READY,TODO)]
	@echo   make clean    remove artifacts  : $(PYTHON) $(CLEAN_SCRIPT)   [$(if $(wildcard $(CLEAN_SCRIPT)),READY,TODO)]
	@echo ---------------------------------------------------------------------
	@echo Success criterion: log token DONE_OK (build) / PROGRAM_OK (JTAG), not exit code.
	@echo Full conventions: docs/repo_spec.md

# ---------------------------------------------------------------------------
# check：仓库自检（命名规则 / 必需目录 / 禁入文件）。退出码 0 = 通过。
# ---------------------------------------------------------------------------
check:
	@$(if $(wildcard $(CHECK_SCRIPT)),$(PYTHON) $(CHECK_SCRIPT),echo TODO: $(CHECK_SCRIPT))

# ---------------------------------------------------------------------------
# sim：仿真回归入口（tb + 激励向量 + 回归脚本，见 sim/README.md）
# ---------------------------------------------------------------------------
sim:
	@$(if $(wildcard $(SIM_SCRIPT)),$(PYTHON) $(SIM_SCRIPT),echo TODO: $(SIM_SCRIPT))

# ---------------------------------------------------------------------------
# bit：综合 / 实现 / 出比特流（Vivado batch 模式）。
#      跑完请自行确认日志里有 DONE_OK，不要只看退出码。
# ---------------------------------------------------------------------------
bit:
	@$(if $(wildcard $(BIT_SCRIPT)),$(VIVADO) -mode batch -source $(BIT_SCRIPT),echo TODO: $(BIT_SCRIPT))

# ---------------------------------------------------------------------------
# report：把 report/ 下的 Markdown 渲染成提交件（docx/pdf 等）
# ---------------------------------------------------------------------------
report:
	@$(if $(wildcard $(REPORT_SCRIPT)),$(PYTHON) $(REPORT_SCRIPT),echo TODO: $(REPORT_SCRIPT))

# ---------------------------------------------------------------------------
# clean：删除可重建的产物（.Xil / *.dcp / *.runs / build/reports 非基线部分）。
#        具体删除范围由脚本负责，本文件不写 rm 逻辑（§4：产物只由脚本管理）。
# ---------------------------------------------------------------------------
clean:
	@$(if $(wildcard $(CLEAN_SCRIPT)),$(PYTHON) $(CLEAN_SCRIPT),echo TODO: $(CLEAN_SCRIPT))
