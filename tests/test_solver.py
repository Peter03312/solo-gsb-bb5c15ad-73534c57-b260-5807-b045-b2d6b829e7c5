"""求解器单元测试：可脱离数据库直接运行（python -m pytest tests/）。"""

from app.solver import (FrequencySolver, analyze_unsat, intervals_overlap)


class TestIntervalsOverlap:
    def test_overlap(self):
        assert intervals_overlap(0, 10, 5, 15)
        assert intervals_overlap(5, 15, 0, 10)
        assert intervals_overlap(0, 10, 0, 10)

    def test_half_open_boundary(self):
        # 左闭右开：[0,10) 与 [10,20) 不相交
        assert not intervals_overlap(0, 10, 10, 20)
        assert not intervals_overlap(10, 20, 0, 10)


class TestSolve:
    def test_basic_lexicographic_minimum(self):
        # 两组干扰、间距 3：字典序最小解为 (1, 4)
        solver = FrequencySolver([1, 2], [1, 2, 3, 4, 5], [(1, 2, 3)])
        assignment, changes = solver.solve()
        assert (assignment[1], assignment[2]) == (1, 4)
        assert changes == 0  # 首次规划计零

    def test_lock_forces_value(self):
        solver = FrequencySolver([1, 2], [1, 2, 3, 4, 5], [(1, 2, 3)])
        assignment, _ = solver.solve({2: 1})
        assert assignment[2] == 1
        assert assignment[1] == 4

    def test_unsatisfiable(self):
        solver = FrequencySolver([1, 2], [1, 2], [(1, 2, 5)])
        assert solver.solve() is None

    def test_locked_unavailable_frequency_unsatisfiable(self):
        solver = FrequencySolver([1], [1, 2, 3], [])
        assert solver.solve({1: 9}) is None

    def test_minimize_rechanges(self):
        # 前版 (1, 4) 已可行：改频数必须为 0 且保持原分配
        solver = FrequencySolver([1, 2], [1, 2, 3, 4, 5], [(1, 2, 3)],
                                 previous={1: 1, 2: 4})
        assignment, changes = solver.solve()
        assert changes == 0
        assert assignment == {1: 1, 2: 4}

    def test_minimal_rechanges_when_forced(self):
        # 锁定组 1 为 2，前版 (1, 4)：组 2 必须改频，改频数为 1
        solver = FrequencySolver([1, 2], [1, 2, 3, 4, 5], [(1, 2, 3)],
                                 previous={1: 1, 2: 4})
        assignment, changes = solver.solve({1: 2})
        assert assignment[1] == 2
        assert assignment[2] == 5
        assert changes == 2  # 组 1、组 2 均偏离前版

    def test_lexicographic_tie_break_with_previous(self):
        # 前版组 1 为 3（已不在可用集合中，必须改频）；无干扰。
        # 改频数最小为 1，并列中按组编号升序取频点最小：组 1 取 10。
        solver = FrequencySolver([1, 2, 3], [10, 20, 30], [],
                                 previous={1: 3, 2: 20, 3: 30})
        assignment, changes = solver.solve()
        assert assignment == {1: 10, 2: 20, 3: 30}
        assert changes == 1

    def test_removed_group_not_counted(self):
        # 前版有组 9，当前不参与规划：不计入改频数
        solver = FrequencySolver([1], [5, 6], [], previous={1: 5, 9: 5})
        assignment, changes = solver.solve()
        assert assignment == {1: 5}
        assert changes == 0


class TestAnalyzeUnsat:
    def test_interference_core(self):
        # 三组两两间距 2、频点 {1,2,3}：任意两条关系可行，三条齐备则不可行
        edges = [(1, 2, 2), (1, 3, 2), (2, 3, 2)]
        result = analyze_unsat([1, 2, 3], [1, 2, 3], edges, {})
        assert result["cause"] == "interferences"
        assert len(result["interferences"]) == 3
        # 核心不可约：移除任一关系后应可解
        for kept in result["interferences"]:
            rest = [e for e in result["interferences"] if e != kept]
            solver = FrequencySolver(
                [1, 2, 3], [1, 2, 3],
                [(e["group_a"], e["group_b"], e["min_separation"])
                 for e in rest])
            assert solver.solve() is not None

    def test_lock_core(self):
        # 无锁定可行；单独锁定组 1=1 或组 2=2 均可行，同时锁定则无解
        edges = [(1, 2, 3)]
        locks = {1: 1, 2: 2}
        result = analyze_unsat([1, 2], [1, 2, 3, 4, 5], edges, locks)
        assert result["cause"] == "locks"
        assert {l["group_id"] for l in result["locks"]} == {1, 2}
        assert result["interferences"] == [
            {"group_a": 1, "group_b": 2, "min_separation": 3}]

    def test_lock_core_excludes_irrelevant_locks(self):
        # 组 3 的锁定与无解无关，不应出现在核心中
        edges = [(1, 2, 3)]
        locks = {1: 1, 2: 2, 3: 1}
        result = analyze_unsat([1, 2, 3], [1, 2, 3, 4, 5], edges, locks)
        assert result["cause"] == "locks"
        assert {l["group_id"] for l in result["locks"]} == {1, 2}
