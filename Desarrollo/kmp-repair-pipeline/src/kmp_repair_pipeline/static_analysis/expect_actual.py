"""Expect/actual resolution for Kotlin Multiplatform declarations."""

from __future__ import annotations

from ..domain.analysis import ExpectActualPair, FileParseResult
from ..utils.log import get_logger

log = get_logger(__name__)


class ExpectActualResolver:
    """Pairs expect declarations with their actual implementations by FQCN."""

    def __init__(self) -> None:
        self.pairs: list[ExpectActualPair] = []
        self._expect_map: dict[str, str] = {}
        self._actual_map: dict[str, list[str]] = {}

    def build(self, parse_results: list[FileParseResult]) -> None:
        for result in parse_results:
            for decl in result.declarations:
                if decl.is_expect:
                    self._expect_map[decl.fqcn] = result.file_path
                if decl.is_actual:
                    self._actual_map.setdefault(decl.fqcn, []).append(result.file_path)

        for fqcn, expect_file in self._expect_map.items():
            actual_files = self._actual_map.get(fqcn, [])
            self.pairs.append(
                ExpectActualPair(
                    expect_fqcn=fqcn,
                    expect_file=expect_file,
                    actual_files=actual_files,
                )
            )

        log.info(f"Expect/actual pairs: {len(self.pairs)}")

    def get_linked_files(self, file_path: str) -> set[str]:
        """Given a file, return all files linked via expect/actual."""
        linked: set[str] = set()
        for pair in self.pairs:
            files_in_pair = {pair.expect_file} | set(pair.actual_files)
            if file_path in files_in_pair:
                linked |= files_in_pair
        linked.discard(file_path)
        return linked

    def is_expect_file(self, file_path: str) -> bool:
        return any(p.expect_file == file_path for p in self.pairs)

    def is_actual_file(self, file_path: str) -> bool:
        return any(file_path in p.actual_files for p in self.pairs)
