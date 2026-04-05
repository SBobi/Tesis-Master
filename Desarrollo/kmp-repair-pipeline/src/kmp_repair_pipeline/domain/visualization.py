"""CodeCharta visualization domain types."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CCAttribute(BaseModel):
    rloc: int = 0
    mcc: int = 1
    impacted: int = 0
    impact_direct: int = 0
    impact_transitive: int = 0
    screen_impacted: int = 0
    screen_names: int = 0


class CCNode(BaseModel):
    name: str
    type: str = "File"
    attributes: CCAttribute = Field(default_factory=CCAttribute)
    children: list[CCNode] = Field(default_factory=list)


class CCProject(BaseModel):
    project_name: str
    api_version: str = "1.3"
    nodes: list[CCNode] = Field(default_factory=list)
    attribute_types: dict = Field(default_factory=dict)
