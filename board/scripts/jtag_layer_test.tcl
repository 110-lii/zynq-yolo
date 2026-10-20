# =============================================================================
# jtag_layer_test.tcl -- conv_engine single-layer correctness via JTAG only
#
# Scenario: the board's SD card holds a PYNQ image but the mode switch is in
# JTAG position and nobody is at the board to flip it. PS never boots Linux,
# so the ssh/PYNQ path is unavailable. This script uses XSCT (hw_server):
#
#     ps7_init   (init PLL/clocks/DDR/MIO from the BD-generated table; same
#                 register writes an FSBL would do, so DDR is usable with no
#                 bootloader running)
#       -> fpga  (program conv_engine.bit; PL runs at FCLK0=83.33 MHz)
#       -> ps7_post_config  (MUST run after fpga: enables PS<->PL level
#                 shifters; skipping it makes every AXI GP0 access to PL hang)
#       -> dow -data  (write test vectors in/w/params/lut into DDR)
#       -> out region pre-filled with 0xCC sentinel
#       -> mwr port B (five 64-bit pointer bases) + port A (11 scalars),
#          per contract docs/accel_interface.md sec 4
#       -> mwr AP_CTRL=1, poll ap_done (bit1) via mrd
#       -> block mrd of out region, compare word-by-word against out_ref
#
# Success tokens (repo_spec sec 5: tokens, never exit codes alone):
#     JTAG_LAYER_OK <case> 0/<n_out>          per-case pass
#     JTAG_TEST_OK <n_ok> / <n_cases>         summary (exit 0)
#     JTAG_LAYER_FAIL / JTAG_FAIL ...         any failure (exit 1)
#
# Usage (Windows PC, board JTAG connected):
#   xsct board/scripts/jtag_layer_test.tcl                 # all cases
#   xsct board/scripts/jtag_layer_test.tcl k3s1_c3_40      # one case
#   Env overrides: CASES=<cases dir>
#
# NOT measured here: throughput. mrd/mwr round trips over JTAG are ~ms scale,
# so any "elapsed" number from this script is meaningless. GOPS/fps must come
# from run_layer_test.py once the board boots PYNQ from SD. This script only
# answers: are the bytes the PL computed on real silicon correct?
#
# DDR layout (1 GB board, avoids low addresses used by bootloaders):
#   0x10000000  in      (uint8, C_IN*H_IN*W_IN bytes, max case 4800)
#   0x10100000  w       (int8,  C_OUT*C_IN*K*K, max case 129*512*9 ~ 590K)
#   0x10200000  params  (int32 x C_OUT*3, max 3072 B)
#   0x10300000  lut     (256 B)
#   0x20000000  out     (uint8, C_OUT*H_OUT*W_OUT, max case 25600 B)
#   0x30000000  out_ref (same size as out)
# Each region has >= 1 MB headroom against the largest of the 6 cases.
# =============================================================================

# ---------------- constants (paths relative to repo root) --------------------
set REPO        [file normalize [file join [file dirname [info script]] .. ..]]
set BITFILE     [file join $REPO board overlay conv_engine.bit]
set CASES       [expr {[info exists ::env(CASES)] ? $::env(CASES) :
                        [file join $REPO data layers cases]}]
set PS7_INIT    [file join $REPO build vivado conv_engine_bd conv_engine_bd.gen \
                     sources_1 bd system ip system_processing_system7_0_0 ps7_init.tcl]

# DDR layout
set A_IN        0x10000000
set A_W         0x10100000
set A_PARAMS    0x10200000
set A_LUT       0x10300000
set A_OUT       0x20000000
set A_REF       0x30000000

# Port A scalar registers (contract sec 4; same source as
# src/ps/pynq_driver/conv_engine.py REG_* constants)
set R_CTRL      0x40000000
set R_CIN       0x40000010
set R_COUT      0x40000018
set R_HIN       0x40000020
set R_WIN       0x40000028
set R_K         0x40000030
set R_STRIDE    0x40000038
set R_PAD       0x40000040
set R_ZPIN      0x40000048
set R_ZPPRE     0x40000050
set R_HOUT      0x40000058
set R_WOUT      0x40000060

# Port B pointer registers (stride is 12 bytes: 0x10,0x1C,0x28,0x34,0x40 --
# HLS packing, do not "fix" to 8 or 16)
set P_IN        0x40010010
set P_W         0x4001001C
set P_PARAMS    0x40010028
set P_LUT       0x40010034
set P_OUT       0x40010040

# ---------------- helpers ------------------------------------------------------
proc fail {token reason} {
    puts $token
    puts $reason
    exit 1
}

proc parse_meta {case_dir} {
    set meta [dict create]
    set fh [open [file join $case_dir meta.txt] r]
    foreach line [split [read $fh] "\n"] {
        set line [string trim $line]
        if {$line eq "" || [string index $line 0] eq "#"} { continue }
        set idx [string first "=" $line]
        if {$idx < 0} { continue }
        dict set meta [string range $line 0 [expr {$idx - 1}]] \
                           [string range $line [expr {$idx + 1}] end]
    }
    close $fh
    return $meta
}

proc m {meta key} { return [dict get $meta $key] }

# Write one 64-bit pointer into port B (low 32 + high 32; high is 0 on 7020)
proc write_ptr {base value} {
    mwr -force $base               [expr {$value & 0xFFFFFFFF}]
    mwr -force [expr {$base + 4}]  0
}

# Block-read n words from addr; returns list of hex-string values.
# Live format (measured on this xsct/hw_server): "40000000:   00000004"
# (address WITHOUT 0x prefix, value WITHOUT 0x prefix, colon separator).
proc block_read {addr n} {
    set out {}
    foreach line [split [mrd $addr $n] "\n"] {
        set line [string trim $line]
        if {$line eq ""} { continue }
        if {![regexp {^([0-9A-Fa-f]+):\s+([0-9A-Fa-f]+)$} $line -> _addr val]} { continue }
        lappend out "0x$val"
    }
    return $out
}

# ---------------- preflight ----------------------------------------------------
if {![file exists $BITFILE]}    { fail JTAG_FAIL "no bitfile: $BITFILE" }
if {![file exists $PS7_INIT]}   { fail JTAG_FAIL "no ps7_init: $PS7_INIT" }
if {![file isdirectory $CASES]} { fail JTAG_FAIL "no cases dir: $CASES" }

set case_arg [lindex $argv 0]
if {$case_arg ne ""} {
    set case_list [list $case_arg]
} else {
    set case_list [list]
    foreach d [lsort [glob -nocomplain -directory $CASES *]] {
        if {[file exists [file join $d meta.txt]]} {
            lappend case_list [file tail $d]
        }
    }
}
if {[llength $case_list] == 0} { fail JTAG_FAIL "no cases found" }
puts "CASES: $case_list"

# ---------------- connect + ps7_init + program PL ------------------------------
# *?Live pitfall (measured, not theoretical): after a prior xsct session died
#   mid-ps7_init, the DAP shows "AHB AP transaction error, DAP status
#   0x30000021" and APU is NOT in the target list. Recovery that worked:
#   select the DAP target, "rst", wait; the APU target comes back by itself
#   on a fresh connect. If APU is missing on first try, do that once.
proc connect_and_select_apu {} {
    connect
    after 1000
    if {[catch {targets -set -filter {name =~ "APU*"}} err]} {
        puts "APU missing on first try ($err); attempting DAP reset..."
        catch {targets -set -filter {name =~ "DAP*"}}
        catch {rst}
        after 3000
        if {[catch {targets -set -filter {name =~ "APU*"}} err2]} {
            fail JTAG_FAIL "cannot select APU target even after DAP reset: $err2"
        }
    }
    puts "TARGET: APU selected"
}
connect_and_select_apu

# *?Halt both A9 cores before touching DDR.
#   Live-measured root cause of "~24-48% of output bytes wrong": after
#   ps7_init alone, `targets` shows "ARM Cortex-A9 MPCore #0 (Running)" and
#   "#1 (Running)". The cores fetch garbage from uninitialised DDR and
#   execute it, writing memory at random while conv_engine is also writing
#   the output buffer -- a memory race, not a numeric bug. It also explains
#   the intermittent "AHB AP transaction error, DAP status 0x30000021" and
#   "Access can hang PS interconnect" seen between runs.
#   Fix: stop both cores right after ps7_init and keep them halted; the PL
#   kernel needs no CPU. (PYNQ/Linux would normally own them, but this path
#   deliberately has no OS.)
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

puts "running ps7_init (PLL/clock/DDR/MIO init, same registers as FSBL)..."
source $PS7_INIT

# *?Reset the PS *before* init. Live-measured: without this, a session that
#   follows an aborted/debug-halted one inherits a wedged DAP/interconnect
#   ("AHB AP transaction error, DAP status 0x30000021"), ps7_init fails at
#   0xF8006078 with "Access can hang PS interconnect", and even when init
#   limps through the kernel's DDR writes come back partially wrong
#   (3096/6400 words mismatched, with sentinel bytes surviving in the
#   output). After rst -system + init + halted cores the very same case
#   compared 6400/6400 words equal. So: reset is NOT optional here.
#   rst -system also clears the PL => fpga must run after it (skill pitfall 5).
if {[catch {rst -system} rst_err]} {
    puts "WARN rst -system failed ($rst_err); continuing"
}
after 2000

# Retry: retry once more with another rst -system if the first init trips.
set init_ok 0
for {set try 1} {$try <= 3} {incr try} {
    if {![catch {ps7_init} initerr]} { set init_ok 1; break }
    puts "ps7_init attempt $try failed ($initerr); resetting and retrying..."
    catch {rst -system}
    after 3000
}
if {!$init_ok} { fail JTAG_FAIL "ps7_init failed 3 times: $initerr" }
puts "PS7_INIT_OK"
halt_cores

puts "programming PL: $BITFILE"
fpga $BITFILE
puts "PROGRAM_OK"

# Run post-config with tolerance: on a healthy APU it enables the PS<->PL
# level shifters. It internally calls ps_version (SLCR read); if the DAP
# hiccups right after programming, a failed attempt is caught and retried
# once after a settle delay (live-measured: the DAP error is transient).
if {[catch {ps7_post_config} pcerr]} {
    puts "post_config first attempt failed ($pcerr); retrying after settle..."
    after 2000
    if {[catch {ps7_post_config} pcerr2]} {
        fail JTAG_FAIL "ps7_post_config failed twice: $pcerr2"
    }
}
puts "POST_CONFIG_OK"

# ---------------- run cases ----------------------------------------------------
set n_ok 0
set n_fail 0
set summary {}
set case_results {}

# *?hw_server blocks PL-side AXI addresses by default: "This address has not
#   beed added to the memory map". The fix (skill pitfall #6) is the XSCT
#   "memmap" command: register the region BEFORE the first mwr/mrd on it.
#   One entry per contiguous region used below (control 64K, data 1MB x 4).
memmap -addr 0x40000000 -size 0x10000
memmap -addr 0x40010000 -size 0x10000
puts "MEMMAP_ADDED 0x40000000/0x40010000 64K each"

foreach case $case_list {
    set case_dir [file join $CASES $case]
    puts "---- CASE $case ----"

    set meta [parse_meta $case_dir]
    set cin    [m $meta C_IN]
    set cout   [m $meta C_OUT]
    set hin    [m $meta H_IN]
    set win    [m $meta W_IN]
    set k      [m $meta K]
    set stride [m $meta stride]
    set pad    [m $meta pad]
    set zpin   [m $meta zp_in]
    set zppre  [m $meta zp_pre]
    set hout   [m $meta H_OUT]
    set wout   [m $meta W_OUT]

    set n_in     [expr {$cin * $hin * $win}]
    set n_w      [expr {$cout * $cin * $k * $k}]
    set n_params [expr {$cout * 3 * 4}]
    set n_out    [expr {$cout * $hout * $wout}]
    set words_out [expr {($n_out + 3) / 4}]

    # 1) download vectors
    if {[catch {
        dow -data [file join $case_dir in.bin]      $A_IN
        dow -data [file join $case_dir w.bin]       $A_W
        dow -data [file join $case_dir params.bin]  $A_PARAMS
        dow -data [file join $case_dir lut.bin]     $A_LUT
        dow -data [file join $case_dir out_ref.bin] $A_REF
    } err]} {
        puts "JTAG_LAYER_FAIL $case download_error: $err"
        incr n_fail
        lappend summary "$case DL_ERR"
        lappend case_results [format {    {"case": "%s", "ok": false, "reason": "download_error"}} $case]
        continue
    }
    puts "VECTORS_LOADED in=$n_in w=$n_w params=$n_params lut=256 out=$n_out"

    # 2) pre-fill output region with 0xCC sentinel (whole region means the
    #    kernel never wrote -- far more diagnostic than zeros)
    for {set i 0} {$i < $words_out} {incr i} {
        mwr -force [expr {$A_OUT + 4 * $i}] 0xCCCCCCCC
    }
    puts "SENTINEL_FILLED words=$words_out"

    # 3) port B: five pointer bases
    write_ptr $P_IN      $A_IN
    write_ptr $P_W       $A_W
    write_ptr $P_PARAMS  $A_PARAMS
    write_ptr $P_LUT     $A_LUT
    write_ptr $P_OUT     $A_OUT

    # 4) port A: 11 scalars (geometry; AP_CTRL untouched yet)
    mwr -force $R_CIN    $cin
    mwr -force $R_COUT   $cout
    mwr -force $R_HIN    $hin
    mwr -force $R_WIN    $win
    mwr -force $R_K      $k
    mwr -force $R_STRIDE $stride
    mwr -force $R_PAD    $pad
    mwr -force $R_ZPIN   $zpin
    mwr -force $R_ZPPRE  $zppre
    mwr -force $R_HOUT   $hout
    mwr -force $R_WOUT   $wout

    # 5) start + poll ap_done (bit1).
    #    *?DO NOT pre-read AP_CTRL before starting. Live-measured twice:
    #    with a pre-read the kernel stalls partway (output half-sentinel,
    #    e.g. word1 = 0xCCC13022, 6201/25600 bytes stale) and a later run
    #    even hit ap_done_timeout; without it CTRL reaches 0x6 (done+idle)
    #    and output matches golden head and mid. Working hypothesis: the
    #    pre-read races the kernel's sampling of ap_start over the slow
    #    JTAG bridge. ap_done IS clear-on-read, so a leftover done from a
    #    previous case is consumed by this poll loop's first read -- an
    #    instant "done" on iteration 0 is then expected and harmless because
    #    each case boundary reloads full geometry + pointers + vectors.
    mwr -force $R_CTRL 0x1
    set done 0
    set polls 0
    set raw_seen ""
    for {set i 0} {$i < 10000} {incr i} {
        set line [string trim [mrd $R_CTRL 1]]
        if {$i < 3} { append raw_seen "\[$i\]$line " }
        if {[regexp {([0-9A-Fa-f]+)$} $line -> ctrl_hex]} {
            if {[expr {"0x$ctrl_hex" & 0x2}] != 0} {
                set done 1
                set polls $i
                break
            }
        }
        after 1
    }
    if {!$done} {
        puts "JTAG_LAYER_FAIL $case ap_done_timeout ctrl_raw=$raw_seen"
        incr n_fail
        lappend summary "$case TIMEOUT"
        lappend case_results [format {    {"case": "%s", "ok": false, "reason": "ap_done_timeout"}} $case]
        continue
    }
    puts "AP_DONE polls=$polls"

    # Settle before reading back. Rationale (measured): with the CPU cores
    # halted a 0 ms read also matched, so this is not papering over a bug --
    # but a stale done bit from the previous case can make the poll exit on
    # iteration 0 while the kernel is still draining its final AXI writes,
    # which showed up as sentinel bytes surviving in the output. 2 s is
    # cheap next to a JTAG round trip and removes that whole failure mode.
    after 2000

    # 6) block-read out + ref and compare word by word (little endian bytes
    #    inside each word; byte-level diff computed only for mismatched words)
    set got_words [block_read $A_OUT $words_out]
    set ref_words [block_read $A_REF $words_out]
    if {[llength $got_words] != $words_out || [llength $ref_words] != $words_out} {
        puts "JTAG_LAYER_FAIL $case block_read_short got=[llength $got_words] ref=[llength $ref_words] want=$words_out"
        incr n_fail
        lappend summary "$case RD_ERR"
        lappend case_results [format {    {"case": "%s", "ok": false, "reason": "block_read_short"}} $case]
        continue
    }
    set bad 0
    set first_off -1
    for {set i 0} {$i < $words_out} {incr i} {
        set gw [lindex $got_words $i]
        set rw [lindex $ref_words $i]
        if {$gw ne $rw} {
            for {set b 0} {$b < 4} {incr b} {
                set gb [expr {"$gw" >> (8 * $b) & 0xFF}]
                set rb [expr {"$rw" >> (8 * $b) & 0xFF}]
                if {$gb != $rb} {
                    if {$first_off < 0} {
                        set first_off [expr {4 * $i + $b}]
                        puts [format "FIRST_DIFF offset=%d got=0x%02X ref=0x%02X" \
                                  $first_off $gb $rb]
                    }
                    incr bad
                }
            }
        }
    }
    if {$bad == 0} {
        puts "JTAG_LAYER_OK $case 0/$n_out"
        incr n_ok
        lappend summary "$case OK"
        lappend case_results [format {    {"case": "%s", "ok": true, "bytes_bad": 0, "bytes_total": %d, "polls": %d}} $case $n_out $polls]
    } else {
        puts "JTAG_LAYER_FAIL $case bad=$bad/$n_out first=$first_off"
        incr n_fail
        lappend summary "$case BAD=$bad"
        lappend case_results [format {    {"case": "%s", "ok": false, "bytes_bad": %d, "bytes_total": %d, "first_bad_byte": %d, "polls": %d}} $case $bad $n_out $first_off $polls]
    }
}

puts "---- SUMMARY ----"
puts "JTAG_TEST_OK $n_ok / [llength $case_list]"
puts "DETAIL: $summary"

# Machine-readable result (report/repo discipline: numbers must have a source
# file). Deliberately contains NO timing: JTAG mrd/mwr round trips are ms
# scale, so any elapsed figure from this path has no physical meaning.
set json_path [file join $REPO data results jtag_layer_test.json]
if {[catch {
    set fh [open $json_path w]
    puts $fh "{"
    puts $fh "  \"tool\": \"board/scripts/jtag_layer_test.tcl\","
    puts $fh "  \"path\": \"JTAG only, no operating system\","
    puts $fh "  \"generated_utc\": \"[clock format [clock seconds] -format %Y-%m-%dT%H:%M:%SZ -gmt 1]\","
    puts $fh "  \"bitfile\": \"board/overlay/conv_engine.bit\","
    puts $fh "  \"ps7_init\": \"PS7_INIT_OK\","
    puts $fh "  \"program\": \"PROGRAM_OK\","
    puts $fh "  \"cases\": \["
    set first 1
    foreach item $case_results {
        if {!$first} { puts $fh "," }
        set first 0
        puts $fh "    $item"
    }
    puts $fh "  \],"
    puts $fh "  \"cases_ok\": $n_ok,"
    puts $fh "  \"cases_total\": [llength $case_list],"
    puts $fh "  \"status\": \"[expr {$n_fail == 0 ? {JTAG_TEST_OK} : {JTAG_TEST_FAIL}}]\","
    puts $fh "  \"timing_note\": \"elapsed/GOPs intentionally absent: JTAG round trips are ms scale; only run_layer_test.py on a PYNQ boot measures throughput\""
    puts $fh "}"
    close $fh
    puts "RESULT_JSON $json_path"
} jerr]} {
    puts "WARN could not write result json: $jerr"
}

if {$n_fail > 0} { exit 1 }
exit 0
