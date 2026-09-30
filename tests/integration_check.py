#!/usr/bin/env python3
"""端到端集成检查：嵌入式 PostgreSQL + 真实 uvicorn 进程。

开发期使用，不属于生产部署。运行方式：
    pip install pgserver httpx uvicorn fastapi psycopg2-binary
    python tests/integration_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

import httpx

BASE = "http://127.0.0.1:18000"
_failures: list[str] = []


def check(name: str, cond: bool, extra: str = ""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        _failures.append(name)


def main() -> int:
    import pgserver

    pgdir = tempfile.mkdtemp(prefix="pgtest-")
    srv = pgserver.get_server(pgdir)
    os.environ["DATABASE_URL"] = srv.get_uri()
    print("postgres:", os.environ["DATABASE_URL"])

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", "18000"],
        env={**os.environ})
    try:
        for _ in range(60):
            try:
                httpx.get(f"{BASE}/docs", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("server failed to start")
            return 1

        c = httpx.Client(base_url=BASE, timeout=10)

        # 1. 创建场次
        r = c.post("/sessions", json={"name": "周六演出",
                                      "frequencies": [1, 2, 3, 4, 5, 6]})
        check("创建场次", r.status_code == 201, r.text)
        sid = r.json()["id"]

        r = c.post("/sessions", json={"name": "空频点", "frequencies": []})
        check("空频点集合被拒绝(422 校验)", r.status_code == 422)

        # 2. 录入设备组
        r = c.post(f"/sessions/{sid}/groups",
                   json={"name": "音响", "start": 0, "end": 120})
        g1 = r.json()["id"]
        check("录入组1", r.status_code == 201, r.text)
        r = c.post(f"/sessions/{sid}/groups",
                   json={"name": "灯光", "start": 60, "end": 180})
        g2 = r.json()["id"]
        r = c.post(f"/sessions/{sid}/groups",
                   json={"name": "返送", "start": 120, "end": 240})
        g3 = r.json()["id"]
        check("录入组2/组3", r.status_code == 201)

        r = c.post(f"/sessions/{sid}/groups",
                   json={"name": "坏时段", "start": 10, "end": 10})
        check("起点不早于终点被拒绝(400)", r.status_code == 400, r.text)

        # 3. 干扰关系
        r = c.post(f"/sessions/{sid}/interferences",
                   json={"group_a": g1, "group_b": g2, "min_separation": 3})
        check("录入干扰关系", r.status_code == 201, r.text)
        r = c.post(f"/sessions/{sid}/interferences",
                   json={"group_a": g1, "group_b": 9999, "min_separation": 2})
        check("引用未知组被拒绝(400)", r.status_code == 400, r.text)
        r = c.post(f"/sessions/{sid}/interferences",
                   json={"group_a": g1, "group_b": g2, "min_separation": 0})
        check("非正间距被拒绝(400)", r.status_code == 400)

        # 4. 首次发布（改频数计零，字典序最小）
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 0, "locks": []})
        check("首次发布", r.status_code == 201, r.text)
        body = r.json()
        check("首版改频数为 0", body["changes"] == 0)
        a = {x["group_id"]: x["frequency"] for x in body["assignments"]}
        check("相交干扰组满足间距", abs(a[g1] - a[g2]) >= 3, str(a))
        check("字典序最小", (a[g1], a[g2], a[g3]) == (1, 4, 1), str(a))

        # 5. 旧版本发布 → 409
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 0, "locks": []})
        check("旧版本请求返回冲突(409)", r.status_code == 409, r.text)

        # 6. 锁定不可用频点 → 400，整次拒绝
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 1,
                         "locks": [{"group_id": g1, "frequency": 99}]})
        check("锁定不可用频点被拒绝(400)", r.status_code == 400, r.text)
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 1,
                         "locks": [{"group_id": 9999, "frequency": 1}]})
        check("锁定未知组被拒绝(400)", r.status_code == 400)

        # 7. 无解：锁定 g1=1 且 g2=2（间距 3）→ 422，发布版不变
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 1, "locks": [
                       {"group_id": g1, "frequency": 1},
                       {"group_id": g2, "frequency": 2}]})
        check("无解返回 422", r.status_code == 422, r.text)
        detail = r.json()["detail"]
        check("无解说明涉及锁定组",
              detail["cause"] == "locks"
              and {l["group_id"] for l in detail["locks"]} == {g1, g2},
              str(detail))
        r = c.get(f"/sessions/{sid}/plans")
        check("无解后发布版不变", [p["version"] for p in r.json()] == [1])

        # 8. 正常第二版：锁定 g1=2，检验最小改频
        r = c.post(f"/sessions/{sid}/plans",
                   json={"base_version": 1,
                         "locks": [{"group_id": g1, "frequency": 2}]})
        check("第二次发布", r.status_code == 201, r.text)
        body = r.json()
        a2 = {x["group_id"]: x["frequency"] for x in body["assignments"]}
        check("锁定生效", a2[g1] == 2, str(a2))
        check("最小改频", a2[g2] == 5 and a2[g3] == 1
              and body["changes"] == 2, f"{a2} changes={body['changes']}")

        # 9. 读取发布版与差异
        r = c.get(f"/sessions/{sid}/plans/2")
        check("读取发布版 2", r.status_code == 200
              and r.json()["version"] == 2, r.text)
        r = c.get(f"/sessions/{sid}/plans/2/diff")
        diff = r.json()
        check("差异：改频组正确",
              {d["group_id"] for d in diff["changed"]} == {g1, g2}
              and diff["unchanged"] == 1, str(diff))
        r = c.get(f"/sessions/{sid}/plans/1/diff")
        check("首版差异全部为新增",
              len(r.json()["added"]) == 3 and r.json()["changed"] == [])
        r = c.get(f"/sessions/{sid}/plans/99")
        check("读取不存在的发布版(404)", r.status_code == 404)

        # 10. 并发发布：只有一个可成功
        results = []

        def publish():
            rr = httpx.post(f"{BASE}/sessions/{sid}/plans",
                            json={"base_version": 2, "locks": []},
                            timeout=30)
            results.append(rr.status_code)

        threads = [threading.Thread(target=publish) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("并发发布只有一个成功",
              results.count(201) == 1 and results.count(409) == 5,
              str(results))
        r = c.get(f"/sessions/{sid}/plans")
        check("并发后版本号为 3", [p["version"] for p in r.json()] == [1, 2, 3])

        print()
        if _failures:
            print(f"{len(_failures)} 项失败: {_failures}")
            return 1
        print("全部集成检查通过")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
