-- 频点协调服务数据库结构
-- 应用启动时自动执行（幂等），无需手工初始化。

CREATE TABLE IF NOT EXISTS sessions (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    frequencies  INTEGER[] NOT NULL,          -- 场次可用的整数频点集合（升序、去重）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS groups (
    id           SERIAL PRIMARY KEY,          -- 组编号（自增，规划时按此升序）
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name         TEXT NOT NULL DEFAULT '',
    start_min    INTEGER NOT NULL,            -- 使用时段起点（分钟，左闭）
    end_min      INTEGER NOT NULL,            -- 使用时段终点（分钟，右开）
    CHECK (start_min < end_min)
);

CREATE TABLE IF NOT EXISTS interferences (
    id             SERIAL PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    group_a        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    group_b        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    min_separation INTEGER NOT NULL,          -- 正整数最小频点间距
    CHECK (group_a <> group_b),
    CHECK (min_separation > 0)
);

-- 发布版：每个场次内版本号从 1 单调递增，发布后不可变
CREATE TABLE IF NOT EXISTS plans (
    id           SERIAL PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    changes      INTEGER NOT NULL,            -- 相对前一发布版共有组的改频数
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, version)
);

CREATE TABLE IF NOT EXISTS plan_assignments (
    plan_id      INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    group_id     INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    frequency    INTEGER NOT NULL,
    PRIMARY KEY (plan_id, group_id)
);

-- 规划时使用的锁定（随发布版留存，便于追溯）
CREATE TABLE IF NOT EXISTS plan_locks (
    plan_id      INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    group_id     INTEGER NOT NULL,
    frequency    INTEGER NOT NULL,
    PRIMARY KEY (plan_id, group_id)
);
