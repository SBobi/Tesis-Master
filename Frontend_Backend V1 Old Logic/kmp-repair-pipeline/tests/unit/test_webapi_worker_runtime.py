from __future__ import annotations

import os

from kmp_repair_pipeline.webapi.worker import _bootstrap_runtime_if_needed, configure_worker_runtime


def test_configure_worker_runtime_non_darwin_keeps_env(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)

    configure_worker_runtime(system_name="Linux", java_21_home_resolver=lambda: "/fake/jdk21")

    assert os.environ.get("JAVA_HOME") is None
    assert os.environ.get("OBJC_DISABLE_INITIALIZE_FORK_SAFETY") is None
    assert os.environ["PATH"] == "/usr/bin"


def test_configure_worker_runtime_darwin_sets_objc_and_java_home(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)

    configure_worker_runtime(
        system_name="Darwin",
        java_21_home_resolver=lambda: "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home",
    )

    assert os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"
    assert os.environ["JAVA_HOME"] == "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home"
    assert os.environ["PATH"].split(":")[0] == "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin"


def test_configure_worker_runtime_darwin_without_jdk21_keeps_java_home(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/old/java/bin:/usr/bin")
    monkeypatch.setenv("JAVA_HOME", "/old/java")
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)

    configure_worker_runtime(system_name="Darwin", java_21_home_resolver=lambda: None)

    assert os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"
    assert os.environ["JAVA_HOME"] == "/old/java"
    assert os.environ["PATH"].split(":")[0] == "/old/java/bin"


def test_bootstrap_runtime_non_darwin_no_reexec(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("KMP_WORKER_BOOTSTRAPPED", raising=False)

    should_reexec = _bootstrap_runtime_if_needed(
        system_name="Linux",
        java_21_home_resolver=lambda: "/fake/jdk21",
    )

    assert should_reexec is False
    assert os.environ.get("KMP_WORKER_BOOTSTRAPPED") is None


def test_bootstrap_runtime_darwin_requests_reexec_when_env_changes(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)
    monkeypatch.delenv("KMP_WORKER_BOOTSTRAPPED", raising=False)

    should_reexec = _bootstrap_runtime_if_needed(
        system_name="Darwin",
        java_21_home_resolver=lambda: "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home",
    )

    assert should_reexec is True
    assert os.environ["KMP_WORKER_BOOTSTRAPPED"] == "1"
    assert os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"


def test_bootstrap_runtime_darwin_already_bootstrapped_no_reexec(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("KMP_WORKER_BOOTSTRAPPED", "1")
    monkeypatch.delenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", raising=False)

    should_reexec = _bootstrap_runtime_if_needed(
        system_name="Darwin",
        java_21_home_resolver=lambda: None,
    )

    assert should_reexec is False
    assert os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"
