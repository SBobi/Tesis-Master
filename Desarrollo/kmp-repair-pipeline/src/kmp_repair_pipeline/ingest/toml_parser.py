"""Parser for Gradle libs.versions.toml version catalogs.

Ported from prototype phase1_shadow/toml_parser.py with package rename only.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..utils.log import get_logger

log = get_logger(__name__)

_SECTION_RE = re.compile(r"^\[(\w+)]")
_KV_RE = re.compile(r'^(\S+)\s*=\s*"([^"]*)"')
_LIB_MODULE_RE = re.compile(
    r"""^(\S+)\s*=\s*\{\s*module\s*=\s*"([^"]+)"\s*,\s*version\.ref\s*=\s*"([^"]+)"\s*\}"""
)
_LIB_GROUP_RE = re.compile(
    r"""^(\S+)\s*=\s*\{\s*group\s*=\s*"([^"]+)"\s*,\s*name\s*=\s*"([^"]+)"\s*,\s*version\.ref\s*=\s*"([^"]+)"\s*\}"""
)
_PLUGIN_RE = re.compile(
    r"""^(\S+)\s*=\s*\{\s*id\s*=\s*"([^"]+)"\s*,\s*version\.ref\s*=\s*"([^"]+)"\s*\}"""
)


class VersionCatalog:
    """Parsed representation of a libs.versions.toml file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.versions: dict[str, str] = {}
        self.libraries: dict[str, dict] = {}
        self.plugins: dict[str, dict] = {}
        self._lines: list[str] = []
        self._parse()

    def _parse(self) -> None:
        self._lines = self.path.read_text(encoding="utf-8").splitlines()
        section = ""
        for line in self._lines:
            stripped = line.strip()
            sec_match = _SECTION_RE.match(stripped)
            if sec_match:
                section = sec_match.group(1)
                continue
            if not stripped or stripped.startswith("#"):
                continue

            if section == "versions":
                kv = _KV_RE.match(stripped)
                if kv:
                    self.versions[kv.group(1)] = kv.group(2)

            elif section == "libraries":
                m = _LIB_MODULE_RE.match(stripped)
                if m:
                    alias, module, version_ref = m.group(1), m.group(2), m.group(3)
                    group = module.rsplit(":", 1)[0] if ":" in module else module
                    self.libraries[alias] = {
                        "group": group,
                        "module": module,
                        "version_ref": version_ref,
                    }
                    continue
                m2 = _LIB_GROUP_RE.match(stripped)
                if m2:
                    alias = m2.group(1)
                    self.libraries[alias] = {
                        "group": m2.group(2),
                        "module": f"{m2.group(2)}:{m2.group(3)}",
                        "version_ref": m2.group(4),
                    }
            elif section == "plugins":
                plugin = _PLUGIN_RE.match(stripped)
                if plugin:
                    alias, plugin_id, version_ref = plugin.group(1), plugin.group(2), plugin.group(3)
                    self.plugins[alias] = {
                        "id": plugin_id,
                        "version_ref": version_ref,
                    }

    def find_version_key(self, dependency_group: str) -> str | None:
        """Find the version-ref key used by a dependency group."""
        for _alias, info in self.libraries.items():
            if info["group"] == dependency_group:
                return info["version_ref"]
        for _alias, info in self.plugins.items():
            if info["id"] == dependency_group:
                return info["version_ref"]
        return None

    def get_version(self, key: str) -> str | None:
        return self.versions.get(key)

    def set_version(self, key: str, new_version: str) -> None:
        """Update a version in-memory and write back to file."""
        old_version = self.versions.get(key)
        if old_version is None:
            raise KeyError(f"Version key '{key}' not found in catalog")

        self.versions[key] = new_version
        pattern = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)"[^"]*"')
        new_lines = []
        for line in self._lines:
            m = pattern.match(line)
            if m:
                new_lines.append(f'{m.group(1)}"{new_version}"')
            else:
                new_lines.append(line)
        self._lines = new_lines
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
        log.info(f"Updated {key}: {old_version} → {new_version}")
