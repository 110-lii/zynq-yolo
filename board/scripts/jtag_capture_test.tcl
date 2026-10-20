# =============================================================================
# jtag_capture_test.tcl -- M1 capture chain on real silicon, JTAG only
#
# What this proves (and what it does NOT):
#   PROVES  : the pixel path vid_test_pattern -> vid_dvp_capture ->
#             inf_axis_async_fifo -> axi_dma (S2MM) -> DDR is byte-exact on
#             silicon, with the PL clocked by the real FCLK0/FCLK1 from the PLL
#             and the real BD wiring. Byte-exactness is decided by comparing the
#             DDR dump against tools/capture_golden.py's model.
#   DOES NOT: measure throughput / fps (JTAG mrd/mwr round trips are ms scale,
#             so no elapsed number here has physical meaning), and does NOT
#             touch the OV5640 pins (no camera: the pattern generator is a
#             stand-in that feeds the *same* capture module the camera will).
#
# Why a pattern generator instead of the camera: OV5640 needs SCCB register
# programming, and this path deliberately has no OS (no PYNQ, no Linux) -- see
# board/logs/board_jtag_bringup_20261020.md. The generator's formula is known
# in closed form, so "the bytes are right" has an exact answer.
#
# Flow (same bring-up skeleton as board/scripts/jtag_layer_test.tcl):
#   ps7_init (DDR/PLL/MIO) -> halt both A9 (they otherwise race DDR) ->
#   fpga bit -> ps7_post_config (else AXI to PL hangs) ->
#   arm DMA S2MM (DA, LENGTH, DMACR.RS) FIRST -> release pattern (GPIO bit0=1) ->
#   poll DMASR.IOC -> check GPIO2 bit0 (capture overflow sticky) ->
#   mrd -bin dump DDR -> tools/capture_golden.py --compare -> token.
#
# Order matters: DMA must be armed before pixels start. Arming afterwards would
# leave the first ~thousands of pixels on the floor and the compare would fail
# in a way that looks like a wiring bug.
#
# Success tokens (docs/repo_spec.md sec 5: tokens, never exit codes alone):
#     PS7_INIT_OK / PROGRAM_OK / POST_CONFIG_OK     bring-up stages
#     CAPTURE_DMA_OK dmasr=0x.. polls=..
#     CAPTURE_NO_OVERFLOW gpio2=0x..
#     CAPTURE_MATCH_OK words=.. crc32=..            (printed by the python tool)
#     JTAG_CAPTURE_OK                               overall pass (exit 0)
#     JTAG_CAPTURE_FAIL <reason>                    any failure (exit 1)
#
# Usage (Windows PC, board JTAG connected, bitstream already built):
#   D:\python\python.exe tools\capture_golden.py --out
#   D:\Xilinx\Vitis\2020.2\bin\xsct.bat board/scripts/jtag_capture_test.tcl
#   xsct ... jtag_capture_test.tcl --skip-program     # reuse a live PL/PS
#   Env overrides: GOLDEN=<bin> DUMP=<bin> PYTHON=<exe> XSCT?
# =============================================================================

# ---------------- constants (paths relative to repo root) --------------------
set REPO     [file normalize [file join [file dirname [info script]] .. ..]]
set BITFILE  [expr {[info exists ::env(BITFILE)] ? $::env(BITFILE) :
                    [file join $REPO board overlay capture_chain.bit]}]
set ADDRFILE [expr {[info exists ::env(ADDRFILE)] ? $::env(ADDRFILE) :
                    [file join $REPO board overlay capture_chain_addr.txt]}]
# HWH 是 BD 导出的硬件握手文件（PYNQ 也读它），地址**以它为准**：
# 第一版 BD 脚本按名字匹配 addr seg，实测一条都没匹配上（addr 文件里是空串），
# 所以这里直接从 .hwh 的 <MEMRANGE INSTANCE=... BASEVALUE=...> 取基址，
# addr 文件退化为“几何参数来源”（有就用，没有就用默认 640x480x4）。
set HWHFILE  [expr {[info exists ::env(HWH)] ? $::env(HWH) :
                    [file join $REPO board overlay capture_chain.hwh]}]
set GOLDEN   [expr {[info exists ::env(GOLDEN)] ? $::env(GOLDEN) :
                    [file join $REPO build board capture_golden.bin]}]
set DUMP     [expr {[info exists ::env(DUMP)] ? $::env(DUMP) :
                    [file join $REPO build board capture_dump.bin]}]
set GOLDEN_TOOL [file join $REPO tools capture_golden.py]
set PYTHON   [expr {[info exists ::env(PYTHON)] ? $::env(PYTHON) : "D:\\python\\python.exe"}]
set PS7_INIT [file join $REPO build vivado capture_chain_bd capture_chain_bd.gen \
                  sources_1 bd system ip system_processing_system7_0_0 ps7_init.tcl]

# DDR layout: frame lands at 0x20000000 (same offset the PYNQ path will use)
set A_FRAME 0x20000000

# DMA S2MM register offsets (PG021; the MM2S half is absent, S2MM offsets stay)
set DMA_DMACR      0x30
set DMA_DMASR      0x34
set DMA_DA         0x48
set DMA_DA_MSB     0x4C
set DMA_LENGTH     0x58
set DMACR_RS       0x00000001
set DMACR_IOC_IRQ  0x00001000
set DMACR_ERR_IRQ  0x00004000
set DMASR_IOC      0x00001000
set DMASR_ERR_IRQ  0x00004000
set DMASR_ERR_MASK 0x00000070   ;# bits 4/5/6 = Int_Err / Slv_Err / Dec_Err

# AXI GPIO offsets (PG144)
set GPIO_CH1_DATA  0x00
set GPIO_CH2_DATA  0x08

set POLL_MAX     400            ;# 400 x 50 ms = 20 s (a 640x480 frame is ~25 ms)
set POLL_STEP_MS 50

proc fail {reason} {
    puts "JTAG_CAPTURE_FAIL $reason"
    exit 1
}

# Always hand xsct hex literals: the OFFSET properties come back as "0x40.."
# strings, but exprs turn them into decimals, and checkint's tolerance for
# decimal addresses is not worth relying on.
proc wr {addr value} {
    mwr -force [format 0x%08X [expr {$addr}]] [format 0x%08X [expr {$value}]]
}

# Read one 32-bit word. Live mrd format on this xsct/hw_server is
# "40000000:   00000004" (no 0x prefixes) -- see jtag_layer_test.tcl.
proc rd32 {addr} {
    set raw [mrd [format 0x%08X [expr {$addr}]] 1]
    foreach line [split $raw "\n"] {
        set line [string trim $line]
        if {[regexp {^([0-9A-Fa-f]+):\s+([0-9A-Fa-f]+)$} $line -> _a v]} {
            return [expr {"0x$v"}]
        }
    }
    fail "mrd returned unparsable text for [format 0x%08X $addr]: $raw"
}

# ---------------- preflight ----------------------------------------------------
set skip_program 0
foreach a $argv { if {$a eq "--skip-program"} { set skip_program 1 } }

if {![file exists $BITFILE]}  { fail "no bitfile: $BITFILE" }
if {![file exists $HWHFILE] && ![file exists $ADDRFILE]} {
    fail "no address source: neither $HWHFILE nor $ADDRFILE (run build/scripts/bd_capture_chain.tcl)"
}
if {![file exists $GOLDEN]}   { fail "no golden bin (python tools/capture_golden.py --out): $GOLDEN" }
if {![file exists $GOLDEN_TOOL]} { fail "no compare tool: $GOLDEN_TOOL" }
if {!$skip_program && ![file exists $PS7_INIT]} { fail "no ps7_init: $PS7_INIT" }

# 从 .hwh 取某个实例的基址：<MEMRANGE ... BASEVALUE="0x40400000" ... INSTANCE="axi_dma_0" ...>
# （属性顺序不保证，所以逐行分别抽 INSTANCE 与 BASEVALUE，不要写成一个 regexp）
proc hwh_base {path inst} {
    if {![file exists $path]} { return "" }
    set fh [open $path r]
    set data [read $fh]
    close $fh
    foreach line [split $data "\n"] {
        if {[string first "MEMRANGE" $line] < 0} { continue }
        if {[string first "INSTANCE=\"$inst\"" $line] < 0} { continue }
        if {[regexp {BASEVALUE="(0x[0-9A-Fa-f]+)"} $line -> bv]} { return $bv }
    }
    return ""
}

# addr 文件（可选）：只提供几何参数；键值格式 "k=v"
proc read_addr_file {path} {
    set cfg [dict create]
    if {![file exists $path]} { return $cfg }
    set fh [open $path r]
    foreach line [split [read $fh] "\n"] {
        set line [string trim $line]
        if {$line eq "" || [string index $line 0] eq "#"} { continue }
        set eq [string first "=" $line]
        if {$eq < 0} { continue }
        set k [string trim [string range $line 0 [expr {$eq - 1}]]]
        set v [string trim [string range $line [expr {$eq + 1}] end]]
        if {$v ne ""} { dict set cfg $k $v }
    }
    close $fh
    return $cfg
}

set CFG [read_addr_file $ADDRFILE]
set DMA_BASE  [hwh_base $HWHFILE axi_dma_0]
set GPIO_BASE [hwh_base $HWHFILE axi_gpio_0]
if {$DMA_BASE eq "" || $GPIO_BASE eq ""} {
    # 退化路径：hwh 不在时用 addr 文件（老版本 BD 才会走到这里）
    if {[dict exists $CFG dma_base] && [dict exists $CFG gpio_base]} {
        set DMA_BASE  [dict get $CFG dma_base]
        set GPIO_BASE [dict get $CFG gpio_base]
        puts "WARN 没有从 hwh 取到地址，退回 addr 文件：dma=$DMA_BASE gpio=$GPIO_BASE"
    } else {
        fail "cannot resolve dma/gpio base (hwh=$HWHFILE addr=$ADDRFILE)"
    }
}
set H_ACTIVE    [expr {[dict exists $CFG h_active]    ? [dict get $CFG h_active]    : 640}]
set V_ACTIVE    [expr {[dict exists $CFG v_active]    ? [dict get $CFG v_active]    : 480}]
set PIXEL_BYTES [expr {[dict exists $CFG pixel_bytes] ? [dict get $CFG pixel_bytes] : 4}]
set FRAME_BYTES [expr {[dict exists $CFG frame_bytes] ? [dict get $CFG frame_bytes] :
                       $H_ACTIVE * $V_ACTIVE * $PIXEL_BYTES}]
set FRAME_WORDS [expr {$FRAME_BYTES / 4}]
puts "CFG dma=$DMA_BASE gpio=$GPIO_BASE frame=${H_ACTIVE}x${V_ACTIVE} (addr from [expr {[file exists $HWHFILE] ? {hwh} : {addr file}}])"
puts "CFG pixel_bytes=$PIXEL_BYTES frame_bytes=$FRAME_BYTES frame_words=$FRAME_WORDS"

# The golden model must agree on geometry, otherwise the compare is meaningless.
if {[file size $GOLDEN] != $FRAME_BYTES} {
    fail "golden size [file size $GOLDEN] != frame_bytes $FRAME_BYTES (re-run capture_golden.py --out)"
}

file mkdir [file dirname $DUMP]

# ---------------- connect + ps7_init + program PL ------------------------------
proc connect_and_select_apu {} {
    connect
    after 1000
    if {[catch {targets -set -filter {name =~ "APU*"}} err]} {
        puts "APU missing on first try ($err); attempting DAP reset..."
        catch {targets -set -filter {name =~ "DAP*"}}
        catch {rst}
        after 3000
        if {[catch {targets -set -filter {name =~ "APU*"}} err2]} {
            fail "cannot select APU target even after DAP reset: $err2"
        }
    }
    puts "TARGET: APU selected"
}

proc halt_cores {} {
    foreach idx {2 3} {
        if {[catch {targets $idx} sel_err]} {
            puts "WARN select core idx $idx failed: $sel_err"
            continue
        }
        catch {stop}
        puts "CORE_HALTED idx=$idx state=[string trim [targets]]"
    }
    catch {targets -set -filter {name =~ "APU*"}}
}

if {!$skip_program} {
    connect_and_select_apu
    puts "running ps7_init (PLL/clock/DDR/MIO init, same registers as FSBL)..."
    source $PS7_INIT

    # Reset the PS before init: a session that follows an aborted one inherits a
    # wedged DAP/interconnect (see board/logs/board_jtag_bringup_20261020.md).
    # rst -system also clears the PL => fpga must run after it.
    if {[catch {rst -system} rst_err]} {
        puts "WARN rst -system failed ($rst_err); continuing"
    }
    after 2000

    set init_ok 0
    for {set try 1} {$try <= 3} {incr try} {
        if {![catch {ps7_init} initerr]} { set init_ok 1; break }
        puts "ps7_init attempt $try failed ($initerr); resetting and retrying..."
        catch {rst -system}
        after 3000
    }
    if {!$init_ok} { fail "ps7_init failed 3 times: $initerr" }
    puts "PS7_INIT_OK"
    halt_cores

    puts "programming PL: $BITFILE"
    # ★ 2026-10-20 实测踩到：`fpga` 报 "fpga configuration failed. DONE PIN is not HIGH"。
    #   之后 DAP 直接进 "AP transaction error, DAP status 0x30000021"（`rst -system`
    #   也报 "Cannot reset DAP"），只能断电重启 —— 所以**失败后不要继续往下跑**，
    #   而是降 JTAG 频率、重做一次 PS 复位+init 后再试一次；再失败就明确报错退出。
    set prog_ok 0
    for {set try 1} {$try <= 2} {incr try} {
        if {[catch {fpga $BITFILE} ferr]} {
            puts "WARN fpga attempt $try failed: $ferr"
            if {$try == 1} {
                catch {jtag frequency 1500000}
                puts "   降 JTAG 时钟到 1.5 MHz，重做 rst -system + ps7_init 后重试"
                catch {rst -system}
                after 2000
                catch {source $PS7_INIT}
                catch {ps7_init}
                after 500
                halt_cores
            }
        } else {
            set prog_ok 1
            break
        }
    }
    if {!$prog_ok} {
        fail "fpga configuration failed twice (DONE PIN not HIGH) —— 板子需断电重启；见 docs/integration_rtl_plan.md §9-12"
    }
    puts "PROGRAM_OK"

    if {[catch {ps7_post_config} pcerr]} {
        puts "post_config first attempt failed ($pcerr); retrying after settle..."
        after 2000
        if {[catch {ps7_post_config} pcerr2]} {
            fail "ps7_post_config failed twice: $pcerr2"
        }
    }
    puts "POST_CONFIG_OK"
} else {
    connect_and_select_apu
    puts "SKIP_PROGRAM: reusing a live PS/PL (no ps7_init, no fpga download)"
}

# ---------------- memmap (hw_server blocks PL-side addresses by default) ------
catch {memmap -addr [format 0x%08X [expr {$DMA_BASE & 0xFFFF0000}]] -size 0x10000}
catch {memmap -addr [format 0x%08X [expr {$GPIO_BASE & 0xFFFF0000}]] -size 0x10000}
catch {memmap -addr [format 0x%08X $A_FRAME] -size [format 0x%08X [expr {($FRAME_BYTES + 0xFFF) & ~0xFFF}]]}
puts "MEMMAP_ADDED dma=$DMA_BASE gpio=$GPIO_BASE frame=$A_FRAME"

# ---------------- make sure the pattern is parked at frame start -------------
# run=0 => generator holds (0,0) with href=0, so nothing enters the FIFO while
# DMA is being armed.
catch {wr [expr {$GPIO_BASE + $GPIO_CH1_DATA}] 0x0}

# ---------------- sentinel: detect "DMA never wrote a byte" -------------------
# Only the first 64 words (a full mwr sweep of 1.2 MB costs minutes over JTAG).
for {set i 0} {$i < 64} {incr i} {
    wr [expr {$A_FRAME + 4 * $i}] 0xDEADBEEF
}
puts "SENTINEL_WRITTEN words=64"

# ---------------- arm DMA S2MM BEFORE releasing pixels -----------------------
# PG021 simple-mode order: DA / DA_MSB -> LENGTH -> DMACR.RS.
wr [expr {$DMA_BASE + $DMA_DA}]      [expr {$A_FRAME & 0xFFFFFFFF}]
wr [expr {$DMA_BASE + $DMA_DA_MSB}]  0
wr [expr {$DMA_BASE + $DMA_LENGTH}]  $FRAME_BYTES
# W1C the error bits + IOC before starting, so a stale sticky bit cannot pass.
wr [expr {$DMA_BASE + $DMA_DMASR}] [expr {$DMASR_IOC | $DMASR_ERR_IRQ | $DMASR_ERR_MASK}]
wr [expr {$DMA_BASE + $DMA_DMACR}] [expr {$DMACR_RS | $DMACR_IOC_IRQ | $DMACR_ERR_IRQ}]
set dmacr_rb [rd32 [expr {$DMA_BASE + $DMA_DMACR}]]
puts "DMA_ARMED da=0x[format %08X $A_FRAME] length=$FRAME_BYTES dmacr=0x[format %08X $dmacr_rb]"

# ---------------- release the pattern ----------------------------------------
wr [expr {$GPIO_BASE + $GPIO_CH1_DATA}] 0x1
puts "PATTERN_RUN gpio bit0=1"

# ---------------- poll for transfer completion -------------------------------
set polls 0
set dmasr 0
while {$polls < $POLL_MAX} {
    set dmasr [rd32 [expr {$DMA_BASE + $DMA_DMASR}]]
    if {[expr {$dmasr & $DMASR_ERR_MASK}] || [expr {$dmasr & $DMASR_ERR_IRQ}]} {
        catch {wr [expr {$GPIO_BASE + $GPIO_CH1_DATA}] 0x0}
        fail "dma error dmasr=0x[format %08X $dmasr] (bit4=Int_Err bit5=Slv_Err bit6=Dec_Err)"
    }
    if {[expr {$dmasr & $DMASR_IOC}]} { break }
    incr polls
    after $POLL_STEP_MS
}
if {[expr {$dmasr & $DMASR_IOC}] == 0} {
    catch {wr [expr {$GPIO_BASE + $GPIO_CH1_DATA}] 0x0}
    fail "dma ioc timeout after [expr {$POLL_MAX * $POLL_STEP_MS / 1000}] s, dmasr=0x[format %08X $dmasr]"
}
# Park the generator again so a second frame cannot overwrite the buffer while
# we are draining it over JTAG (that failure looks exactly like a byte mismatch).
wr [expr {$GPIO_BASE + $GPIO_CH1_DATA}] 0x0
puts "CAPTURE_DMA_OK dmasr=0x[format %08X $dmasr] polls=$polls"

# ---------------- capture-side overflow (gpio2 bit0, sticky) -----------------
set gpio2 [rd32 [expr {$GPIO_BASE + $GPIO_CH2_DATA}]]
if {[expr {$gpio2 & 0x1}]} {
    fail "capture fifo overflowed (gpio2=0x[format %08X $gpio2]) -- pixels were dropped, compare would be meaningless"
}
puts "CAPTURE_NO_OVERFLOW gpio2=0x[format %08X $gpio2]"

# ---------------- sanity: did DMA actually write? ----------------------------
set head [rd32 $A_FRAME]
if {$head == 0xDEADBEEF} {
    fail "frame head still 0xDEADBEEF: DMA reported done but wrote nothing"
}
puts "FRAME_HEAD word0=0x[format %08X $head]"

# ---------------- drain DDR and compare against the model --------------------
if {[file exists $DUMP]} { file delete $DUMP }
puts "dumping $FRAME_WORDS words over JTAG (this is slow on purpose: ms per burst)..."
if {[catch {mrd -bin -file $DUMP [format 0x%08X $A_FRAME] $FRAME_WORDS} mrderr]} {
    fail "mrd -bin failed: $mrderr"
}
if {![file exists $DUMP]} { fail "dump missing after mrd: $DUMP" }
puts "CAPTURE_DUMP_OK path=$DUMP bytes=[file size $DUMP]"

if {![file exists $PYTHON]} { fail "python not found: $PYTHON" }
set cmp_out ""
if {[catch {exec $PYTHON $GOLDEN_TOOL --compare $DUMP} cmp_out]} {
    puts $cmp_out
    fail "byte compare FAILED (see CAPTURE_MATCH_FAIL above)"
}
puts $cmp_out
if {![string match "*CAPTURE_MATCH_OK*" $cmp_out]} {
    fail "compare tool did not emit CAPTURE_MATCH_OK"
}

# ---------------- machine-readable result ------------------------------------
# RESULT_JSON override exists so a stub/dry run can never leave a fake result in
# data/results/ (a stub pass must not look like a board pass in the evidence).
set json_path [expr {[info exists ::env(RESULT_JSON)] ? $::env(RESULT_JSON) :
                     [file join $REPO data results jtag_capture_test.json]}]
if {[catch {
    set fh [open $json_path w]
    puts $fh "{"
    puts $fh "  \"tool\": \"board/scripts/jtag_capture_test.tcl\","
    puts $fh "  \"path\": \"JTAG only, no operating system\","
    puts $fh "  \"generated_utc\": \"[clock format [clock seconds] -format %Y-%m-%dT%H:%M:%SZ -gmt 1]\","
    puts $fh "  \"bitfile\": \"board/overlay/capture_chain.bit\","
    puts $fh "  \"pattern\": \"vid_test_pattern R5=x&0x1F G6=y&0x3F B5=(x+y)&0x1F, RGB565 -> RGB888 bit replication\","
    puts $fh "  \"geometry\": \"${H_ACTIVE}x${V_ACTIVE} pixel_bytes=$PIXEL_BYTES frame_bytes=$FRAME_BYTES\","
    puts $fh "  \"dmasr\": \"0x[format %08X $dmasr]\","
    puts $fh "  \"polls\": $polls,"
    puts $fh "  \"gpio2\": \"0x[format %08X $gpio2]\","
    puts $fh "  \"dump\": \"build/board/capture_dump.bin\","
    puts $fh "  \"status\": \"JTAG_CAPTURE_OK\","
    puts $fh "  \"timing_note\": \"elapsed/fps intentionally absent: JTAG mrd/mwr round trips are ms scale\""
    puts $fh "}"
    close $fh
    puts "RESULT_JSON $json_path"
} jerr]} {
    puts "WARN could not write result json: $jerr"
}

puts "JTAG_CAPTURE_OK"
exit 0