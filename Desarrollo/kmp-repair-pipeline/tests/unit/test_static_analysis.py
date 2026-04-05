"""Unit tests for static analysis — parser, symbol table, expect/actual."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kmp_repair_pipeline.domain.analysis import DeclarationKind, ImpactRelation
from kmp_repair_pipeline.static_analysis.kotlin_parser import (
    _infer_source_set,
    _parse_with_regex,
    parse_kotlin_file,
)
from kmp_repair_pipeline.static_analysis.symbol_table import SymbolTable
from kmp_repair_pipeline.static_analysis.expect_actual import ExpectActualResolver
from kmp_repair_pipeline.static_analysis.source_metrics import compute_metrics


SIMPLE_KT = textwrap.dedent("""\
    package com.example.app

    import io.ktor.client.HttpClient
    import io.ktor.client.engine.cio.CIO

    class ApiClient {
        fun fetch(): String = ""
    }
""")

EXPECT_KT = textwrap.dedent("""\
    package com.example.platform

    expect class PlatformInfo {
        fun name(): String
    }
""")

ACTUAL_ANDROID_KT = textwrap.dedent("""\
    package com.example.platform

    actual class PlatformInfo {
        actual fun name(): String = "Android"
    }
""")


class TestSourceSetInference:
    def test_common_main(self) -> None:
        assert _infer_source_set("/project/src/commonMain/kotlin/App.kt") == "common"

    def test_android_main(self) -> None:
        assert _infer_source_set("/project/src/androidMain/kotlin/App.kt") == "android"

    def test_ios_main(self) -> None:
        assert _infer_source_set("/project/src/iosMain/kotlin/App.kt") == "ios"

    def test_default_to_common(self) -> None:
        # Paths without Main/Test suffix don't match the pattern → default "common"
        assert _infer_source_set("/project/src/kotlin/App.kt") == "common"


class TestRegexParser:
    def test_parse_package(self) -> None:
        result = _parse_with_regex(SIMPLE_KT, "App.kt")
        assert result.package == "com.example.app"

    def test_parse_imports(self) -> None:
        result = _parse_with_regex(SIMPLE_KT, "App.kt")
        assert "io.ktor.client.HttpClient" in result.imports

    def test_parse_class_declaration(self) -> None:
        result = _parse_with_regex(SIMPLE_KT, "App.kt")
        classes = [d for d in result.declarations if d.kind == DeclarationKind.CLASS]
        assert any(d.name == "ApiClient" for d in classes)

    def test_expect_modifier(self) -> None:
        result = _parse_with_regex(EXPECT_KT, "src/commonMain/kotlin/PlatformInfo.kt")
        decl = next(d for d in result.declarations if d.name == "PlatformInfo")
        assert decl.is_expect is True
        assert decl.is_actual is False

    def test_actual_modifier(self) -> None:
        result = _parse_with_regex(ACTUAL_ANDROID_KT, "src/androidMain/kotlin/PlatformInfo.kt")
        decl = next(d for d in result.declarations if d.name == "PlatformInfo")
        assert decl.is_actual is True
        assert decl.is_expect is False


class TestSymbolTable:
    def test_build_and_resolve(self) -> None:
        from kmp_repair_pipeline.domain.analysis import FileParseResult, KotlinDeclaration

        decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="ApiClient",
            fqcn="com.example.app.ApiClient",
        )
        pr = FileParseResult(
            file_path="App.kt",
            package="com.example.app",
            declarations=[decl],
        )
        table = SymbolTable()
        table.build([pr])
        assert table.files_for_fqcn("com.example.app.ApiClient") == ["App.kt"]
        assert table.package_for_file("App.kt") == "com.example.app"

    def test_wildcard_resolve(self) -> None:
        from kmp_repair_pipeline.domain.analysis import FileParseResult, KotlinDeclaration

        decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="Foo",
            fqcn="com.example.Foo",
        )
        pr = FileParseResult(file_path="Foo.kt", package="com.example", declarations=[decl])
        table = SymbolTable()
        table.build([pr])
        files = table.resolve_import("com.example.*")
        assert "Foo.kt" in files


class TestExpectActualResolver:
    def test_pairs_expect_and_actual(self) -> None:
        from kmp_repair_pipeline.domain.analysis import FileParseResult, KotlinDeclaration

        expect_decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="PlatformInfo",
            fqcn="com.example.platform.PlatformInfo",
            is_expect=True,
        )
        actual_decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="PlatformInfo",
            fqcn="com.example.platform.PlatformInfo",
            is_actual=True,
        )
        common_pr = FileParseResult(
            file_path="src/commonMain/kotlin/PlatformInfo.kt",
            package="com.example.platform",
            declarations=[expect_decl],
        )
        android_pr = FileParseResult(
            file_path="src/androidMain/kotlin/PlatformInfo.kt",
            package="com.example.platform",
            declarations=[actual_decl],
        )
        resolver = ExpectActualResolver()
        resolver.build([common_pr, android_pr])
        assert len(resolver.pairs) == 1
        pair = resolver.pairs[0]
        assert pair.expect_file == "src/commonMain/kotlin/PlatformInfo.kt"
        assert "src/androidMain/kotlin/PlatformInfo.kt" in pair.actual_files

    def test_get_linked_files(self) -> None:
        from kmp_repair_pipeline.domain.analysis import FileParseResult, KotlinDeclaration

        expect_decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="PlatformInfo",
            fqcn="com.example.platform.PlatformInfo",
            is_expect=True,
        )
        actual_decl = KotlinDeclaration(
            kind=DeclarationKind.CLASS,
            name="PlatformInfo",
            fqcn="com.example.platform.PlatformInfo",
            is_actual=True,
        )
        common_pr = FileParseResult(
            file_path="common/PlatformInfo.kt",
            package="com.example.platform",
            declarations=[expect_decl],
        )
        android_pr = FileParseResult(
            file_path="android/PlatformInfo.kt",
            package="com.example.platform",
            declarations=[actual_decl],
        )
        resolver = ExpectActualResolver()
        resolver.build([common_pr, android_pr])
        linked = resolver.get_linked_files("common/PlatformInfo.kt")
        assert "android/PlatformInfo.kt" in linked


class TestSourceMetrics:
    def test_nonexistent_file(self) -> None:
        m = compute_metrics("/nonexistent/path/Foo.kt")
        assert m.rloc == 0
        assert m.mcc == 1

    def test_real_file(self, tmp_path: Path) -> None:
        kt = tmp_path / "App.kt"
        kt.write_text(SIMPLE_KT)
        m = compute_metrics(str(kt))
        assert m.rloc > 0
        assert m.functions >= 1
