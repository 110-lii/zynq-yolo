#!/bin/bash
cd /home/xilinx
end=$((SECONDS + 1200))
n=0
while [ $SECONDS -lt $end ]; do
  ./bench_ncnn person_v8n25.param person_v8n25.bin 2 5 >/dev/null 2>&1
  n=$((n + 1))
done
echo "DONE rounds=$n" > /tmp/stress_done
