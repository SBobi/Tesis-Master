"""Static and structural analysis domain types.

Ported from the prototype's contracts.py (Phase 2 section) with minor
extensions for thesis architecture alignment.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DeclarationKind(str, Enum):
    CLASS = "class"
    OBJECT = "object"
    INTERFACE = "interface"
    FUNCTION = "fun"
    TYPEALIAS = "typealias"
    PROPERTY = "property"


class KotlinDeclaration(BaseModel):
    kind: DeclarationKind
    name: str
    fqcn: str
    is_expect: bool = False
    is_actual: bool = False
    source_set: str = "common"
    file_path: str = ""


class FileParseResult(BaseModel):
    file_path: str
    package: str = ""
    imports: list[str] = Field(default_factory=list)
    declarations: list[KotlinDeclaration] = Field(default_factory=list)
    source_set: str = "common"


class SourceMetrics(BaseModel):
    rloc: int = 0
    functions: int = 0
    mcc: int = 1


class ImpactRelation(str, Enum):
    DIRECT = "direct"
    TRANSITIVE = "transitive"
    EXPECT_ACTUAL = "expect_actual"


class FileImpact(BaseModel):
    file_path: str
    relation: ImpactRelation
    distance: int = 0
    imports_from_dependency: list[str] = Field(default_factory=list)
    metrics: SourceMetrics = Field(default_factory=SourceMetrics)
    declarations: list[str] = Field(default_factory=list)
    source_set: str = "common"


class ExpectActualPair(BaseModel):
    expect_fqcn: str
    expect_file: str
    actual_files: list[str] = Field(default_factory=list)


class ImpactGraph(BaseModel):
    dependency_group: str
    version_before: str
    version_after: str
    seed_files: list[str] = Field(default_factory=list)
    impacted_files: list[FileImpact] = Field(default_factory=list)
    expect_actual_pairs: list[ExpectActualPair] = Field(default_factory=list)
    total_project_files: int = 0
    total_impacted: int = 0
