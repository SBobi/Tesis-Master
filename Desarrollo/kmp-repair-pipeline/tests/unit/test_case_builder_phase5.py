"""Unit tests for Phase 5 — case builder (repo_cloner + case_factory)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.case_builder.repo_cloner import (
    ClonerError,
    _run,
    is_git_repo,
)


# ---------------------------------------------------------------------------
# repo_cloner — _run helper
# ---------------------------------------------------------------------------


class TestRunHelper:
    def test_run_returns_stdout_when_capture(self, tmp_path: Path) -> None:
        result = _run(["echo", "hello"], capture=True)
        assert "hello" in result

    def test_run_raises_on_nonzero(self) -> None:
        with pytest.raises(ClonerError, match="Command failed"):
            _run(["false"])

    def test_run_no_capture_returns_empty(self) -> None:
        result = _run(["true"], capture=False)
        assert result == ""


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    def test_true_when_git_dir_present(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert is_git_repo(tmp_path) is True

    def test_false_when_no_git_dir(self, tmp_path: Path) -> None:
        assert is_git_repo(tmp_path) is False

    def test_false_for_nonexistent_path(self, tmp_path: Path) -> None:
        assert is_git_repo(tmp_path / "does-not-exist") is False


# ---------------------------------------------------------------------------
# clone_at_ref — patched (no real network)
# ---------------------------------------------------------------------------


class TestCloneAtRef:
    def test_reuses_existing_clone(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.case_builder.repo_cloner import clone_at_ref

        dest = tmp_path / "repo"
        dest.mkdir()
        # No git dir but overwrite=False → just returns existing path
        result = clone_at_ref("https://github.com/x/y", "abc123", dest, overwrite=False)
        assert result == dest

    def test_overwrite_removes_and_reclones(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.case_builder.repo_cloner import clone_at_ref

        dest = tmp_path / "repo"
        dest.mkdir()
        (dest / "old_file.txt").write_text("old")

        with patch("kmp_repair_pipeline.case_builder.repo_cloner._run") as mock_run:
            mock_run.return_value = ""
            result = clone_at_ref(
                "https://github.com/x/y", "abc123", dest, overwrite=True
            )
        assert result == dest
        # old file should be gone (rmtree was called)
        assert not (dest / "old_file.txt").exists()

    def test_clone_calls_git(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.case_builder.repo_cloner import clone_at_ref

        dest = tmp_path / "fresh"
        with patch("kmp_repair_pipeline.case_builder.repo_cloner._run") as mock_run:
            mock_run.return_value = ""
            clone_at_ref("https://github.com/x/y", "deadbeef", dest)

        calls = [c.args[0] for c in mock_run.call_args_list]
        # Should have issued a `git clone` and a `git checkout`
        assert any("clone" in cmd for cmd in calls)
        assert any("checkout" in cmd for cmd in calls)


# ---------------------------------------------------------------------------
# clone_before_after
# ---------------------------------------------------------------------------


class TestCloneBeforeAfter:
    def test_returns_two_paths(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.case_builder.repo_cloner import clone_before_after

        with patch("kmp_repair_pipeline.case_builder.repo_cloner._run") as mock_run:
            mock_run.return_value = ""
            before, after = clone_before_after(
                "https://github.com/x/y",
                "base000",
                "head111",
                tmp_path,
            )

        assert before == (tmp_path / "before").resolve()
        assert after == (tmp_path / "after").resolve()

    def test_propagates_cloner_error(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.case_builder.repo_cloner import clone_before_after

        with patch("kmp_repair_pipeline.case_builder.repo_cloner._run") as mock_run:
            mock_run.side_effect = ClonerError("network down")
            with pytest.raises(ClonerError):
                clone_before_after("https://github.com/x/y", "b", "h", tmp_path)


# ---------------------------------------------------------------------------
# case_factory — _resolve_shas (patched GitHub calls)
# ---------------------------------------------------------------------------


class TestResolveShas:
    def _make_bundle(self, pr_ref: str = "pull/3") -> MagicMock:
        bundle = MagicMock()
        bundle.meta.repository_url = "https://github.com/acme/kmp-app"
        bundle.update_evidence.update_event.pr_ref = pr_ref
        return bundle

    def _make_session(self, base_sha: str | None = None, head_sha: str | None = None):
        """Return a session mock with optionally pre-populated revision rows."""
        session = MagicMock()
        rev_repo = MagicMock()

        if base_sha and head_sha:
            before_rev = MagicMock()
            before_rev.git_sha = base_sha
            after_rev = MagicMock()
            after_rev.git_sha = head_sha
            rev_repo.get.side_effect = lambda _cid, rtype: (
                before_rev if rtype == "before" else after_rev
            )
        else:
            rev_repo.get.return_value = None

        return session, rev_repo

    def test_returns_shas_from_db_when_present(self) -> None:
        from kmp_repair_pipeline.case_builder.case_factory import _resolve_shas

        bundle = self._make_bundle()

        with patch(
            "kmp_repair_pipeline.case_builder.case_factory.RevisionRepo"
        ) as MockRevRepo:
            before_rev = MagicMock()
            before_rev.git_sha = "BASE000"
            after_rev = MagicMock()
            after_rev.git_sha = "HEAD111"
            MockRevRepo.return_value.get.side_effect = lambda rtype: (
                before_rev if rtype == "before" else after_rev
            )

            # Patch the call signature to accept (case_id, type)
            instance = MockRevRepo.return_value
            instance.get.side_effect = lambda case_id, rtype: (
                before_rev if rtype == "before" else after_rev
            )

            session = MagicMock()
            base, head = _resolve_shas("case-1", bundle, session)

        assert base == "BASE000"
        assert head == "HEAD111"

    def test_fetches_from_github_when_no_db_shas(self) -> None:
        from kmp_repair_pipeline.case_builder.case_factory import _resolve_shas

        bundle = self._make_bundle(pr_ref="pull/5")

        with (
            patch("kmp_repair_pipeline.case_builder.case_factory.RevisionRepo") as MockRevRepo,
            patch("kmp_repair_pipeline.ingest.github_client.get") as mock_get,
        ):
            MockRevRepo.return_value.get.return_value = None
            mock_get.return_value = {
                "base": {"sha": "AAABBBCCC"},
                "head": {"sha": "DDDEEEFFF"},
            }

            session = MagicMock()
            base, head = _resolve_shas("case-1", bundle, session)

        assert base == "AAABBBCCC"
        assert head == "DDDEEEFFF"
        mock_get.assert_called_once()
