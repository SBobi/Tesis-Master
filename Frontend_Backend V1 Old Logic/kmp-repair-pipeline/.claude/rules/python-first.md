# Rule: Python-first architecture

- All pipeline, agent, and tooling code is written in Python ≥ 3.10.
- Do not rewrite any module in Kotlin, Go, or another language.
- A small Kotlin/JVM helper is allowed only when Python cannot invoke the required JVM API directly (e.g., Gradle tooling API). Document the reason before writing it.
- Keep Kotlin helpers isolated under a named subdirectory (e.g., `helpers/gradle-probe/`).
