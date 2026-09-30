# 频点协调服务

为临时演出的无线电设备组协调频点。录入场次可用的整数频点、设备组使用时段
（左闭右开）与组间干扰关系（正整数最小频点间距），服务在满足全部约束的前提
下规划频点分配，并发布为不可变的版本。

- 时段相交的干扰组，频点间距不小于其最小间距；同组全程只用一个频点；
- 规划时可锁定部分组的频点；
- 目标依次为：满足所有约束 → 最小化与前一发布版共有组的改频数（首次规划
  计零）→ 并列时按组编号升序的频点序列取字典序最小；
- 无解时返回 422 并说明涉及的锁定组或干扰关系，当前发布版不变；
- 发布须提交所见版本号 `base_version`，过期即返回 409；并发发布只有一个
  可成功；各发布版不可变，可读取分配结果及与前版差异；
- 起点不早于终点、引用未知组、锁定不可用频点等校验失败均返回 400 并拒绝
  整次操作。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

Compose 会启动 PostgreSQL 与应用两个容器；应用启动时自动等待数据库就绪并
初始化表结构（`schema.sql`），无需手工操作。

- API 地址：http://localhost:8000
- 交互式接口文档（Swagger UI）：http://localhost:8000/docs
- PostgreSQL：localhost:5432（用户/密码/库名均为 `freq` / `freq` / `freqdb`）

停止并清空数据：`docker compose down -v`

## 配置

| 环境变量       | 默认值                                          | 说明           |
| -------------- | ----------------------------------------------- | -------------- |
| `DATABASE_URL` | `postgresql://freq:freq@localhost:5432/freqdb`  | 数据库连接串   |

Compose 中已注入 `DATABASE_URL=postgresql://freq:freq@db:5432/freqdb`。

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
# 准备 PostgreSQL 并设置 DATABASE_URL，然后：
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试：

```bash
pip install pytest
python -m pytest tests/            # 求解器单元测试（无需数据库）
# 端到端集成检查（自动拉起嵌入式 PostgreSQL 与真实服务进程）：
pip install pgserver httpx
python tests/integration_check.py
```

## 数据模型与约定

- **场次（session）**：名称 + 可用整数频点集合；
- **设备组（group）**：使用时段 `[start, end)`，左闭右开，整数分钟；`id`
  即组编号（自增），规划的字典序比较按组编号升序；
- **干扰关系（interference）**：两个组 + 正整数最小频点间距，仅当两组时段
  相交时生效；
- **发布版（plan）**：场次内版本号从 1 递增，发布后不可变，记录分配结果、
  改频数与本次锁定。

## API 一览

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| POST | `/sessions` | 创建场次（登记可用频点集合） |
| GET  | `/sessions/{id}` | 场次详情（含组与干扰关系） |
| POST | `/sessions/{id}/groups` | 录入设备组及时段 |
| POST | `/sessions/{id}/interferences` | 录入干扰关系 |
| POST | `/sessions/{id}/plans` | 规划并发布新版本（携带 `base_version` 与 `locks`） |
| GET  | `/sessions/{id}/plans` | 列出全部发布版 |
| GET  | `/sessions/{id}/plans/{version}` | 读取指定发布版分配结果 |
| GET  | `/sessions/{id}/plans/{version}/diff` | 与前一版的差异 |

状态码约定：`201` 创建/发布成功；`400` 校验失败（起点不早于终点、引用未知
组、锁定不可用频点等）；`404` 资源不存在；`409` 版本冲突；`422` 约束不可
满足（当前发布版不变）。

## 请求示例

```bash
# 1. 创建场次：可用频点 1..6
curl -s -X POST http://localhost:8000/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name": "周六演出", "frequencies": [1, 2, 3, 4, 5, 6]}'
# => {"id":1,...}

# 2. 录入设备组（时段左闭右开，单位：分钟）
curl -s -X POST http://localhost:8000/sessions/1/groups \
  -H 'Content-Type: application/json' -d '{"name": "音响", "start": 0, "end": 120}'
curl -s -X POST http://localhost:8000/sessions/1/groups \
  -H 'Content-Type: application/json' -d '{"name": "灯光", "start": 60, "end": 180}'

# 3. 录入干扰关系：组 1 与组 2 最小间距 3
curl -s -X POST http://localhost:8000/sessions/1/interferences \
  -H 'Content-Type: application/json' \
  -d '{"group_a": 1, "group_b": 2, "min_separation": 3}'

# 4. 首次规划并发布（无发布版时 base_version 为 0）
curl -s -X POST http://localhost:8000/sessions/1/plans \
  -H 'Content-Type: application/json' \
  -d '{"base_version": 0, "locks": []}'
# => {"version":1,"changes":0,"assignments":[{"group_id":1,"frequency":1},
#     {"group_id":2,"frequency":4}],...}

# 5. 再次发布：基于版本 1，并锁定组 1 使用频点 2
curl -s -X POST http://localhost:8000/sessions/1/plans \
  -H 'Content-Type: application/json' \
  -d '{"base_version": 1, "locks": [{"group_id": 1, "frequency": 2}]}'

# 6. 读取发布版与差异
curl -s http://localhost:8000/sessions/1/plans/2
curl -s http://localhost:8000/sessions/1/plans/2/diff
```

无解时的响应示例（422，当前发布版不变）：

```json
{
  "detail": {
    "error": "unsatisfiable",
    "message": "约束不可满足，当前发布版不变",
    "cause": "locks",
    "locks": [{"group_id": 1, "frequency": 1}],
    "interferences": [{"group_a": 1, "group_b": 2, "min_separation": 3}]
  }
}
```

版本冲突时的响应示例（409）：

```json
{
  "detail": {
    "error": "version_conflict",
    "message": "所见版本已过期，请基于最新发布版重新提交",
    "current_version": 2
  }
}
```

也可以直接运行示例脚本（需服务已启动）：

```bash
bash examples/demo.sh
```

## 项目结构

```
app/main.py    HTTP API 与发布事务（行锁串行化并发发布）
app/solver.py  回溯 + 前向检查求解器；无解时给出不可约的锁定/干扰核心
app/db.py      连接池、启动重试与 schema 初始化
schema.sql     表结构（应用启动时幂等执行）
tests/         求解器单元测试与端到端集成检查
examples/      curl 请求示例
```
