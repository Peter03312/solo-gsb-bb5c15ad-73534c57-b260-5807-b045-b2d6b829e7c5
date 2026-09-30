"""发布版之间的逐组安全调频路线求解。

现场设备通常不能一次把所有设备组切换到新发布版，因此一次只允许移动一个组。
移动后的频点仍必须属于本场次可用频点（可先调到其它可用频点作为临时频点），
且完整分配必须满足当前所有时段相交干扰关系的最小间距。

求解目标：
1. 路线步数最少；
2. 步数相同时，将每步的 ``(组编号, 新频点)`` 按顺序展开，取字典序最小。

第二项目标由 BFS 保证：每一层按动作 ``(group_id, new_frequency)`` 升序生成
后继，FIFO 队列中同一深度的状态也按其最小字典序路线排列，所以目标状态第一
次被发现时，对应的就是最短且字典序最小的路线。
"""

from __future__ import annotations

from collections import deque

from .solver import SolverLimitExceeded


def assignment_violations(assignment, group_ids, frequencies, edges):
    """检查一份完整分配在当前规则下的冲突，返回结构化冲突列表。"""
    available = set(frequencies)
    result = {
        "missing_groups": [],
        "unexpected_groups": [],
        "invalid_frequencies": [],
        "interference_violations": [],
    }

    group_set = set(group_ids)
    for group_id in sorted(set(assignment) - group_set):
        result["unexpected_groups"].append(group_id)
    for group_id in group_ids:
        frequency = assignment.get(group_id)
        if frequency is None:
            result["missing_groups"].append(group_id)
        elif frequency not in available:
            result["invalid_frequencies"].append(
                {"group_id": group_id, "frequency": frequency})

    for group_a, group_b, separation in edges:
        frequency_a = assignment.get(group_a)
        frequency_b = assignment.get(group_b)
        if frequency_a not in available or frequency_b not in available:
            continue
        actual = abs(frequency_a - frequency_b)
        if actual < separation:
            result["interference_violations"].append({
                "group_a": group_a,
                "group_b": group_b,
                "min_separation": separation,
                "frequency_a": frequency_a,
                "frequency_b": frequency_b,
                "actual_separation": actual,
            })
    return result


class TransitionSolver:
    """在两份均可行的完整分配之间搜索逐组调频路线。

    :param group_ids: 本场次当前全部组编号。
    :param frequencies: 本场次可用频点，临时频点也必须来自该集合。
    :param edges: 当前生效的干扰关系 ``(a, b, min_separation)``。
    :param start: 起始发布版完整分配 ``{group_id: frequency}``。
    :param target: 目标发布版完整分配 ``{group_id: frequency}``。
    :param fixed_groups: 切换期间不可调频的组；起止版频点必须已由调用方
        校验为相同。
    """

    def __init__(self, group_ids, frequencies, edges, start, target,
                 fixed_groups=(), max_states: int = 200_000):
        self.groups = sorted(set(group_ids))
        self.frequencies = sorted(set(frequencies))
        self.frequency_index = {f: i for i, f in enumerate(self.frequencies)}
        self.fixed = set(fixed_groups)
        self.start = self._to_state(start)
        self.target = self._to_state(target)
        self.max_states = max_states

        self.adj = {g: [] for g in self.groups}
        for group_a, group_b, separation in edges:
            if group_a in self.adj and group_b in self.adj:
                self.adj[group_a].append((group_b, separation))
                self.adj[group_b].append((group_a, separation))
        self.position = {g: i for i, g in enumerate(self.groups)}
        self.adj_positions = {
            self.position[g]: [(self.position[other], separation)
                               for other, separation in neighbors]
            for g, neighbors in self.adj.items()}

    def _to_state(self, assignment):
        return tuple(self.frequency_index[assignment[g]] for g in self.groups)

    def _to_assignment(self, state):
        return {g: self.frequencies[state[i]]
                for i, g in enumerate(self.groups)}

    def _move_is_valid(self, state, position, new_index):
        """只检查被移动组的邻接边；移动前状态本身已可行。"""
        group_id = self.groups[position]
        new_frequency = self.frequencies[new_index]
        for other_position, separation in self.adj_positions[position]:
            other_frequency = self.frequencies[state[other_position]]
            if abs(other_frequency - new_frequency) < separation:
                return False
        return True

    def _build_route(self, parents):
        actions = []
        states = []
        state = self.target
        while state != self.start:
            previous, group_id, new_frequency = parents[state]
            actions.append((group_id, new_frequency))
            states.append(state)
            state = previous

        states.append(self.start)
        states.reverse()
        actions.reverse()

        steps = []
        for step_number, ((group_id, new_frequency), before, after) in enumerate(
                zip(actions, states, states[1:]), start=1):
            position = self.position[group_id]
            steps.append({
                "step": step_number,
                "group_id": group_id,
                "from_frequency": self.frequencies[before[position]],
                "to_frequency": new_frequency,
                "assignment": self._to_assignment(after),
            })
        return steps

    def solve(self):
        """返回最短且字典序最小的路线；状态空间不连通时返回 None。"""
        if self.start == self.target:
            return []

        queue = deque([self.start])
        parents = {self.start: None}

        while queue:
            state = queue.popleft()

            # 变量按组编号升序、候选频点按频点升序，保证后继动作字典序。
            for position, group_id in enumerate(self.groups):
                if group_id in self.fixed:
                    continue
                old_index = state[position]
                for new_index, new_frequency in enumerate(self.frequencies):
                    if new_index == old_index:
                        continue
                    if not self._move_is_valid(state, position, new_index):
                        continue

                    candidate = list(state)
                    candidate[position] = new_index
                    candidate = tuple(candidate)
                    if candidate in parents:
                        continue

                    parents[candidate] = (state, group_id, new_frequency)
                    if candidate == self.target:
                        return self._build_route(parents)
                    if len(parents) >= self.max_states:
                        raise SolverLimitExceeded(
                            f"transition search exceeded {self.max_states} "
                            "states")
                    queue.append(candidate)

        return None
