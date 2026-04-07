"""Kotlin source parser using tree-sitter with regex fallback.

Ported from prototype phase2_static/kotlin_parser.py with package rename.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..domain.analysis import DeclarationKind, FileParseResult, KotlinDeclaration
from ..utils.log import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Try to load tree-sitter; fall back to regex if unavailable
# ---------------------------------------------------------------------------

_ts_kotlin_language = None

try:
    import tree_sitter_kotlin as tskotlin
    from tree_sitter import Language, Parser

    _ts_kotlin_language = Language(tskotlin.language())
except Exception:
    log.warning("tree-sitter-kotlin unavailable, using regex fallback")

# ---------------------------------------------------------------------------
# Source-set inference
# ---------------------------------------------------------------------------

# Non-greedy so "commonMain" → group(1)="common", suffix="Main"
_SOURCE_SET_PATTERN = re.compile(r"/src/(\w+?)(?:Main|Test)/")

_SOURCE_SET_MAPPING: dict[str, str] = {
    "common": "common",
    "android": "android",
    "ios": "ios",
    "jvm": "jvm",
    "js": "js",
    "desktop": "desktop",
    "native": "native",
    "apple": "apple",
    "linux": "linux",
    "windows": "windows",
    "mingw": "native",
    "macos": "macos",
}


def _infer_source_set(path: str) -> str:
    m = _SOURCE_SET_PATTERN.search(path.replace("\\", "/"))
    if m:
        raw = m.group(1)
        return _SOURCE_SET_MAPPING.get(raw, raw)
    return "common"


# ---------------------------------------------------------------------------
# Tree-sitter based parser
# ---------------------------------------------------------------------------


def _parse_with_tree_sitter(source: bytes, file_path: str) -> FileParseResult:
    parser = Parser(_ts_kotlin_language)
    tree = parser.parse(source)
    root = tree.root_node

    package = ""
    imports: list[str] = []
    declarations: list[KotlinDeclaration] = []
    source_set = _infer_source_set(file_path)

    def _text(node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _qualified_id_text(node) -> str:
        parts = []
        for child in node.children:
            if child.type == "identifier":
                parts.append(_text(child))
        return ".".join(parts)

    def _walk(node) -> None:
        nonlocal package

        if node.type == "package_header":
            for child in node.children:
                if child.type == "qualified_identifier":
                    package = _qualified_id_text(child)
                    break

        elif node.type == "import":
            for child in node.children:
                if child.type == "qualified_identifier":
                    imports.append(_qualified_id_text(child))
                    break

        elif node.type in (
            "class_declaration",
            "object_declaration",
            "interface_declaration",
            "function_declaration",
            "type_alias",
            "property_declaration",
        ):
            _extract_declaration(node)
            return

        for child in node.children:
            _walk(child)

    def _extract_declaration(node) -> None:
        name = ""
        is_expect = False
        is_actual = False

        kind_map = {
            "class_declaration": DeclarationKind.CLASS,
            "object_declaration": DeclarationKind.OBJECT,
            "interface_declaration": DeclarationKind.INTERFACE,
            "function_declaration": DeclarationKind.FUNCTION,
            "type_alias": DeclarationKind.TYPEALIAS,
            "property_declaration": DeclarationKind.PROPERTY,
        }
        kind = kind_map.get(node.type, DeclarationKind.CLASS)

        for child in node.children:
            if child.type == "modifiers":
                mod_text = _text(child)
                if "expect" in mod_text:
                    is_expect = True
                if "actual" in mod_text:
                    is_actual = True
            elif child.type in ("identifier", "type_identifier", "simple_identifier"):
                if not name:
                    name = _text(child)

        if name:
            fqcn = f"{package}.{name}" if package else name
            declarations.append(
                KotlinDeclaration(
                    kind=kind,
                    name=name,
                    fqcn=fqcn,
                    is_expect=is_expect,
                    is_actual=is_actual,
                    source_set=source_set,
                    file_path=file_path,
                )
            )

    _walk(root)

    return FileParseResult(
        file_path=file_path,
        package=package,
        imports=imports,
        declarations=declarations,
        source_set=source_set,
    )


# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------

_PACKAGE_RE = re.compile(r"^package\s+([\w.]+)", re.MULTILINE)
_IMPORT_RE = re.compile(r"^import\s+([\w.]+(?:\.\*)?)", re.MULTILINE)
_DECL_RE = re.compile(
    r"^(?:(?P<modifiers>(?:(?:expect|actual|abstract|open|sealed|data|internal|private|public|protected|override|inline|suspend|annotation|enum|value)\s+)*)"
    r"(?P<kind>class|object|interface|fun|typealias|val|var)\s+(?P<name>\w+))",
    re.MULTILINE,
)


def _parse_with_regex(source: str, file_path: str) -> FileParseResult:
    source_set = _infer_source_set(file_path)

    pkg_match = _PACKAGE_RE.search(source)
    package = pkg_match.group(1) if pkg_match else ""

    imports = [m.group(1) for m in _IMPORT_RE.finditer(source)]

    declarations: list[KotlinDeclaration] = []
    kind_map = {
        "class": DeclarationKind.CLASS,
        "object": DeclarationKind.OBJECT,
        "interface": DeclarationKind.INTERFACE,
        "fun": DeclarationKind.FUNCTION,
        "typealias": DeclarationKind.TYPEALIAS,
        "val": DeclarationKind.PROPERTY,
        "var": DeclarationKind.PROPERTY,
    }

    for m in _DECL_RE.finditer(source):
        modifiers = m.group("modifiers") or ""
        kind = kind_map.get(m.group("kind"), DeclarationKind.CLASS)
        name = m.group("name")
        fqcn = f"{package}.{name}" if package else name

        declarations.append(
            KotlinDeclaration(
                kind=kind,
                name=name,
                fqcn=fqcn,
                is_expect="expect" in modifiers,
                is_actual="actual" in modifiers,
                source_set=source_set,
                file_path=file_path,
            )
        )

    return FileParseResult(
        file_path=file_path,
        package=package,
        imports=imports,
        declarations=declarations,
        source_set=source_set,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SKIP_DIRS = frozenset(
    ("build", ".gradle", ".idea", "generated", "tools", ".venv", "output", "output-local")
)


def parse_kotlin_file(file_path: str | Path) -> FileParseResult:
    """Parse a single Kotlin file, returning package, imports, and declarations."""
    p = Path(file_path)
    content = p.read_text(encoding="utf-8", errors="replace")

    if _ts_kotlin_language is not None:
        try:
            return _parse_with_tree_sitter(content.encode("utf-8"), str(p))
        except Exception as e:
            log.warning(f"tree-sitter failed for {p.name}, falling back to regex: {e}")

    return _parse_with_regex(content, str(p))


def parse_project(root: Path) -> list[FileParseResult]:
    """Parse all .kt files in a project directory."""
    results = []
    for kt_file in sorted(root.rglob("*.kt")):
        parts = kt_file.parts
        if any(skip in parts for skip in _SKIP_DIRS):
            continue
        try:
            results.append(parse_kotlin_file(kt_file))
        except Exception as e:
            log.warning(f"Failed to parse {kt_file}: {e}")
    return results
