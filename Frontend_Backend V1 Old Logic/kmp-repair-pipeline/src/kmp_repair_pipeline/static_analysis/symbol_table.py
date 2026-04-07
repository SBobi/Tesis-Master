"""Symbol table: maps FQCN → file path for all parsed declarations."""

from __future__ import annotations

from ..domain.analysis import FileParseResult
from ..utils.log import get_logger

log = get_logger(__name__)


class SymbolTable:
    """Maps fully qualified class names to their source files."""

    def __init__(self) -> None:
        self._fqcn_to_file: dict[str, list[str]] = {}
        self._file_to_package: dict[str, str] = {}

    def build(self, parse_results: list[FileParseResult]) -> None:
        for result in parse_results:
            self._file_to_package[result.file_path] = result.package
            for decl in result.declarations:
                self._fqcn_to_file.setdefault(decl.fqcn, []).append(result.file_path)

        log.info(f"Symbol table: {len(self._fqcn_to_file)} symbols from {len(parse_results)} files")

    def files_for_fqcn(self, fqcn: str) -> list[str]:
        return self._fqcn_to_file.get(fqcn, [])

    def package_for_file(self, file_path: str) -> str:
        return self._file_to_package.get(file_path, "")

    def all_fqcns(self) -> list[str]:
        return list(self._fqcn_to_file.keys())

    def resolve_import(self, import_str: str) -> list[str]:
        """Resolve an import to file paths. Handles wildcard imports."""
        if import_str.endswith(".*"):
            prefix = import_str[:-2]
            files: list[str] = []
            for fqcn, paths in self._fqcn_to_file.items():
                if fqcn.startswith(prefix + "."):
                    files.extend(paths)
            return list(set(files))
        return self._fqcn_to_file.get(import_str, [])
