# 板端环境（PYNQ 镜像）与掉线排查（2026-10-30）

> 只记**板子自身**的环境事实与配置改动，和仓库代码无关。
> 掉线症状、判据、改了什么、怎么回退，都在这。

## 1. 结论先说

「板子老掉线」不是玄学，是**两个独立故障**叠在一起，而这台 PYNQ 镜像的网络配置把第一个放大成了必然：

| 类型 | 现象 | 判据（本次实测） |
|---|---|---|
| **A 网络地址丢失** | 板子还在跑、`eth0` link up，但从 PC ping 100% 丢、Jupyter 连不上 | 心跳日志在「不可达」期间**仍在逐行记录**（`up=` 继续涨、`link=up`），说明整机没死 |
| **B 整机硬复位** | 日志在 dhclient 那行**戛然而止**，没有 shutdown 序列、没有 panic | `journalctl --list-boots`：每次 boot 的最后一屏都是 DHCP 重试中；`panic=0`（真 panic 只会挂住）、`RuntimeWatchdogUSec=0`（没有软看门狗） |

A 类的根因（**改前**的配置）：

```
# /etc/network/interfaces.d/eth0
auto eth0
iface eth0 inet dhcp          ← 这条链路上根本没有 DHCP 服务器
auto eth0:1
iface eth0:1 inet static
    address 192.168.2.99      ← 静态 IP 只挂在别名上
```

```
# /etc/dhcp/dhclient.conf
timeout 10; retry 1; reboot 3;   ← dhclient 重试 3 次就「重启整机」
```

三重后果：

1. `dhclient` 从开机起每 ~10 s 失败一次，**永远不成功**，且每轮把 `systemd-resolved` 停/起一次（syslog 被刷满，日志里「最后一次」永远是 DHCP 那行）；
2. 静态地址在 `eth0:1` 上，主口归 dhclient 管：任何一次 `ifdown eth0` / `ip addr flush dev eth0` 都会连带把 `.99` 带走 → **板子在线但不可达**；
3. `reboot 3` 是 dhclient 的「重试 N 次后重启系统」开关，等于给板子装了个**自动重启定时器**。

## 2. 改了什么（2026-10-30，均可回退）

| 文件/位置 | 改动 | 备份 |
|---|---|---|
| `/etc/network/interfaces.d/eth0` | `iface eth0 inet dhcp` → `inet manual`；`eth0:1` 静态原样保留（改动前实际生效的就是这个形态） | `eth0.bak-20261030` |
| `/etc/dhcp/dhclient.conf` | `reboot 3;` → `#reboot 3;`（带原因注释） | `dhclient.conf.bak-20261030` |
| `/usr/local/bin/net_guard.sh` + `crontab` | 每分钟检查 `.99` 是否在 `eth0` 上，不在就加回去；补上日志 `/var/log/net_guard.log` | 新增 |
| 运行时 | `pkill -f dhclient`（当前 boot 内不再循环；下次开机本来也不会启动） | — |

**回退**：

```bash
# 板端
cp -a /etc/network/interfaces.d/eth0.bak-20261030 /etc/network/interfaces.d/eth0
cp -a /etc/dhcp/dhclient.conf.bak-20261030    /etc/dhcp/dhclient.conf
crontab -l | grep -v net_guard.sh | crontab -
```

**验证**（板端，改完当场做）：

- `ifquery eth0:1` → `address: 192.168.2.99 / netmask 255.255.255.0`；
- `ifdown eth0:1; ifup eth0:1` 后地址正确恢复（日志 `/home/xilinx/ifup_test.log`）；
- `pgrep -c dhclient` → `0`。

## 3. 心跳日志（用来定死下一次掉线是 A 还是 B）

`/home/xilinx/heartbeat.log`，每 15 s 一行、`fsync` 落盘：

```
12:21:12 up=258.19 boot=d9b13054 link=up ip=192.168.2.99/24 dhclient=0
```

- `up=` 继续涨 ⇒ 板子活着，只是网络没了（A）；
- `up=` 归零 / `boot=` 变 ⇒ 整机重启（B）；
- `ip=NONE` ⇒ 地址真丢了（A 的典型形态），此时 `net_guard.sh` 应在一分钟内补回。

心跳进程是**普通后台进程**，重启后要重新拉起（见 `ai/sessions/022` §7.8 的命令）。

## 4. 还没定死的

- **B 类（硬复位）的物理原因**：12 V 适配器供电，已排除「USB 供电不足」；日志里既无 panic 也无 MMC 错误，无软看门狗证据。下一步可查：适配器带载时的 12 V 压降、电源插头/排针接触、SD 卡在写操作时的 I/O 卡死（`dmesg` 里 FAT「not properly unmounted」每次开机都有，说明每次都是断电式退出）。
- 板子**没有 RTC**（`timedatectl`: `RTC time: n/a`），断电后时间回到 1970/2021，日志时间不可信 —— 排查时按 `boot=` 编号对，不要按时间对。

## 5. 板子回来时的最短路径（2026-10-30 16:xx 新增）

**先说结论：这次不是「地址变了」，是板子没上电。** 2026-10-30 16:12 对整个
`192.168.2.0/24` 做了 9090/22/80 三端口的全段扫描（254 个地址，128 并发），
**一个 host 都不通** —— 本机 `192.168.2.100` 这个网口上再没有第二个活设备。
所以不要再去 9100/8000 之类的备用端口猜，也不必怀疑 `eth0:1` 没起来：整机不在网里。

上电之后**不用再一条条手工敲**，一条命令把所有板端验收按顺序跑完（`--wait` 让它自己等）：

```bash
py tools/run_board_acceptance.py --wait 2700
```

它做的事和判据：

| 步 | 做什么 | 过了的样子 |
|---|---|---|
| `wait` | 每 15 s 探一次 9090 | `BOARD_ONLINE after Ns` |
| `deploy` | 推 `flow_system.bit/.hwh` + 6 个工具 + 10 段分层片段 | **两端 sha256 逐文件对上**才算过 |
| `overlay` | `flow_overlay.py --selftest` | `FLOW_OVERLAY_LCD OK` + `FLOW_OVERLAY_HDMI OK` + `FLOW_OVERLAY_OK` |
| `hdmi` | 红/绿/蓝/白/黑五张带字色卡 | `HDMI_CARDS_DONE`（**屏上读字仍要人报**） |
| `scenes` | 10 段 `contest`/`normal` 走 `board_pipeline` | `SCENE_BOARD_AGG contest=… normal=…` + `SCENE_BOARD_OK` |

中间任何一步失败都会写进 `data/results/board_acceptance.json` 并**停下不往下跑**
（缺文件时往下跑只会得出假结论）。跑完顺手看：

```bash
py -c "import json;print(json.dumps(json.load(open('data/results/board_acceptance.json',encoding='utf-8')),ensure_ascii=False,indent=1))"
```

**注意**：`overlay` 和 `hdmi` 两步会持续满速扫出 HDMI（这和 B 类硬复位的时间相关性最高）。
如果它就掉在这两步上，那基本就是供电/硬件，而不是软件 —— 这本身是有用的结论，
把 `boot=` 编号和掉线时刻记到 `ai/sessions/022` §7.8。
