"use client";

import { useEffect, useMemo, useState } from "react";

import { getEnvironment } from "@/lib/api";
import { EnvironmentSnapshot } from "@/lib/types";

type CheckLevel = "validated" | "executed" | "action";

function itemTone(level: CheckLevel) {
  if (level === "validated") {
    return {
      dot: "dot dot-ok",
      text: "status-success",
      label: "VALIDATED",
      marker: "✓",
    };
  }

  if (level === "action") {
    return {
      dot: "dot dot-bad",
      text: "status-error",
      label: "ACTION REQUIRED",
      marker: "⚠",
    };
  }

  return {
    dot: "dot",
    text: "text-[var(--muted)]",
    label: "EXECUTED",
    marker: "•",
  };
}

export default function EnvironmentPage() {
  const [snapshot, setSnapshot] = useState<EnvironmentSnapshot | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [javaHome, setJavaHome] = useState("");
  const [androidHome, setAndroidHome] = useState("");
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [credentialsPath, setCredentialsPath] = useState("");
  const [llmProvider, setLlmProvider] = useState("anthropic");
  const [llmModel, setLlmModel] = useState("");
  const [vertexProject, setVertexProject] = useState("");
  const [vertexLocation, setVertexLocation] = useState("us-central1");
  const [analysisTimeout, setAnalysisTimeout] = useState(600);
  const [validateTimeout, setValidateTimeout] = useState(600);
  const [localizeTopK, setLocalizeTopK] = useState(10);
  const [repairTopK, setRepairTopK] = useState(5);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        const environment = await getEnvironment();
        if (!mounted) return;
        setSnapshot(environment);

        setJavaHome(environment.paths.java_home || "");
        setAndroidHome(environment.paths.android_home || environment.paths.android_sdk_root || "");
        setDatabaseUrl(environment.paths.kmp_database_url || "");
        setCredentialsPath(environment.paths.google_application_credentials || "");
        setLlmProvider(environment.llm.provider || "anthropic");
        setLlmModel(environment.llm.model || "");
        setVertexProject(environment.llm.vertex_project || "");
        setVertexLocation(environment.llm.vertex_location || "us-central1");

        setAnalysisTimeout(environment.defaults.run_before_after_timeout_s);
        setValidateTimeout(environment.defaults.validate_timeout_s);
        setLocalizeTopK(environment.defaults.localize_top_k);
        setRepairTopK(environment.defaults.repair_top_k);
      } catch (err) {
        if (mounted) {
          setHealthError(err instanceof Error ? err.message : "No se pudo consultar el estado del backend");
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }

    load();

    return () => {
      mounted = false;
    };
  }, []);

  const engineReady = useMemo(() => {
    if (!snapshot) return false;
    return snapshot.checks.api_database && snapshot.checks.java_available && snapshot.checks.llm_provider_available;
  }, [snapshot]);

  const checks = useMemo(
    () => {
      const boolLevel = (ok: boolean | undefined): CheckLevel => (ok ? "validated" : "action");
      const pythonLabel = snapshot?.checks.python_version
        ? `Python ${snapshot.checks.python_version}`
        : "Python";

      return [
        {
          name: "API / Database",
          level: boolLevel(snapshot?.checks.api_database),
        },
        {
          name: pythonLabel,
          level: boolLevel(snapshot?.checks.python_ok),
        },
        {
          name: "Git (Global)",
          level: boolLevel(snapshot?.checks.git_available),
        },
        {
          name: "Java / JDK",
          level: boolLevel(snapshot?.checks.java_available),
        },
        {
          name: "Android SDK",
          level: boolLevel(snapshot?.checks.android_sdk_available),
        },
        {
          name: "LLM Provider",
          level: boolLevel(snapshot?.checks.llm_provider_available),
        },
      ];
    },
    [snapshot],
  );

  return (
    <div className="page-shell py-16">
      <section className="mb-20 grid gap-8 lg:grid-cols-12 lg:items-end">
        <div className="lg:col-span-8">
          <p className="eyebrow mb-4">System Core</p>
          <h1 className="editorial-title text-[clamp(2.8rem,7vw,5.7rem)] font-black text-[var(--ink)]">
            Environment
            <br />
            <span className="text-stone-300">Readiness</span>
          </h1>
        </div>
        <div className="lg:col-span-4">
          <p className="max-w-sm leading-relaxed text-[var(--muted)]">
            Verify runtime integrity and inspect execution parameters currently loaded by the backend.
          </p>
        </div>
      </section>

      <div className="grid gap-10 lg:grid-cols-12">
        <section className="space-y-10 lg:col-span-5">
          <div>
            <h2 className="technical-font border-b border-[var(--line-quiet)] pb-2 text-[0.58rem] text-[var(--muted)]">Health Checks</h2>
            <div className="mt-6 space-y-4">
              {checks.map((check) => {
                const tone = itemTone(check.level);
                return (
                  <div key={check.name} className="flex items-center justify-between rounded-lg p-2 hover:bg-white/80">
                    <div className="flex items-center gap-4">
                      <span className={tone.dot} />
                      <span className="font-medium text-[var(--ink)]">{check.name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`technical-font text-[0.55rem] ${tone.text}`}>{tone.label}</span>
                      <span className={`text-sm ${tone.text}`}>{tone.marker}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <article className="surface-card p-7">
            <h3 className="display-font text-2xl font-bold text-[var(--ink)]">Automated Repair Mode</h3>
            <p className="mt-3 text-sm leading-relaxed text-[var(--muted)]">
              System ready for autonomous triage. Missing critical SDK paths are surfaced explicitly so unavailable targets are not treated as failures.
            </p>
            <p className={`technical-font mt-5 text-[0.55rem] ${engineReady ? "status-success" : "status-error"}`}>
              {engineReady ? "● Core Engine Active" : "● Action Required"}
            </p>
          </article>

          {healthError ? <p className="text-sm text-[var(--bad)]">{healthError}</p> : null}
          {loading ? <p className="text-sm text-[var(--muted)]">Loading environment signals...</p> : null}
        </section>

        <section className="surface-card space-y-10 p-8 lg:col-span-7">
          <div className="space-y-6">
            <h2 className="technical-font text-[0.58rem] text-[var(--muted)]">Path Configuration</h2>

            <div className="grid gap-6 md:grid-cols-2">
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">JAVA_HOME</span>
                <input
                  value={javaHome}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">ANDROID_HOME / ANDROID_SDK_ROOT</span>
                <input
                  value={androidHome || "Not defined"}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">KMP_DATABASE_URL</span>
                <input
                  value={databaseUrl}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">GOOGLE_APPLICATION_CREDENTIALS</span>
                <input
                  value={credentialsPath}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
            </div>
          </div>

          <div className="space-y-6 border-t border-[var(--line-quiet)] pt-8">
            <h2 className="technical-font text-[0.58rem] text-[var(--muted)]">LLM Configuration</h2>
            <div className="grid gap-6 md:grid-cols-2">
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">KMP_LLM_PROVIDER</span>
                <input
                  value={llmProvider}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">KMP_LLM_MODEL</span>
                <input
                  value={llmModel}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">VERTEX PROJECT</span>
                <input
                  value={vertexProject}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
              <label className="space-y-2">
                <span className="technical-font text-[0.56rem] text-[var(--muted)]">VERTEX LOCATION</span>
                <input
                  value={vertexLocation}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
            </div>

          </div>

          <div className="space-y-6 border-t border-[var(--line-quiet)] pt-8">
            <h2 className="technical-font text-[0.58rem] text-[var(--muted)]">Execution Defaults</h2>
            <div className="grid gap-6 md:grid-cols-2">
              <label className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="technical-font text-[0.56rem] text-[var(--muted)]">run-before-after timeout</span>
                  <span className="technical-font text-[0.56rem] text-[var(--ink)]">{analysisTimeout}s</span>
                </div>
                <input
                  value={`${analysisTimeout}s`}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>

              <label className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="technical-font text-[0.56rem] text-[var(--muted)]">validate timeout</span>
                  <span className="technical-font text-[0.56rem] text-[var(--ink)]">{validateTimeout}s</span>
                </div>
                <input
                  value={`${validateTimeout}s`}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>

              <label className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="technical-font text-[0.56rem] text-[var(--muted)]">localize top-k</span>
                  <span className="technical-font text-[0.56rem] text-[var(--ink)]">{localizeTopK}</span>
                </div>
                <input
                  value={String(localizeTopK)}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>

              <label className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="technical-font text-[0.56rem] text-[var(--muted)]">repair top-k</span>
                  <span className="technical-font text-[0.56rem] text-[var(--ink)]">{repairTopK}</span>
                </div>
                <input
                  value={String(repairTopK)}
                  readOnly
                  className="w-full rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--ink)]"
                />
              </label>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
