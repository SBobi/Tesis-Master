# Guia UI y casos de uso del pipeline actual

## Objetivo
Este documento describe todo lo que un usuario puede ejecutar hoy en el pipeline `kmp-repair`, que parametros puede configurar, que prerrequisitos necesita, y como mapearlo a una UI usable.

Esta guia esta orientada a producto y UX, no solo a CLI.

---

## 1) Mapa de flujo para UI

Flujo principal recomendado en UI:

1. discover
2. ingest
3. build-case
4. run-before-after
5. analyze-case
6. localize
7. repair
8. validate
9. explain
10. metrics
11. report

Estado del caso (para una vista tipo Kanban o timeline):

- CREATED
- SHADOW_BUILT
- EXECUTED
- LOCALIZED
- PATCH_ATTEMPTED
- VALIDATED
- EXPLAINED
- EVALUATED
- NO_ERRORS_TO_FIX (atajo: no hay nada que reparar)
- FAILED

Regla UX clave:
- Si el caso entra en `NO_ERRORS_TO_FIX`, la UI debe deshabilitar `repair` y `validate`, y sugerir ir directo a `metrics`.

---

## 2) Comandos disponibles hoy (inventario completo)

## Global

### kmp-repair --help
Muestra comandos y ayuda global.

### kmp-repair --version
Muestra version del CLI.

---

## Stage 1: Ingestion y construccion del caso

### kmp-repair discover
Busca repos KMP con PRs de Dependabot.

Parametros:
- `--min-stars` (int, default: 100)
- `--min-commits` (int, default: 250)
- `--min-contributors` (int, default: 3)
- `--active-months` (int, default: 18)
- `--strict-targets/--no-strict-targets` (bool, default: strict)
- `--max-repos` (int, default: 50)
- `--max-prs` (int, default: 10)
- `--repo` (string opcional, formato owner/repo)
- `--format` (text|json, default: text)

Para UI:
- Formulario de filtros con presets Thesis Default.
- Tabla resultado con repo, stars, cantidad de PRs, links a PR.

### kmp-repair ingest <pr_url>
Ingesta una PR puntual y crea evento/caso en DB.

Parametros:
- argumento `pr_url` (requerido, URL completa de GitHub PR)
- `--artifact-dir` (string opcional)
- `--source` (string, default: dependabot)
- `--dry-run` (flag, default: false)

Para UI:
- Input de URL con validacion.
- Modo Dry Run para previsualizar sin escribir en DB.
- Mostrar `case_id`, `event_id`, `update_class`, cambios detectados.

### kmp-repair build-case <case_id>
Clona before/after y arma workspace reproducible del caso.

Parametros:
- argumento `case_id` (requerido)
- `--artifact-base` (string, default: data/artifacts)
- `--work-dir` (string opcional)
- `--overwrite` (flag, default: false)

Para UI:
- Selector de caso desde lista existente.
- Toggle overwrite para reclonar.
- Mostrar paths before/after y artifact dir.

---

## Stage 2: Ejecucion before/after

### kmp-repair run-before-after <case_id>
Ejecuta Gradle en before y after, guarda errores y evidencia.

Parametros:
- argumento `case_id` (requerido)
- `--artifact-base` (string, default: data/artifacts)
- `--target` (repeatable: shared|android|ios)
- `--timeout` (int segundos, default: 600)
- `--fresh` (flag, default: false)

Para UI:
- Multi-select de targets.
- Campo timeout por tarea.
- Boton soft reset (`--fresh`) con confirmacion.
- Mostrar targets no disponibles como `NOT_RUN_ENVIRONMENT_UNAVAILABLE`.

---

## Stage 3: Analisis estructural + localizacion

### kmp-repair analyze-case <case_id>
Construye `StructuralEvidence` (source sets, grafo, expect/actual, build files).

Parametros:
- argumento `case_id` (requerido)

Para UI:
- Vista de resumen: total kotlin files, impacted files, pares expect/actual, build files relevantes.

### kmp-repair localize <case_id>
Ejecuta localizacion hibrida (deterministica + agente opcional).

Parametros:
- argumento `case_id` (requerido)
- `--no-agent` (flag, default: false)
- `--top-k` (int, default: 10)
- `--provider` (anthropic|vertex)
- `--model` (string opcional)

Para UI:
- Toggle usar LLM o no.
- Slider/number para top-k.
- Tabla de candidatos con rank, score y source_set.

---

## Stage 4: Reparacion

### kmp-repair repair <case_id>
Sintetiza y aplica patch de reparacion.

Parametros:
- argumento `case_id` (requerido)
- `--mode` (full_thesis|raw_error|context_rich|iterative_agentic, default: full_thesis)
- `--artifact-base` (string, default: data/artifacts)
- `--top-k` (int, default: 5)
- `--provider` (anthropic|vertex)
- `--model` (string opcional)
- `--patch-strategy` (single_diff|chain_by_file, default: single_diff)
- `--force-patch-attempt/--no-force-patch-attempt` (bool, default: force)
- `--all-baselines` (flag, default: false)

Para UI:
- Selector de modo o boton "run all baselines".
- Mostrar budget por modo:
  - raw_error: 2
  - context_rich: 3
  - iterative_agentic: 4
  - full_thesis: 5
- Mostrar status por intento, touched files y diff path.

Regla UX clave:
- Si status de caso es `NO_ERRORS_TO_FIX`, ocultar CTA de repair y mostrar mensaje de atajo a metrics.

---

## Stage 5: Validacion y explicacion

### kmp-repair validate <case_id>
Valida patch por target KMP.

Parametros:
- argumento `case_id` (requerido)
- `--attempt-id` (UUID opcional, default: ultimo APPLIED)
- `--targets` (string CSV, por ejemplo: shared,android,ios)
- `--artifact-base` (string, default: data/artifacts)
- `--timeout` (int segundos, default: 600)

Para UI:
- Selector de attempt (latest o custom UUID).
- Multi-select de targets (en UI) y serializacion CSV en backend.
- Tabla por target con estado final.

### kmp-repair explain <case_id>
Genera explicacion para reviewer (JSON + Markdown).

Parametros:
- argumento `case_id` (requerido)
- `--artifact-base` (string, default: data/artifacts)
- `--provider` (anthropic|vertex)
- `--model` (string opcional)

Para UI:
- Boton "Generar explicacion".
- Preview markdown + descarga de JSON/MD.

---

## Evaluacion y reporting

### kmp-repair metrics <case_id>
Calcula metricas tesis por caso.

Parametros:
- argumento `case_id` (requerido)
- `--ground-truth` (path JSON opcional)

Para UI:
- Mostrar BSR, CTSR, FFSR, EFR, Hit@k, source_set_accuracy por modo.
- Si no hay ground truth, marcar Hit@k y source_set_accuracy como N/A.

### kmp-repair report
Exporta reportes agregados.

Parametros:
- `--output-dir` (string, default: data/reports)
- `--format` (csv|json|markdown|all, default: all)
- `--modes` (CSV opcional)
- `--cases` (CSV opcional)

Para UI:
- Filtros por modo y caso.
- Descarga de archivos generados.
- Vista de promedios por modo.

---

## Utilidades operativas

### kmp-repair doctor
Chequea entorno: Python, git, java, SDKs, DB, SDKs LLM.

Parametros:
- sin parametros

Uso UI:
- Dashboard de salud antes de ejecutar pipeline.

### kmp-repair db-status
Muestra migracion actual de Alembic.

Parametros:
- sin parametros

### kmp-repair db-upgrade
Ejecuta `alembic upgrade head`.

Parametros:
- sin parametros

### kmp-repair db-seed
Inserta data minima de ejemplo (idempotente).

Parametros:
- sin parametros

---

## Comandos tecnicos complementarios (legacy/prototipo)

Estos comandos existen hoy y se pueden ejecutar, aunque no son el flujo principal de tesis por caso:

### kmp-repair detect-changes
Compara dos libs.versions.toml.

Parametros:
- `--before` (path requerido)
- `--after` (path requerido)
- `--format` (json|text, default: json)

### kmp-repair analyze-static
Analisis estatico dirigido por dependencia y versiones.

Parametros:
- `--repo` (path requerido)
- `--dependency` (string requerido)
- `--before-version` (string requerido)
- `--after-version` (string requerido)
- `--output-dir` (default: output)

### kmp-repair build-shadow
Crea par before/after en output local.

Parametros:
- `--repo` (path requerido)
- `--dependency` (string requerido)
- `--before-version` (string requerido)
- `--after-version` (string requerido)
- `--output-dir` (default: output)
- `--init-script` (path opcional)

### kmp-repair evaluate
Evalua resultados legacy contra ground truth YAML.

Parametros:
- `--results` (path requerido)
- `--ground-truth` (path requerido)
- `--output-dir` (default: output/evaluation)

Recomendacion UI:
- Mostrar estos comandos en una seccion "Advanced / Legacy" para no mezclar con el journey principal.

---

## 3) Script operativo end-to-end

### scripts/run_e2e.sh <case_id> [--fresh] [--verbose]
Ejecuta de corrido fases 6 a 13.

Flags:
- `--fresh`: soft reset antes de correr
- `--verbose`: logs tecnicos completos
- `--human`: salida resumida (default)

Para UI:
- Boton "Run completo".
- Selector de modo de consola: human vs verbose.
- Timeline vivo por fase con links a logs.

---

## 4) Parametros de configuracion (Settings de UI)

Panel "Environment":
- `JAVA_HOME` (obligatorio, Java 21)
- `KMP_DATABASE_URL` (obligatorio)
- `ANDROID_HOME` o `ANDROID_SDK_ROOT` (opcional, requerido para Android)
- `GOOGLE_APPLICATION_CREDENTIALS` (si Vertex)

Panel "LLM":
- `KMP_LLM_PROVIDER` (vertex|anthropic)
- `KMP_LLM_MODEL` (alias/model id)
- `KMP_LLM_FAKE` (1 para tests sin proveedor real)
- `KMP_VERTEX_PROJECT` o `GCP_PROJECT_ID` o `GOOGLE_CLOUD_PROJECT`
- `KMP_VERTEX_LOCATION`
- `ANTHROPIC_API_KEY` (si anthropic)

Panel "Execution Defaults":
- timeout default run-before-after
- timeout default validate
- artifact base path
- top-k default localize
- top-k default repair
- repair mode default
- patch strategy default

---

## 5) Casos de uso UI recomendados

## Caso A: "Quiero detectar y crear casos nuevos"
Secuencia:
1. discover
2. ingest
3. build-case

Datos minimos:
- repo owner/name o filtros de discovery
- URL PR

Entregables UI:
- listado de casos creados con case_id y status CREATED/SHADOW_BUILT.

## Caso B: "Quiero ejecutar un caso completo"
Secuencia:
1. run-before-after
2. analyze-case
3. localize
4. repair
5. validate
6. explain
7. metrics

Datos minimos:
- case_id

Entregables UI:
- evidencia before/after
- candidatos localizados
- patch attempts
- validacion por target
- explicacion markdown
- metricas por modo

## Caso C: "Quiero comparar baselines"
Secuencia:
1. repair --all-baselines
2. metrics
3. report --modes ... --cases ...

Entregables UI:
- tabla comparativa por modo
- graficos BSR/CTSR/FFSR/EFR

## Caso D: "Solo quiero validar un patch puntual"
Secuencia:
1. validate --attempt-id
2. explain

Entregables UI:
- estado por target
- resumen de cobertura y riesgos

## Caso E: "No tengo entorno listo"
Secuencia:
1. doctor
2. db-upgrade
3. reintentar fase

Entregables UI:
- checklist de prerequisitos faltantes con acciones.

---

## 6) Validaciones de formulario que la UI debe imponer

- `case_id` debe existir en DB.
- `pr_url` debe tener formato `https://github.com/<owner>/<repo>/pull/<n>`.
- `discover --repo` debe ser `<owner>/<repo>`.
- `run-before-after --target` solo acepta `shared`, `android`, `ios`.
- `localize --provider` y `repair/explain --provider` solo `anthropic` o `vertex`.
- `repair --mode` solo valores permitidos.
- `repair --patch-strategy` solo `single_diff` o `chain_by_file`.
- `validate --targets` debe parsear CSV limpio sin vacios.
- timeouts deben ser enteros positivos.

---

## 7) Sugerencia de arquitectura de UI

Vistas principales:
- Cases List: filtro por status, repo, fecha.
- Case Detail: timeline de fases + acciones habilitadas por estado.
- Repair Attempts: historial por modo, status, archivos tocados, diff.
- Validation: matriz target x estado.
- Explanation: markdown render + JSON raw.
- Reports: filtros y descarga.
- Settings: entorno, LLM, defaults.

Principio UX central:
- UI guiada por estado del caso, no por comandos sueltos.

---

## 8) Defaults recomendados para una UI inicial

- discover: usar defaults de tesis.
- run-before-after timeout: 600s.
- localize top-k: 10.
- repair mode por defecto: full_thesis.
- repair top-k: 5.
- repair patch strategy: single_diff.
- validate timeout: 600s.
- report format: all.

---

## 9) Comandos de referencia rapida

```bash
# flujo principal
kmp-repair discover --repo owner/repo
kmp-repair ingest https://github.com/owner/repo/pull/42
kmp-repair build-case <case_id>
kmp-repair run-before-after <case_id>
kmp-repair analyze-case <case_id>
kmp-repair localize <case_id>
kmp-repair repair <case_id> --all-baselines
kmp-repair metrics <case_id>
kmp-repair report --format all

# utilidades
kmp-repair doctor
kmp-repair db-upgrade

# run integrado
./scripts/run_e2e.sh <case_id> --fresh
```

---

## 10) Nota de implementacion para backend de UI

Este pipeline ya expone toda la semantica necesaria desde CLI + DB + artifacts.
Para una UI web/desktop, el backend puede modelar cada accion como:
- comando + payload validado
- ejecucion sincrona o en job
- parseo de stdout estructurado
- lectura de DB para estado final y evidencia

Con esto se evita acoplar la UI a parsing fragil de logs.
