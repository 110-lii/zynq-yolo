# 板上单层验收 conv_layer_test（20211118_0452）

- 命令: `python3 src/ps/host/run_layer_test.py --repeat 10`
- 完整日志: 同名 `.log`（板上与 tar 归档）
- 判据令牌: BOARD_LAYER_OK / BOARD_LAYER_FAIL

```
OVERLAY_LOADED
WINDOWS conv_engine windows: s_axi_control=conv_engine_0/s_axi_control @0x40000000  s_axi_control_r=conv_engine_0/s_axi_control_r @0x40010000
RUN k1s1_smallw iter=0 elapsed_ns=7992649 ap_done=1
RUN k1s1_smallw iter=1 elapsed_ns=7832622 ap_done=1
RUN k1s1_smallw iter=2 elapsed_ns=7827812 ap_done=1
RUN k1s1_smallw iter=3 elapsed_ns=7858845 ap_done=1
RUN k1s1_smallw iter=4 elapsed_ns=7975842 ap_done=1
RUN k1s1_smallw iter=5 elapsed_ns=7833047 ap_done=1
RUN k1s1_smallw iter=6 elapsed_ns=7975346 ap_done=1
RUN k1s1_smallw iter=7 elapsed_ns=7843881 ap_done=1
RUN k1s1_smallw iter=8 elapsed_ns=7993734 ap_done=1
RUN k1s1_smallw iter=9 elapsed_ns=7922742 ap_done=1
CASE_OK k1s1_smallw bytes=1600 elapsed_ns=7827812 gops=0.0065
RUN k1s1_tailcout iter=0 elapsed_ns=43461486 ap_done=1
RUN k1s1_tailcout iter=1 elapsed_ns=43505359 ap_done=1
RUN k1s1_tailcout iter=2 elapsed_ns=43451917 ap_done=1
RUN k1s1_tailcout iter=3 elapsed_ns=43443282 ap_done=1
RUN k1s1_tailcout iter=4 elapsed_ns=43553396 ap_done=1
RUN k1s1_tailcout iter=5 elapsed_ns=43427255 ap_done=1
RUN k1s1_tailcout iter=6 elapsed_ns=43434154 ap_done=1
RUN k1s1_tailcout iter=7 elapsed_ns=43450727 ap_done=1
RUN k1s1_tailcout iter=8 elapsed_ns=43451813 ap_done=1
RUN k1s1_tailcout iter=9 elapsed_ns=43500021 ap_done=1
CASE_OK k1s1_tailcout bytes=8000 elapsed_ns=43427255 gops=0.0118
RUN k3s1_c3_40 iter=0 elapsed_ns=5956455 ap_done=1
RUN k3s1_c3_40 iter=1 elapsed_ns=5938255 ap_done=1
RUN k3s1_c3_40 iter=2 elapsed_ns=5897794 ap_done=1
RUN k3s1_c3_40 iter=3 elapsed_ns=5914669 ap_done=1
RUN k3s1_c3_40 iter=4 elapsed_ns=5946715 ap_done=1
RUN k3s1_c3_40 iter=5 elapsed_ns=5911158 ap_done=1
RUN k3s1_c3_40 iter=6 elapsed_ns=5899438 ap_done=1
RUN k3s1_c3_40 iter=7 elapsed_ns=5928736 ap_done=1
RUN k3s1_c3_40 iter=8 elapsed_ns=5882948 ap_done=1
RUN k3s1_c3_40 iter=9 elapsed_ns=5894679 ap_done=1
CASE_OK k3s1_c3_40 bytes=25600 elapsed_ns=5882948 gops=0.1175
RUN k3s1_cin512 iter=0 elapsed_ns=133502144 ap_done=1
RUN k3s1_cin512 iter=1 elapsed_ns=133500477 ap_done=1
RUN k3s1_cin512 iter=2 elapsed_ns=133543191 ap_done=1
RUN k3s1_cin512 iter=3 elapsed_ns=133452268 ap_done=1
RUN k3s1_cin512 iter=4 elapsed_ns=133764380 ap_done=1
RUN k3s1_cin512 iter=5 elapsed_ns=133587560 ap_done=1
RUN k3s1_cin512 iter=6 elapsed_ns=133483721 ap_done=1
RUN k3s1_cin512 iter=7 elapsed_ns=133500803 ap_done=1
RUN k3s1_cin512 iter=8 elapsed_ns=133593993 ap_done=1
RUN k3s1_cin512 iter=9 elapsed_ns=133625444 ap_done=1
CASE_OK k3s1_cin512 bytes=1600 elapsed_ns=133452268 gops=0.0552
RUN k3s1_pad0 iter=0 elapsed_ns=8814162 ap_done=1
RUN k3s1_pad0 iter=1 elapsed_ns=8780969 ap_done=1
RUN k3s1_pad0 iter=2 elapsed_ns=8743871 ap_done=1
RUN k3s1_pad0 iter=3 elapsed_ns=8753552 ap_done=1
RUN k3s1_pad0 iter=4 elapsed_ns=8684977 ap_done=1
RUN k3s1_pad0 iter=5 elapsed_ns=8657234 ap_done=1
RUN k3s1_pad0 iter=6 elapsed_ns=8810724 ap_done=1
RUN k3s1_pad0 iter=7 elapsed_ns=8653471 ap_done=1
RUN k3s1_pad0 iter=8 elapsed_ns=8780142 ap_done=1
RUN k3s1_pad0 iter=9 elapsed_ns=8784688 ap_done=1
CASE_OK k3s1_pad0 bytes=5184 elapsed_ns=8653471 gops=0.0863
RUN k3s2_c16_40 iter=0 elapsed_ns=22785420 ap_done=1
RUN k3s2_c16_40 iter=1 elapsed_ns=22788376 ap_done=1
RUN k3s2_c16_40 iter=2 elapsed_ns=22838849 ap_done=1
RUN k3s2_c16_40 iter=3 elapsed_ns=22863689 ap_done=1
RUN k3s2_c16_40 iter=4 elapsed_ns=22883403 ap_done=1
RUN k3s2_c16_40 iter=5 elapsed_ns=22893172 ap_done=1
RUN k3s2_c16_40 iter=6 elapsed_ns=22874056 ap_done=1
RUN k3s2_c16_40 iter=7 elapsed_ns=22846858 ap_done=1
RUN k3s2_c16_40 iter=8 elapsed_ns=23971892 ap_done=1
RUN k3s2_c16_40 iter=9 elapsed_ns=23349183 ap_done=1
CASE_OK k3s2_c16_40 bytes=12800 elapsed_ns=22785420 gops=0.0809
RESULT_JSON /home/xilinx/zynq-yolo/data/results/board_layer_test.json
BOARD_LAYER_OK all_6_cases 222029174 0.0505
```
