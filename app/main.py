"""临时演出无线电设备组频点协调服务（HTTP API）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import db
from .schemas import (GroupCreate, InterferenceCreate, PlanCreate,
                      SessionCreate)
from .solver import (FrequencySolver, SolverLimitExceeded, analyze_unsat,
                     intervals_overlap)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_pool()
    db.init_schema()
    yield
    db.close_pool()


app = FastAPI(
    title="频点协调服务",
    version="1.0.0",
    description="为临时演出的无线电设备组协调频点：录入场次、设备组时段与"
                "干扰关系，按约束规划并发布不可变的频点分配版本。",
    lifespan=lifespan,
)


def bad_request(message: str):
    return HTTPException(status_code=400, detail=message)


# --------------------------------------------------------------------- #
# 场次
# --------------------------------------------------------------------- #
@app.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, conn=Depends(db.get_conn)):
    """创建场次，登记可用的整数频点集合。"""
    frequencies = sorted(set(payload.frequencies))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (name, frequencies) VALUES (%s, %s) "
            "RETURNING id, created_at",
            (payload.name, frequencies))
        row = cur.fetchone()
    conn.commit()
    return {"id": row[0], "name": payload.name,
            "frequencies": frequencies, "created_at": row[1]}


def _load_session(conn, session_id: int, for_update: bool = False):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, frequencies, created_at FROM sessions "
            f"WHERE id = %s {'FOR UPDATE' if for_update else ''}",
            (session_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="场次不存在")
    return {"id": row[0], "name": row[1], "frequencies": list(row[2]),
            "created_at": row[3]}


def _load_groups(conn, session_id: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, start_min, end_min FROM groups "
            "WHERE session_id = %s ORDER BY id", (session_id,))
        return [{"id": r[0], "name": r[1], "start": r[2], "end": r[3]}
                for r in cur.fetchall()]


def _load_interferences(conn, session_id: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, group_a, group_b, min_separation FROM interferences "
            "WHERE session_id = %s ORDER BY id", (session_id,))
        return [{"id": r[0], "group_a": r[1], "group_b": r[2],
                 "min_separation": r[3]} for r in cur.fetchall()]


@app.get("/sessions/{session_id}")
def get_session(session_id: int, conn=Depends(db.get_conn)):
    """读取场次详情（含设备组与干扰关系）。"""
    session = _load_session(conn, session_id)
    session["groups"] = _load_groups(conn, session_id)
    session["interferences"] = _load_interferences(conn, session_id)
    return session


# --------------------------------------------------------------------- #
# 设备组与干扰关系
# --------------------------------------------------------------------- #
@app.post("/sessions/{session_id}/groups", status_code=201)
def create_group(session_id: int, payload: GroupCreate,
                 conn=Depends(db.get_conn)):
    """录入设备组及其使用时段（左闭右开）。"""
    _load_session(conn, session_id)
    if payload.start >= payload.end:
        raise bad_request("时段起点必须早于终点（左闭右开）")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (session_id, name, start_min, end_min) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (session_id, payload.name, payload.start, payload.end))
        group_id = cur.fetchone()[0]
    conn.commit()
    return {"id": group_id, "session_id": session_id, "name": payload.name,
            "start": payload.start, "end": payload.end}


@app.post("/sessions/{session_id}/interferences", status_code=201)
def create_interference(session_id: int, payload: InterferenceCreate,
                        conn=Depends(db.get_conn)):
    """录入两个设备组之间的干扰关系及最小频点间距。"""
    _load_session(conn, session_id)
    if payload.min_separation <= 0:
        raise bad_request("最小频点间距必须为正整数")
    if payload.group_a == payload.group_b:
        raise bad_request("干扰关系的两个组不能相同")
    groups = {g["id"] for g in _load_groups(conn, session_id)}
    unknown = [g for g in (payload.group_a, payload.group_b)
               if g not in groups]
    if unknown:
        raise bad_request(f"引用未知组: {unknown}")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO interferences (session_id, group_a, group_b, "
            "min_separation) VALUES (%s, %s, %s, %s) RETURNING id",
            (session_id, payload.group_a, payload.group_b,
             payload.min_separation))
        interference_id = cur.fetchone()[0]
    conn.commit()
    return {"id": interference_id, "session_id": session_id,
            "group_a": payload.group_a, "group_b": payload.group_b,
            "min_separation": payload.min_separation}


# --------------------------------------------------------------------- #
# 规划与发布
# --------------------------------------------------------------------- #
def _current_version(conn, session_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM plans "
                    "WHERE session_id = %s", (session_id,))
        return cur.fetchone()[0]


def _load_assignments(conn, plan_id: int) -> dict[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT group_id, frequency FROM plan_assignments "
                    "WHERE plan_id = %s ORDER BY group_id", (plan_id,))
        return {r[0]: r[1] for r in cur.fetchall()}


@app.post("/sessions/{session_id}/plans", status_code=201)
def publish_plan(session_id: int, payload: PlanCreate,
                 conn=Depends(db.get_conn)):
    """规划并发布新版本。

    须提交所见版本号 base_version：与当前最新发布版不一致时返回 409，
    并发发布只有一个可以成功。无解时返回 422 并说明涉及的锁定组或
    干扰关系，当前发布版不变。
    """
    # 锁定场次行，串行化并发发布
    session = _load_session(conn, session_id, for_update=True)
    current = _current_version(conn, session_id)
    if payload.base_version != current:
        raise HTTPException(
            status_code=409,
            detail={"error": "version_conflict",
                    "message": "所见版本已过期，请基于最新发布版重新提交",
                    "current_version": current})

    groups = _load_groups(conn, session_id)
    group_by_id = {g["id"]: g for g in groups}
    interferences = _load_interferences(conn, session_id)
    available = set(session["frequencies"])

    # 校验锁定：引用未知组 / 锁定不可用频点 / 重复锁定均拒绝整次操作
    locks: dict[int, int] = {}
    for lock in payload.locks:
        if lock.group_id not in group_by_id:
            raise bad_request(f"锁定引用了未知组: {lock.group_id}")
        if lock.frequency not in available:
            raise bad_request(
                f"组 {lock.group_id} 锁定的频点 {lock.frequency} "
                "不在场次可用频点集合中")
        if lock.group_id in locks:
            raise bad_request(f"组 {lock.group_id} 被重复锁定")
        locks[lock.group_id] = lock.frequency

    # 仅时段相交（左闭右开）的干扰关系生效
    edges = []
    for itf in interferences:
        ga, gb = group_by_id[itf["group_a"]], group_by_id[itf["group_b"]]
        if intervals_overlap(ga["start"], ga["end"],
                             gb["start"], gb["end"]):
            edges.append((itf["group_a"], itf["group_b"],
                          itf["min_separation"]))

    # 前一发布版的分配（首次规划时为空，改频数目标恒为 0）
    previous: dict[int, int] = {}
    if current > 0:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM plans WHERE session_id = %s "
                        "AND version = %s", (session_id, current))
            previous = _load_assignments(conn, cur.fetchone()[0])

    try:
        result = FrequencySolver(
            [g["id"] for g in groups], session["frequencies"], edges,
            previous).solve(locks)
    except SolverLimitExceeded:
        raise HTTPException(status_code=503,
                            detail="求解超出资源限制，请缩小规模后重试")

    if result is None:
        analysis = analyze_unsat([g["id"] for g in groups],
                                 session["frequencies"], edges, locks,
                                 previous)
        raise HTTPException(
            status_code=422,
            detail={"error": "unsatisfiable",
                    "message": "约束不可满足，当前发布版不变",
                    **analysis})

    assignment, changes = result
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO plans (session_id, version, changes) "
            "VALUES (%s, %s, %s) RETURNING id, created_at",
            (session_id, current + 1, changes))
        plan_id, created_at = cur.fetchone()
        cur.executemany(
            "INSERT INTO plan_assignments (plan_id, group_id, frequency) "
            "VALUES (%s, %s, %s)",
            [(plan_id, gid, freq) for gid, freq in assignment.items()])
        if locks:
            cur.executemany(
                "INSERT INTO plan_locks (plan_id, group_id, frequency) "
                "VALUES (%s, %s, %s)",
                [(plan_id, gid, freq) for gid, freq in locks.items()])
    conn.commit()
    return {
        "session_id": session_id,
        "version": current + 1,
        "changes": changes,
        "assignments": [{"group_id": gid, "frequency": assignment[gid]}
                        for gid in sorted(assignment)],
        "locks": [{"group_id": gid, "frequency": freq}
                  for gid, freq in sorted(locks.items())],
        "created_at": created_at,
    }


# --------------------------------------------------------------------- #
# 发布版查询（发布版不可变，仅提供读取）
# --------------------------------------------------------------------- #
def _load_plan(conn, session_id: int, version: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, version, changes, created_at FROM plans "
            "WHERE session_id = %s AND version = %s",
            (session_id, version))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="发布版不存在")
    return {"id": row[0], "version": row[1], "changes": row[2],
            "created_at": row[3]}


@app.get("/sessions/{session_id}/plans")
def list_plans(session_id: int, conn=Depends(db.get_conn)):
    """列出场次的全部发布版。"""
    _load_session(conn, session_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version, changes, created_at FROM plans "
            "WHERE session_id = %s ORDER BY version", (session_id,))
        return [{"version": r[0], "changes": r[1], "created_at": r[2]}
                for r in cur.fetchall()]


@app.get("/sessions/{session_id}/plans/{version}")
def get_plan(session_id: int, version: int, conn=Depends(db.get_conn)):
    """读取指定发布版的频点分配结果。"""
    _load_session(conn, session_id)
    plan = _load_plan(conn, session_id, version)
    assignments = _load_assignments(conn, plan["id"])
    with conn.cursor() as cur:
        cur.execute("SELECT group_id, frequency FROM plan_locks "
                    "WHERE plan_id = %s ORDER BY group_id", (plan["id"],))
        locks = [{"group_id": r[0], "frequency": r[1]}
                 for r in cur.fetchall()]
    return {
        "session_id": session_id,
        "version": plan["version"],
        "changes": plan["changes"],
        "assignments": [{"group_id": gid, "frequency": assignments[gid]}
                        for gid in sorted(assignments)],
        "locks": locks,
        "created_at": plan["created_at"],
    }


@app.get("/sessions/{session_id}/plans/{version}/diff")
def get_plan_diff(session_id: int, version: int, conn=Depends(db.get_conn)):
    """读取指定发布版与前一版的差异（改频 / 新增 / 移除的组）。"""
    _load_session(conn, session_id)
    plan = _load_plan(conn, session_id, version)
    current = _load_assignments(conn, plan["id"])
    previous: dict[int, int] = {}
    if version > 1:
        prev_plan = _load_plan(conn, session_id, version - 1)
        previous = _load_assignments(conn, prev_plan["id"])

    changed, added, removed = [], [], []
    unchanged = 0
    for gid in sorted(current):
        if gid not in previous:
            added.append({"group_id": gid, "frequency": current[gid]})
        elif previous[gid] != current[gid]:
            changed.append({"group_id": gid,
                            "old_frequency": previous[gid],
                            "new_frequency": current[gid]})
        else:
            unchanged += 1
    for gid in sorted(previous):
        if gid not in current:
            removed.append({"group_id": gid,
                            "old_frequency": previous[gid]})
    return {
        "session_id": session_id,
        "version": version,
        "previous_version": version - 1 if version > 1 else None,
        "changed": changed,
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
    }
