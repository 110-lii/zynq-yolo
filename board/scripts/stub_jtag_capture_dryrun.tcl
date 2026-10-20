# =============================================================================
# stub_run.tcl -- dry-run harness for board/scripts/jtag_capture_test.tcl
#
# Runs the real JTAG script against *fake* hardware so its control flow, token
# discipline and compare wiring can be exercised without a board:
#   - register accesses go to a Tcl array instead of the DAP
#   - "DMA" completes instantly and the "DDR" dump is produced from the golden
#     bin (mode=pass) or from a golden with one byte flipped (mode=corrupt)
#
# Purpose: a pass is only credible if the same script provably FAILS on bad
# bytes (docs/repo_spec.md sec 5 discipline: the judge must be able to fail).
#
# Usage (from repo root):
#   xsct build/tmp_lead_stub/stub_run.tcl pass
#   xsct build/tmp_lead_stub/stub_run.tcl corrupt
# =============================================================================

set ::STUB_MODE [expr {[llength $argv] > 0 ? [lindex $argv 0] : "pass"}]
set ::argv [list --skip-program]
set script_dir [file dirname [info script]]
set repo       [file normalize [file join $script_dir .. ..]]
# ★ 桩的所有产物必须落 build/stub/，**不能**落在 board/scripts/（那是 git 跟踪的真源目录）：
#   第一版用 [file dirname [info script]]，结果在 board/scripts/ 下生成了假的
#   capture_chain.bit / capture_chain_addr.txt / STUB_NOT_A_BOARD_RESULT.json，
#   差点被当成真产物提交。桩的输出只能进 build/（.gitignore 已排除）。
set stub_dir [file join $repo build stub]
file mkdir $stub_dir

# fake bitstream + addr table (the real ones only exist after a BD build)
set fake_bit  [file join $stub_dir capture_chain.bit]
set fake_addr [file join $stub_dir capture_chain_addr.txt]
set fh [open $fake_bit w]; puts $fh "FAKE"; close $fh
set fh [open $fake_addr w]
puts $fh "dma_base=0x40400000"
puts $fh "gpio_base=0x41200000"
puts $fh "h_active=640"
puts $fh "v_active=480"
puts $fh "pixel_bytes=4"
puts $fh "frame_bytes=1228800"
close $fh

set ::env(BITFILE)  $fake_bit
set ::env(ADDRFILE) $fake_addr
set ::env(DUMP)     [file join $stub_dir capture_dump.bin]
set ::env(RESULT_JSON) [file join $stub_dir STUB_NOT_A_BOARD_RESULT.json]

# ---- fake hardware state ----------------------------------------------------
array set ::REG {0x40400030 0 0x40400034 0 0x40400048 0 0x40400058 0 0x41200000 0 0x41200008 0}
set ::ARMED 0
set ::DONE  0
set ::A_FRAME 0x20000000
set ::FRAME_BYTES 1228800
if {[info exists ::env(GOLDEN)]} { set g $::env(GOLDEN) } else { set g "" }

proc reg_addr {addr} { return [format 0x%08X [expr {$addr}]] }

proc stubs_init {} {
    proc connect {} { puts "STUB connect" }
    proc targets {args} { return "APU #0" }
    proc memmap {args} { puts "STUB memmap $args" }
    proc rst {args} { puts "STUB rst $args" }
    proc fpga {args} { puts "STUB fpga $args" }
    proc ps7_post_config {} { puts "STUB ps7_post_config" }
    proc dow {args} { puts "STUB dow $args" }
    proc after {ms args} { return "" }   ;# no real waiting in the dry run

    proc mwr {args} {
        set addr [reg_addr [lindex $args 1]]
        set val  [expr {[lindex $args 2]}]
        set ::REG($addr) $val
        switch -- $addr {
            0x40400030 {
                if {$val & 0x1} { set ::ARMED 1; puts "STUB DMACR.RS armed" }
            }
            0x40400034 {
                # W1C: drop the bits written as 1
                set cur $::REG(0x40400034)
                set ::REG(0x40400034) [expr {$cur & ~$val}]
            }
            0x41200000 {
                if {$val & 0x1} { puts "STUB pattern run=1" }
            }
        }
    }

    proc mrd {args} {
        if {[lindex $args 0] eq "-bin"} {
            # -bin -file <path> <addr> <count>
            set path  [lindex $args 2]
            set count [lindex $args 4]
            set fh [open $::env(GOLDEN) rb]
            fconfigure $fh -translation binary
            set bytes [read $fh [expr {$count * 4}]]
            close $fh
            if {$::STUB_MODE eq "corrupt"} {
                binary scan [string range $bytes 4001 4001] c v
                set bytes [string replace $bytes 4001 4001 \
                    [binary format c [expr {($v ^ 0xFF) & 0xFF}]]]
            }
            set out [open $path wb]
            fconfigure $out -translation binary
            puts -nonewline $out $bytes
            close $out
            return ""
        }
        set addr [reg_addr [lindex $args 0]]
        set val $::REG($addr)
        switch -- $addr {
            0x40400034 {
                if {$::ARMED && ($::REG(0x41200000) & 0x1)} {
                    # transfer completes; note errors are NOT modelled on purpose
                    set val [expr {$val | 0x1000}]
                    set ::REG(0x40400034) $val
                    set ::DONE 1
                    puts "STUB dma transfer complete (IOC set)"
                }
            }
            default {
                # frame region: after completion the "DMA" owns it, so read from
                # the golden bin instead of the sentinel values mwr stored
                set dec [expr {[lindex $args 0]}]
                if {$::DONE && $dec >= $::A_FRAME && $dec < ($::A_FRAME + $::FRAME_BYTES)} {
                    set off [expr {$dec - $::A_FRAME}]
                    set fh [open $::env(GOLDEN) rb]
                    fconfigure $fh -translation binary
                    seek $fh $off
                    set bytes [read $fh 4]
                    close $fh
                    if {$::STUB_MODE eq "corrupt" && $off == 4000} {
                        binary scan [string index $bytes 1] c v
                        set v [expr {($v ^ 0xFF) & 0xFF}]
                        set bytes [string replace $bytes 1 1 [binary format c $v]]
                    }
                    binary scan $bytes c4 bb
                    set val [expr {([lindex $bb 0] & 0xFF) | (([lindex $bb 1] & 0xFF) << 8) \
                                   | (([lindex $bb 2] & 0xFF) << 16) | (([lindex $bb 3] & 0xFF) << 24)}]
                }
            }
        }
        return [format "%08X:   %08X" [expr {$addr}] [expr {$val & 0xFFFFFFFF}]]
    }
}
stubs_init

set ::env(GOLDEN) [file join $repo build board capture_golden.bin]
puts "STUB_MODE $::STUB_MODE  GOLDEN=$::env(GOLDEN)"
source [file join $repo board scripts jtag_capture_test.tcl]