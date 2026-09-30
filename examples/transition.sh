#!/usr/bin/env bash
# 逐组安全调频路线示例（需服务已启动，默认 http://localhost:8000）
# 场景：两组相交且最小间距 3，可用频点 1/4/7。
# 版本 1=(1,4)，版本 2=(4,1)；不能直接交换，必须有一组先临时切到 7。
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"

echo "== 创建场次：可用频点 1、4、7 =="
SESSION=$(curl -s -X POST "$BASE/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"name":"逐组调频示例","frequencies":[1,4,7]}')
echo "$SESSION"
SID=$(echo "$SESSION" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

echo "== 录入两个全程相交的设备组 =="
curl -s -X POST "$BASE/sessions/$SID/groups" \
  -H 'Content-Type: application/json' \
  -d '{"name":"A","start":0,"end":120}'; echo
curl -s -X POST "$BASE/sessions/$SID/groups" \
  -H 'Content-Type: application/json' \
  -d '{"name":"B","start":0,"end":120}'; echo

echo "== 录入最小间距 3 的干扰关系 =="
curl -s -X POST "$BASE/sessions/$SID/interferences" \
  -H 'Content-Type: application/json' \
  -d '{"group_a":1,"group_b":2,"min_separation":3}'; echo

echo "== 发布版本 1：(A=1, B=4) =="
curl -s -X POST "$BASE/sessions/$SID/plans" \
  -H 'Content-Type: application/json' \
  -d '{"base_version":0,"locks":[]}'; echo

echo "== 发布版本 2：(A=4, B=1) =="
curl -s -X POST "$BASE/sessions/$SID/plans" \
  -H 'Content-Type: application/json' \
  -d '{"base_version":1,"locks":[
    {"group_id":1,"frequency":4},
    {"group_id":2,"frequency":1}
  ]}'; echo

echo "== 查询版本 1 -> 2 的最短且字典序最小安全路线 =="
curl -s -X POST "$BASE/sessions/$SID/transitions" \
  -H 'Content-Type: application/json' \
  -d '{"start_version":1,"target_version":2,"fixed_groups":[]}'; echo
