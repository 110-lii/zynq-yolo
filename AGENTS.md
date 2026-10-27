# AGENTS.md —— zynq-yolo 项目须知（只在本仓库生效）

> 本文件放**项目专属**的硬事实：板子怎么连、工具链在哪、权威资料去哪找。
> 跨项目的环境事实（机器配置、网关、Python 选型）不写在这里，写在 Hermes 记忆里。
> 仓库内部的事实源优先级：`docs/repo_spec.md` > `README.md` > 本文件。

## 1. 项目是什么

ZYNQ-7020（领航者/正点原子）上的足球赛事视频分析系统：PL 侧 RTL + HLS 卷积引擎，PS 侧 PYNQ
负责标定与判定。**输入源自 2026-10-20（D015）起改为 PC 经以太网送入视频**，板载 DVP 采集链
已退出主线。功能范围与待裁定项以 `ai/coordination/DECISIONS.md` 为准。

## 2. 板子与远程操作

- 板子 IP：`192.168.2.99`
- **SSH 免密不通**，不要在这上面浪费时间；用仓库里的脚本走 Jupyter：
  `py tools/board_jupyter.py "<python 代码>"`（Jupyter 端口 9090，密码写在脚本里）
- 板子不稳定：一天能掉三次线，掉了先手动上电重启，再确认 Jupyter 端口通
- 板子离线时**不做任何硬件验证**，也不要声称「已验证」，改为记 todo

## 3. 工具链路径（本机，不装全局 PATH）

| 用途 | 路径 |
|---|---|
| Vivado（综合/实现/出 bit） | `D:\Xilinx\Vivado\2020.2\bin\vivado.bat` |
| 仿真 | `D:\iverilog\bin` |
| 训练环境 | `.train/venv`（torch 2.11） |

综合一次十分钟级：改 RTL 后不要连着跑三五次，先想清楚再出片。

## 4. 引脚与硬件权威资料

查引脚**不要凭网帖猜**，只看这三处（都在上一个项目目录下）：

- `D:\FPGA\oi\ref\board\board_pinout_navigator_zynq_io.xlsx`（全信号 + 方向）
- `D:\FPGA\oi\ref\board\board_pinout_navigator_zynq_io_v3.7.xdc`
- `D:\FPGA\oi\ref\board\board_base_schematic_navigator_v3.6.pdf`（原理图）

`D:\FPGA\oi` 还有上一个项目的上板实测记录（`ai/sessions`），遇到同类问题的现象可以对照。

## 5. 记录与验收

- `ai/` 是**评分目录**（指南 3.3.5.3/3.3.5.5，占 15 分），结构：`raw/`（逐字原文，不得手改）、
  `sessions/`、`coordination/`。每个有实质产出的会话要留记录，改动要能追到提交。
- 验收标准走到实物：读回一致、时序收敛、指标上涨**都不算过**；要人眼看屏、上板跑通。
- 提交包由 `py tools/make_submission.py` 生成到 `dist/`，旧包及时删，只留当天一份。
