# MacroPipeline — Plan de proyecto (v2)

> Documento maestro de referencia. Compila las decisiones técnicas y estratégicas tomadas tras la revisión crítica del plan original.

---

## 1. Resumen ejecutivo

**Qué es:** Pipeline automatizado que ingesta datos macroeconómicos y financieros de fuentes públicas, los procesa de forma determinista, los renderiza visualmente y los publica multi-canal con aprobación humana asistida.

**Por qué existe:** Proyecto de portfolio para demostrar habilidades de ingeniería de datos, arquitectura de sistemas, MLOps ligero y prácticas production-grade. No es un producto comercial ni un servicio financiero.

**Cómo se diferencia:** La arquitectura, no el contenido. El pipeline está diseñado para ser auditable, observable, testeable y resiliente — exactamente lo que un sistema de datos financieros real necesita.

---

## 2. Cambios respecto al plan original

| Decisión original | Decisión actual | Motivo |
|---|---|---|
| Claude como ETL del path numérico | LLM fuera del path numérico, solo titulares y validator | Determinismo y credibilidad en datos financieros |
| Instagram primero | X + LinkedIn primero, IG en mes 2-3 | Encaje natural con audiencia profesional |
| Audiencia objetivo financiera | Portfolio para recruiters | El proyecto está sin tracción; pivote honesto |
| Side project mínimo + datos críticos | HITL primeros 30 días + auto después | Resuelve contradicción "mínimo mantenimiento" vs datos sensibles |
| Sin observabilidad explícita | OpenTelemetry + Grafana Cloud | Diferenciador de portfolio |
| Sin validación explícita | Pydantic + YAML rules + validator agent LLM | Defensa en profundidad |
| APIs gratuitas sin plan B | APIs gratuitas + contract tests nightly + retry/fallback | Resiliencia ante cambios externos |
| Una plantilla "para todo" | Plantillas por canal con data layer compartido | Respeta convenciones de cada plataforma |
| Publicación auto desde día 1 | HITL vía Telegram bot, migración a Slack mes 2-3 | Showcase de iteración técnica documentada |

---

## 3. Filosofía del proyecto

1. **El LLM no toca números.** Los datos numéricos pasan por código Python determinista. El LLM solo redacta titulares de texto libre y actúa como validator de segunda opinión.
2. **Mejor nada que algo incompleto.** Si una API falla y no hay fallback, no se publica. Política estricta en horarios críticos.
3. **HITL hasta probar estabilidad.** Los primeros 30 días, cada draft pasa por aprobación humana. Después, automatizado con kill switch siempre disponible.
4. **Observabilidad como ciudadano de primera clase.** Logs estructurados, métricas y traces desde el día 1, no como añadido posterior.
5. **El repo es el producto.** El código público en GitHub es el showcase principal; las cuentas sociales son prueba de funcionamiento.

---

## 4. Arquitectura

```
                    ┌──────────────────┐
                    │  Claude Routine  │  (schedule trigger)
                    │   (orquestador)  │
                    └────────┬─────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │           ETL determinista              │
        │   Python + Pandas + Pydantic + YAML     │
        └────┬──────────┬───────────┬─────────────┘
             │          │           │
             ▼          ▼           ▼
        ┌────────┐ ┌────────┐ ┌──────────┐
        │  FRED  │ │  FMP   │ │  Alpha   │
        │  (free)│ │ (free) │ │  Vantage │
        └────────┘ └────────┘ └──────────┘
                             │
                             ▼
              ┌──────────────────────────┐
              │   Sanity checks + Pydantic│
              └──────────────┬─────────────┘
                             │
                             ▼
              ┌──────────────────────────┐
              │  Claude API (Sonnet 4.5)  │
              │  · Headline generation    │
              │  · Validator agent        │
              │    (tool-use, JSON forced)│
              └──────────────┬─────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
     ┌────────────────┐            ┌────────────────┐
     │  X template    │            │  LinkedIn tpl  │
     └───────┬────────┘            └───────┬────────┘
             │                             │
             ▼                             ▼
     ┌────────────────────────────────────────────┐
     │      Renderizado híbrido                    │
     │  · Pillow (posts repetitivos, fijos)       │
     │  · Playwright/HTML (posts complejos)        │
     └─────────────────────┬──────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Telegram bot (HITL)  │
              │   ✅ Aprobar / Editar / Rechazar │
              └────────┬───────────────┘
                       │  (approve)
                       ▼
              ┌────────────────────────┐
              │   Publishers           │
              │  · X API v2            │
              │  · LinkedIn API (Page) │
              └────────────────────────┘

  Telemetría transversal:
  · OpenTelemetry → Grafana Cloud (logs, métricas, traces)
  · SQLite local (queue) + Cloudflare R2 (snapshots inmutables)
```

---

## 5. Stack tecnológico

### Datos
- **FRED** (free, ilimitado) — macro USA
- **FMP** free tier — fundamentales, earnings calendar, precios
- **Alpha Vantage** free tier — backup y precios complementarios
- **Cobertura geográfica**: USA principalmente

### Procesamiento
- **Python 3.12** + **Pandas** / **Polars** para ETL determinista
- **Pydantic** para validación de schemas
- **Reglas declarativas YAML** para sanity checks configurables
- **Anthropic SDK** para llamadas a Claude API (solo titulares + validator)

### Orquestación
- **Claude Routines** (scheduled triggers) — orquestador principal
- Plan B documentado: GitHub Actions scheduled workflow si Routines cambia en research preview

### Renderizado
- **Claude Design** — exploración inicial de plantillas (one-shot)
- **Pillow** — runtime de posts repetitivos
- **Playwright + HTML/CSS** — runtime de posts complejos
- Plantillas distintas por canal (X / LinkedIn / IG) sobre data layer común

### Publicación
- **X API v2** (tier Free, 1.500 tweets/mes)
- **LinkedIn API** (publicación en Company Page)
- **Instagram Graph API** (mes 2-3, requiere Facebook Page)

### Aprobación humana
- **Telegram bot** con inline buttons (long polling, sin endpoint público)
- Migración planificada a **Slack** en mes 2-3 (con endpoint propio para approvals)

### Observabilidad
- **structlog** para logs JSON
- **OpenTelemetry** SDK para métricas y traces
- **Grafana Cloud** free tier (14 días logs + 50GB traces)

### Storage
- **SQLite** local durante la run (queue de drafts)
- **Cloudflare R2** free tier (snapshots JSON inmutables con timestamp)

### Secrets
- **GitHub Secrets** para CI
- Variables de entorno en Routines

### CI/CD
- **GitHub Actions** workflows:
  - `ci.yml` — lint (ruff) + types (mypy) + tests (pytest con fixtures)
  - `contract-tests.yml` — nightly contra APIs reales
  - `coverage.yml` — reporte Codecov
- **Pre-commit hooks**: ruff + mypy en módulos críticos

---

## 6. Decisiones documentadas (ADRs resumidos)

### ADR-001 — LLM fuera del path numérico
**Decisión:** El procesamiento de números (BPA, precios, %) usa Python determinista. El LLM solo redacta titulares y actúa como validator.
**Motivo:** Determinismo no negociable en datos financieros. Un error numérico destruye credibilidad construida en meses.
**Tradeoff:** Menos "magia" del LLM, más código manual. Aceptable.

### ADR-002 — Claude Routines como orquestador
**Decisión:** Routines (scheduled) clona repo y ejecuta scripts Python. Claude API se llama desde Python.
**Motivo:** Cero infra propia, infraestructura gestionada por Anthropic, encaje natural con suscripción existente.
**Tradeoff:** Routines está en research preview. Plan B: GitHub Actions scheduled workflow.

### ADR-003 — Plantillas por canal con data layer compartido
**Decisión:** Mismo data layer alimenta N renderers especializados por plataforma.
**Motivo:** Cada canal tiene "real estate" radicalmente distinto. Adaptador genérico produce contenido mediocre. Determinismo total.
**Tradeoff:** Más plantillas que mantener; cero drift entre canales.

### ADR-004 — HITL primeros 30 días, auto después
**Decisión:** Drafts pasan por aprobación Telegram durante mes 1. Auto-publicación tras estabilidad probada.
**Motivo:** Margen de seguridad real. Feature técnica adicional (approval queue). Kill switch natural.
**Tradeoff:** Atención humana diaria durante mes 1.

### ADR-005 — APIs nativas en lugar de Buffer/Make
**Decisión:** Integración directa con X API v2 y LinkedIn API.
**Motivo:** Máximo control, mejor showcase técnico, sin dependencia/costo externo.
**Tradeoff:** Setup más complejo (aprobación LinkedIn requiere Company Page).

### ADR-006 — Telegram bot primero, Slack después
**Decisión:** Empezar con Telegram (cero infra, long polling). Migrar a Slack en mes 2-3.
**Motivo:** Time-to-running mínimo. Migración documentada es por sí misma un punto de portfolio.
**Tradeoff:** Estética Slack es superior; se acepta el coste de migrar.

### ADR-007 — Storage híbrido SQLite + R2
**Decisión:** SQLite efímero durante la run, JSON snapshots inmutables a Cloudflare R2.
**Motivo:** Event sourcing ligero sin complejidad de event store real. Reproducibilidad histórica.
**Tradeoff:** Dos sistemas en lugar de uno. Aceptable por separación de concerns.

### ADR-008 — Contract tests nightly separados del PR pipeline
**Decisión:** Tests de PR usan fixtures grabadas. Contract tests corren nightly contra APIs reales.
**Motivo:** APIs externas cambian; sin contract tests, los cambios se descubren en producción. Tests de PR rápidos y deterministas.
**Tradeoff:** Más workflows que mantener; el valor compensa.

---

## 7. Roadmap revisado

### Semana 0 — Setup administrativo (3h aprox)
- [x] Verificar disponibilidad de `MacroPipeline` en X, LinkedIn, GitHub
- [x] Crear cuenta X (@MacroPipeline) + Developer Account
- [x] Crear cuenta personal LinkedIn (si no existe) + Company Page MacroPipeline
- [x] Crear repo público `github.com/SimonChiabo/MacroPipeline`
- [x] Crear bot de Telegram (`@BotFather`)
- [ ] Provisionar cuenta Cloudflare R2 free tier
- [ ] Crear cuenta Grafana Cloud free tier

### Semana 1 — ETL determinista + validación
- [x] Esqueleto del repo con estructura recomendada
- [x] Cliente FRED + FMP + Alpha Vantage con retry/fallback
- [x] Pydantic schemas por tipo de dato (cierre semanal, macro release, earnings)
- [x] Sanity checks declarativos en YAML
- [x] Tests unitarios con fixtures grabadas
- [x] Pre-commit hooks (ruff, mypy)
- [x] **Sin LLM, sin renderizado, sin publicación todavía**

### Semana 2 — Renderizado + LLM auxiliar
- [x] Plantillas iniciales en Claude Design → exportar HTML/PPTX
- [x] Runtime Pillow para plantillas fijas (calendarios, esperado vs real)
- [x] Runtime Playwright para plantillas complejas (cierre semanal)
- [x] Cliente Claude API con prompts versionados
- [x] Validator agent con tool-use forzado (schema JSON estructurado)
- [x] Tests de snapshot sobre outputs renderizados

### Semana 3 — Orquestación + HITL
- [x] Configurar Claude Routine para cierre semanal del viernes
- [x] Bot Telegram: envío de drafts con preview + botones
- [x] Long polling desde Python
- [x] Estado en SQLite + snapshots a R2
- [x] structlog + OpenTelemetry + dashboard básico en Grafana Cloud

### Semana 4 — Publicación + observabilidad completa
- [x] X API v2: publicación de hilos
- [x] LinkedIn API: publicación en Company Page (texto + PDF carrusel)
- [ ] Workflow `contract-tests.yml` nightly con alertas Telegram
- [ ] Dashboard Grafana completo (runs OK/KO, latencia, errores)

### Semanas 5-8 — Operación HITL
- [ ] 2-3 posts/semana con aprobación manual
- [ ] Iteración de plantillas, prompts y validators
- [ ] Documentación de bugs y soluciones en ADRs
- [ ] Decisión de switch auto-publicación

### Mes 3 — Migración + escala
- [ ] Migrar Telegram → Slack (documentar como ADR-009)
- [ ] Setup Facebook Page + Instagram Business
- [ ] Evaluación de escalar a 2 posts/día L-V

---

## 8. Métricas de éxito

Como proyecto de portfolio, las métricas no son engagement social, son **demostración de competencias técnicas**:

- **Estabilidad operativa:** uptime del pipeline > 95% durante 60 días
- **Calidad del código:** cobertura > 60% en módulos críticos (ETL, validators, renderers)
- **Contract tests:** detección automática de al menos un breaking change real de APIs en 90 días
- **Observabilidad funcional:** dashboard Grafana con métricas reales (latencia APIs, error rate, drafts aprobados/rechazados)
- **Visibilidad:** repo con > 50 stars, o al menos 1 conversación de reclutamiento atribuible al proyecto
- **Documentación:** todos los ADRs escritos, README excelente, diagrama de arquitectura actualizado

---

## 9. Riesgos residuales y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| FMP/AV cambian schema sin aviso | Alta (anual) | Medio | Contract tests nightly + alertas Telegram |
| Claude Routines cambia en research preview | Media | Medio | Plan B documentado: GitHub Actions scheduled |
| Aprobación LinkedIn Marketing API se complica | Media | Alto | Pivote a Company Page (no requiere aprobación) |
| Datos publicados con error pese a HITL | Baja | Alto | Validator agent + double-check humano |
| Free tier APIs deja de ser suficiente | Media | Bajo | Presupuesto previsto $30-80/mes en caso de upgrade |
| Side project requiere demasiada atención | Media | Medio | HITL solo mes 1; después automático con alertas |
| Routines daily cap (15) insuficiente | Baja | Bajo | Volumen actual << cap; revisable si escalamos |

---

## 10. Apéndice — Estructura del repo

```
macro-pipeline/
├── README.md
├── PLAN.md                          # Este documento
├── pyproject.toml
├── .env.example
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/
│       ├── ci.yml                   # PR: lint + types + tests
│       ├── contract-tests.yml       # Nightly contra APIs reales
│       └── coverage.yml             # Reporte Codecov
├── src/
│   └── macro_pipeline/
│       ├── data/                    # Clientes API + ETL
│       ├── validators/              # Pydantic + reglas YAML
│       ├── llm/                     # Claude API (headlines + validator)
│       ├── templates/               # Plantillas por canal
│       ├── render/                  # Pillow + Playwright
│       ├── publishers/              # X + LinkedIn
│       ├── telegram/                # HITL bot
│       ├── observability/           # OTel setup
│       └── orchestration/           # Entry points para Routines
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/                    # Tests contra APIs reales
│   └── fixtures/                    # Responses grabadas
└── docs/
    └── adr/                         # Architecture Decision Records
        ├── 001-llm-out-of-numbers.md
        ├── 002-claude-routines.md
        ├── 003-templates-per-channel.md
        ├── 004-hitl-first-month.md
        ├── 005-native-apis.md
        ├── 006-telegram-first.md
        ├── 007-storage-hybrid.md
        └── 008-contract-tests.md
```
