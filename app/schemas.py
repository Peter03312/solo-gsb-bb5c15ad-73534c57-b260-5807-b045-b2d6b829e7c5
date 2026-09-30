"""请求体的 Pydantic 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    name: str = Field(min_length=1)
    frequencies: list[int] = Field(
        min_length=1, description="场次可用的整数频点集合")


class GroupCreate(BaseModel):
    name: str = ""
    start: int = Field(description="使用时段起点（分钟，左闭）")
    end: int = Field(description="使用时段终点（分钟，右开）")


class InterferenceCreate(BaseModel):
    group_a: int
    group_b: int
    min_separation: int = Field(description="正整数最小频点间距")


class Lock(BaseModel):
    group_id: int
    frequency: int


class PlanCreate(BaseModel):
    base_version: int = Field(
        ge=0, description="所见版本号；无发布版时为 0，否则为最新发布版号")
    locks: list[Lock] = Field(default_factory=list)


class RetuneRequest(BaseModel):
    start_version: int = Field(
        ge=1, description="起始发布版版本号（同一场次内）")
    target_version: int = Field(
        ge=1, description="目标发布版版本号（同一场次内）")
    fixed_groups: list[int] = Field(
        default_factory=list,
        description="切换期间不可调频的组编号；其在两版中的频点必须相同")
