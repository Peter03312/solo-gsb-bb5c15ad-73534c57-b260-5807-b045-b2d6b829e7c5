"""逐组调频路线求解器单元测试。"""

from app.solver import SolverLimitExceeded
from app.transition import TransitionSolver, assignment_violations


def actions(route):
    return [(step["group_id"], step["to_frequency"]) for step in route]


class TestAssignmentViolations:
    def test_detects_missing_invalid_and_conflict(self):
        result = assignment_violations(
            {1: 1, 3: 1},
            [1, 2, 3],
            [1, 4],
            [(1, 2, 4), (1, 3, 4)])
        assert result["missing_groups"] == [2]
        assert result["unexpected_groups"] == []
        assert result["invalid_frequencies"] == []
        assert result["interference_violations"][0]["group_b"] == 3

    def test_reports_missing_and_unexpected_groups(self):
        result = assignment_violations(
            {1: 1, 4: 1}, [1, 2], [1, 4], [])
        assert result["missing_groups"] == [2]
        assert result["unexpected_groups"] == [4]

    def test_invalid_frequency_not_reported_as_interference(self):
        result = assignment_violations(
            {1: 9, 2: 1}, [1, 2], [1, 4], [(1, 2, 4)])
        assert result["invalid_frequencies"] == [
            {"group_id": 1, "frequency": 9}]
        assert result["interference_violations"] == []


class TestTransitionSolver:
    def test_identical_assignment_has_zero_steps(self):
        assignment = {1: 1, 2: 4}
        solver = TransitionSolver(
            [1, 2], [1, 4, 7], [(1, 2, 3)], assignment, dict(assignment))
        assert solver.solve() == []

    def test_uses_temporary_frequency_and_returns_complete_states(self):
        solver = TransitionSolver(
            [1, 2], [1, 4, 7], [(1, 2, 3)],
            {1: 1, 2: 4}, {1: 4, 2: 1})
        route = solver.solve()

        # 两条最短路线：
        # (1,7),(2,1),(1,4) 或 (2,7),(1,4),(2,1)，前者字典序更小。
        assert actions(route) == [(1, 7), (2, 1), (1, 4)]
        assert route[0]["from_frequency"] == 1
        assert route[0]["assignment"] == {1: 7, 2: 4}
        assert route[-1]["assignment"] == {1: 4, 2: 1}

    def test_direct_safe_move_is_one_step(self):
        solver = TransitionSolver(
            [1, 2], [1, 4, 7], [(1, 2, 3)],
            {1: 1, 2: 7}, {1: 4, 2: 7})
        assert actions(solver.solve()) == [(1, 4)]

    def test_fixed_groups_can_block_route(self):
        solver = TransitionSolver(
            [1, 2], [1, 4, 7], [(1, 2, 3)],
            {1: 1, 2: 4}, {1: 4, 2: 1}, fixed_groups=[2])
        assert solver.solve() is None

    def test_fixed_group_can_remain_unchanged(self):
        solver = TransitionSolver(
            [1, 2], [1, 4, 7], [(1, 2, 3)],
            {1: 1, 2: 7}, {1: 4, 2: 7}, fixed_groups=[2])
        assert actions(solver.solve()) == [(1, 4)]

    def test_disconnected_state_graph_has_no_safe_route(self):
        edges = [(1, 2, 4), (2, 3, 4), (1, 3, 4)]
        solver = TransitionSolver(
            [1, 2, 3], [1, 4], edges,
            {1: 1, 2: 4, 3: 1}, {1: 4, 2: 1, 3: 4})
        assert solver.solve() is None

    def test_non_overlapping_interference_not_in_edges(self):
        # edges 由 API 按左闭右开时段过滤；空边表示组 1、3 互不干扰。
        solver = TransitionSolver(
            [1, 3], [1, 4], [], {1: 1, 3: 1}, {1: 4, 3: 4})
        assert actions(solver.solve()) == [(1, 4), (3, 4)]

    def test_state_limit_is_enforced(self):
        solver = TransitionSolver(
            [1, 2, 3], [1, 4], [],
            {1: 1, 2: 1, 3: 1}, {1: 4, 2: 4, 3: 4},
            max_states=3)
        try:
            solver.solve()
        except SolverLimitExceeded:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected SolverLimitExceeded")
