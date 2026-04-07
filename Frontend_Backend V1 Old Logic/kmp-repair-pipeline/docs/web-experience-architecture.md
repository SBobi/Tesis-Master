# Experiencia Web para kmp-repair-pipeline

## 1. Resumen ejecutivo

Se implemento una capa web completa para operar el pipeline desde navegador, manteniendo el nucleo de dominio existente en Python y evitando duplicar la logica de etapas. La solucion combina:

- Backend FastAPI en Python 3.11+.
- Orquestacion asyncrona con RQ + Redis.
- Persistencia con PostgreSQL + SQLAlchemy + Alembic.
- Streaming en vivo de estado y logs por SSE.
- Frontend Next.js (App Router) + TypeScript + Tailwind con direccion visual editorial.

La web permite:

- Crear caso desde URL de PR.
- Ejecutar etapa individual.
- Ejecutar pipeline completo desde cualquier etapa.
- Cancelar job activo.
- Revisar evidencia trazable (logs, stdout/stderr, diff, validacion, explicacion, metricas).

## 2. Arquitectura tecnica y diagrama textual

```text
[Next.js Web (TS)]
    |
    | HTTPS + SSE
    v
[FastAPI Web API]
    | \
    |  \ enqueue/dequeue
    |   v
    | [RQ Queue] <-> [Redis]
    |
    v
[RQ Worker Python]
    |
    | reusa funciones de etapas existentes
    v
[Pipeline Core Modules]
    - ingest.event_builder
    - case_builder.case_factory
    - runners.execution_runner
    - static_analysis.structural_builder
    - localization.localizer
    - repair.repairer
    - validation.validator
    - explanation.explainer
    - evaluation.evaluator
    - reporting.reporter
    |
    v
[PostgreSQL + Artifacts filesystem]
```

Principios de implementacion:

- No se reescribe dominio ni CLI.
- Parametros de etapa con allowlist estricta.
- Cada etapa persiste transiciones de estado y metadatos.
- SSE con polling de estado + tail de logs de job para observabilidad operativa.

## 3. Modelo de datos y contratos API

### 3.1 Nuevas tablas

1. pipeline_jobs
- Guarda tipo de job (RUN_STAGE/RUN_PIPELINE), estado, etapa actual, parametros efectivos, comando equivalente, error, log_path y timestamps.

2. case_status_transitions
- Historial inmutable de transiciones por etapa/job:
  - from_status
  - to_status
  - transition_type
  - stage
  - metadata_json

### 3.2 Estados usados

Estado de caso:
- CREATED
- SHADOW_BUILT
- EXECUTED
- LOCALIZED
- PATCH_ATTEMPTED
- VALIDATED
- EXPLAINED
- EVALUATED
- FAILED

Estado de validacion:
- SUCCESS_REPOSITORY_LEVEL
- PARTIAL_SUCCESS
- FAILED_BUILD
- FAILED_TESTS
- NOT_RUN_ENVIRONMENT_UNAVAILABLE
- INCONCLUSIVE
- NOT_RUN_YET

### 3.3 Endpoints principales

1. Gestion de casos
- POST /api/cases
- GET /api/cases
- GET /api/cases/{case_id}
- GET /api/cases/{case_id}/history

2. Orquestacion
- POST /api/cases/{case_id}/jobs/stage
- POST /api/cases/{case_id}/jobs/pipeline
- POST /api/jobs/{job_id}/cancel
- GET /api/jobs/{job_id}

3. Observabilidad
- GET /api/jobs/{job_id}/logs
- GET /api/jobs/{job_id}/stream (SSE)
- GET /api/stream/active (SSE)
- GET /api/cases/{case_id}/artifact-content

4. Reportes
- GET /api/reports/compare

## 4. Implementacion por fases

### Fase A - Persistencia y auditoria

- Extension ORM con PipelineJob y CaseStatusTransition.
- Nuevos repositorios:
  - PipelineJobRepo
  - CaseStatusTransitionRepo
- Migracion Alembic:
  - d9f4e6a7b8c9_add_pipeline_jobs_and_status_transitions.py

### Fase B - Capa web y orquestacion

- Modulo `src/kmp_repair_pipeline/webapi/`:
  - app.py
  - job_runner.py
  - orchestrator.py
  - stages.py
  - queries.py
  - settings.py
  - queue.py
  - worker.py

Capacidades:
- Validacion strict de params por etapa.
- Generacion de comando equivalente para transparencia.
- Ejecucion secuencial por etapas con logs y transiciones.
- Cancelacion de job encolado/activo (best effort con RQ).

### Fase C - Frontend editorial

- App en `web/` con App Router:
  - Home narrativa + estado global en vivo.
  - Casos: feed editorial + vista tabla.
  - Caso: timeline vertical, evidencia, consola viva, run composer sticky.
  - Reportes: comparacion por modo con tarjetas y tabla.

### Fase D - Testing y hardening

- Backend:
  - tests/unit/test_webapi_phase14.py
  - ajustes en tests de estado ejecutado (EXECUTED)
  - cobertura integration para repos de jobs/transitions
- Frontend:
  - Vitest unit: tests/unit/format.test.ts
  - Playwright smoke: tests/e2e/home.spec.ts

## 5. Pruebas ejecutadas

Backend:
- pytest tests/unit/test_webapi_phase14.py tests/unit/test_structural_builder_phase7.py tests/unit/test_localization_phase8.py -q
- pytest tests/integration/test_db_schema.py -q

Frontend:
- npm run build
- npm test
- npm run test:e2e

## 6. Ejecucion local

1) Infra:
- docker compose up -d postgres redis

2) Backend:
- pip install -e ".[dev]"
- alembic upgrade head
- kmp-repair-api
- kmp-repair-worker

3) Frontend:
- cd web
- npm install
- npm run dev

Variables recomendadas:
- KMP_DATABASE_URL
- KMP_REDIS_URL
- NEXT_PUBLIC_API_BASE_URL

## 7. Riesgos y siguientes pasos

Riesgos actuales:
- Cancelacion de etapas largas depende de puntos de chequeo entre etapas (cancelacion best effort intra-stage).
- SSE implementado con polling del estado de DB + lectura de log file; suficiente para MVP, no para muy alta concurrencia.

Siguientes pasos sugeridos:
1. Pasar eventos a pub/sub Redis para reducir polling.
2. Agregar autenticacion y RBAC para multiusuario.
3. Incorporar snapshots visuales reales de pantalla en docs (capturas automatizadas Playwright).
4. Añadir tests e2e de flujo completo caso->run->retry->report.
