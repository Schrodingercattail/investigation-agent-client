# Docker Deployment & Operations Guide

Maintainer-facing operations reference for the Docker Compose deployment of
the Investigation Agent Client. For a quick start see the
[Docker section of the README](../../README.md#docker).

**Verification status legend used throughout this document:**

- **VERIFIED** — exercised in the Phase 3A/3B Docker acceptance rounds
  (2026-11-06) on this repository.
- **IMPLEMENTED, NOT SPECIFICALLY VERIFIED** — the code path exists and is
  load-bearing, but no dedicated acceptance run targeted it.
- **NOT TESTED** — not exercised; requires a dedicated verification round.
- **NOT SUPPORTED** — intentionally out of scope for this deployment model.

## 1. Compose Architecture

`docker-compose.yml` defines exactly two services. The **Risk Platform is
an external dependency in a separate repository and is NOT part of this
Compose stack** — it must already be running and reachable before
investigation requests are submitted.

```text
browser ──:3001──▶ agent-frontend (nginx:1.27-alpine)
                      │  static SPA + reverse proxy
                      ├─ /api/  ──▶ http://backend:8001   (compose DNS)
                      └─ /health ──▶ http://backend:8001/health
                    agent-backend (python:3.12-slim, uvicorn :8001)
                      │  SQLite: /app/data/investigation_agent_v2.db
                      │            (bind mount → host ./data/)
                      └──HTTP──▶ Risk Platform (external, host :8000)
                                 RISK_PLATFORM_BASE_URL, default
                                 http://host.docker.internal:8000
```

| Service | Container name | Build context | Host port | Container port |
|---|---|---|---|---|
| `backend` | `agent-backend` | `./backend` | 8001 | 8001 |
| `frontend` | `agent-frontend` | `./frontend` | 3001 | 80 (nginx) |

- **depends_on**: `frontend` waits for `backend` to reach
  `service_healthy`.
- **backend healthcheck**: container-internal
  `python -c "urllib.request.urlopen('http://localhost:8001/health')"`
  (interval 10s, timeout 5s, retries 5, start_period 10s). The `/health`
  route is a static liveness response — it does **not** touch the LLM,
  the Risk Platform, or the database.
- **restart policy**: none configured (manual restart /
  `docker compose restart` after host reboots). *IMPLEMENTED, NOT
  SPECIFICALLY VERIFIED as a restart workflow.*
- **networks**: default compose network (created as
  `<project>_default`). Service-name DNS (`backend`) resolves inside it.

## 2. Environment Variables

Configured in the repository-root `.env` (compose reads it automatically
for interpolation). Template: `.env.example`. The backend exposes four
runtime variables plus the **Demo mode switch**.

| Variable | Purpose | Default (compose) | Required |
|---|---|---|---|
| `AGENT_SYNTHETIC_RP_FIXTURE` | **Demo mode switch** | `/app/app/demo_fixtures/demo_case.json` (bundled synthetic case U99001) | No — unset = Demo ON |
| `RISK_PLATFORM_BASE_URL` | Risk Platform base URL (External RP mode) | `http://host.docker.internal:8000` | External RP mode |
| `ANTHROPIC_API_KEY` | LLM API key | *(empty)* | Yes for investigation turns. The stack **starts and reports healthy without it** (deterministic fallbacks stay active; LLM-dependent requests fail with a bounded configuration error) |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible LLM endpoint | `https://open.bigmodel.cn/api/anthropic` (Zhipu BigModel — matches the default model) | Yes for investigation turns |
| `ANTHROPIC_MODEL` | Model name | `glm-5.3` | Yes for investigation turns |

### Demo mode vs External RP mode

- **Demo mode (default).** The backend serves a bundled **fully
  synthetic** case `U99001` — you can create an investigation for
  `U99001` (submit `Investigate U99001`) and exercise the full Agent
  Loop, evidence retrieval, and Artifact generation **without a Risk
  Platform**. Demo data is fabricated and every response from the Demo
  data source carries `"synthetic": true`. Demo mode is a convenience
  for experiencing the Agent — it is **not** real Risk Platform
  evidence and **not** a production architecture.
- **External RP mode.** Set in your `.env`:
  `AGENT_SYNTHETIC_RP_FIXTURE=` (**explicitly empty**) plus
  `RISK_PLATFORM_BASE_URL=<your RP URL>`. The real RiskPlatformAdapter
  path is unchanged — Demo is a data-source switch at the adapter
  boundary, never a bypass of the Agent Runtime or tools.

**Precedence: shell environment > root `.env` > compose default.** If
your shell exports `ANTHROPIC_BASE_URL` or
`AGENT_SYNTHETIC_RP_FIXTURE`, those values override `.env` and the
compose defaults. Use `env -u <VAR> docker compose ...` to ignore a
shell value for one invocation.

### Historical-investigation isolation (data_source)

Every investigation records the data source it was created with
(`data_source: "demo" | "external"`, persisted in the investigation
document). Cross-mode protection:

- Continuing an investigation (submit / turn / goal / resume) under a
  **different** data source returns **HTTP 409** with a clear message —
  before any RP read, tool call, or evidence write. Switch back to the
  creating mode to continue.
- Investigations created **before** the `data_source` field existed
  carry `data_source: null` — continuing them is **blocked with 409**
  in both modes (source cannot be proven). Viewing them remains
  allowed.
- Switching modes never migrates or re-interprets historical data;
  to continue an old investigation, switch back to the mode that
  created it.

Notes:

- `.env` is git-ignored; never commit it. Provide your own API key — no
  key ships with the repository.
- The key must match the endpoint: a BigModel key with the BigModel
  endpoint, a z.ai key with `https://api.z.ai/api/anthropic`, etc.
- The **frontend container receives none of these variables** — LLM
  credentials never reach the browser.
- Without an API key the stack runs in deterministic fallback mode —
  bounded, honest outputs, **not real LLM reasoning**. This is visible
  in backend logs (`LLM not configured; deterministic fallbacks
  active`).

## 3. Data Persistence (SQLite)

- The backend store is **SQLite via `TaskStoreV2`** — a single database
  file. There is no second database and no migration tooling; the schema
  is created automatically on first startup (`CREATE TABLE IF NOT EXISTS`).
- **Host location:** `./data/investigation_agent_v2.db` (bind mount
  `./data:/app/data`; the directory is created on first start and is
  git-ignored).
- **Retention behavior (VERIFIED bind mount; see status notes):**
  - `docker compose restart` / `stop` + `start` — data retained.
  - Container recreation (`up` after image/config change, `down` + `up`)
    — **data retained** because it lives in the host `./data/` bind
    mount, not in the container filesystem.
  - Deleting the host `./data/` directory — data lost. Do not delete it
    unless you intend to reset all investigation records.
- **Backup:** stop the backend (or ensure no write is in progress), then
  copy `./data/investigation_agent_v2.db`. For a consistent copy of a
  live database prefer `sqlite3 <db> ".backup '<target>'"` over a plain
  file copy. Restore = place the file back before starting the stack.
- **Single-writer assumption:** run exactly one backend against a given
  database file. Do not point a second stack (or the local dev server) at
  the same file while another process writes it.

## 4. Common Commands

All commands run from the repository root.

```bash
# Build (first start, or after code changes)
docker compose build

# Start (add --build to rebuild in the same step; backend becomes
# healthy within ~30s, frontend starts after that)
docker compose up -d            # or: docker compose up --build -d

# Status
docker compose ps
docker images | grep investigation-agent-client

# Logs (follow)
docker compose logs -f backend
docker compose logs -f frontend

# Stop (containers are removed on `down`; data persists in ./data/)
docker compose down             # add --volumes only if you also want to
                                # remove named volumes (this stack uses a
                                # bind mount, so ./data is NOT affected)
```

## 5. Troubleshooting

| Symptom | Likely cause | Check / remedy |
|---|---|---|
| `up` fails with *port already allocated* on 8001/3001 | The local dev stack (uvicorn :8001 / vite :3001) or another process holds the port | `lsof -nP -iTCP:8001 -sTCP:LISTEN` (and 3001). Stop the conflicting process or change the published ports in `docker-compose.yml` |
| `up` fails with *name already in use* for `agent-backend`/`agent-frontend` | A stale container from a previous run | `docker ps -a`, remove the stale container (or `docker compose down` first) |
| Backend unhealthy / restarts | Database directory not writable, or app crash at startup | `docker compose logs backend` — startup errors (import, seeding, DB path) are logged and the seeding failure is non-fatal by design |
| Frontend loads but every API call fails | Backend down, or nginx → backend DNS issue | `curl http://localhost:8001/health` directly; `docker compose logs frontend` |
| Investigations fail with an LLM configuration error | `ANTHROPIC_API_KEY` missing/empty, endpoint/model mismatch, or a shell-exported `ANTHROPIC_BASE_URL` overriding `.env` (see precedence above) | `docker compose config` (values are not secret except the key — do not paste the key); confirm the key matches the configured endpoint |
| Investigations return `DATA_SOURCE_MISMATCH` (HTTP 409) | The investigation was created under the **other** data source (Demo vs External RP) and is being continued under the wrong one | Switch the data source back to the creating mode (see §2 Demo vs External), then retry. Viewing history is always allowed |
| Investigations return `UNKNOWN_DATA_SOURCE` (HTTP 409) | The investigation was created before the `data_source` field existed; its origin cannot be proven | Viewing remains allowed; continuing is intentionally blocked (fail-closed). No workaround is provided by design |
| Investigations fail with Risk Platform errors (Demo mode) | You are in **Demo mode** — the Risk Platform is intentionally not contacted; data comes from the bundled synthetic fixture | Switch to External RP mode (clear `AGENT_SYNTHETIC_RP_FIXTURE`) if you need real evidence |
| Investigations fail with Risk Platform errors (External mode) | RP not running, or RP bound only to `127.0.0.1` on a Linux host (the container reaches the host via the gateway IP, not loopback) | Verify RP is reachable and, on plain Linux Docker, that it listens on an interface reachable from `host.docker.internal` |
| Frontend build succeeds but TypeScript errors exist in the IDE | The frontend image build runs `npx vite build` and **skips `tsc`** | Run `npm run build` locally for a full typecheck; a Dockerfile change to `npm run build` is the structural fix |

## 6. Verification Status (as of 2026-11-07)

**VERIFIED** (Phase 3A infrastructure acceptance + Phase 3B real-browser
journey + Phase 3B-2.2 real Agent Loop API E2E + Phase 3B-3 Demo-mode
browser journey, Docker Desktop 4.84 / Engine 29.6.2, linux/arm64):

- Image builds for both services; backend + frontend containers start;
  backend reaches `healthy` via the configured healthcheck.
- Frontend page served on :3001; nginx `/api` reverse proxy and `/health`
  passthrough return backend responses.
- Backend → Risk Platform connectivity from inside the container via
  `host.docker.internal:8000` (RP health 200).
- SQLite bind mount: host `./data/investigation_agent_v2.db` is created
  and used by the container; investigation/task/session/artifact records
  written by a real Agent Loop E2E are persisted in it.
- Real-browser user journey over a persisted investigation: history
  list → open → conversation, evidence, artifact sidebar, refresh
  persistence — all verified against the records above.
- BigModel (`open.bigmodel.cn`, `glm-5.3`) real LLM calls through this
  stack (Goal Understanding + bounded decision proposals).
- **Demo mode (synthetic fixture, real browser + API):** create
  investigation for the bundled synthetic case `U99001`, full Agent Loop
  over fixture evidence (8 synthetic withdrawal records with
  addresses), Artifact generation, data_source isolation (cross-mode
  continuation blocked with 409 before any RP read; unlabeled legacy
  records blocked), refresh persistence — verified at code level and in
  the running stack.

**IMPLEMENTED, NOT SPECIFICALLY VERIFIED:**

- `docker compose restart` / host-reboot recovery as a workflow.
- `GET /investigations/{id}/followups` and follow-up chip flows in the
  Docker deployment.
- MCP access paths (`mcp_server/`, `.mcp.json`) — development-host
  tooling; not part of, and not exercised by, this Compose stack.

**NOT TESTED:**

- Container-recreate persistence drill (write → recreate container →
  assert data retained). The bind mount makes this the expected
  behavior, but no dedicated before/after acceptance run has been
  executed.
- Frontend TypeScript check inside the image build: the frontend
  Dockerfile intentionally runs `npx vite build` **without** `tsc`
  (known gap — run `npm run build` locally for a full typecheck).
- Multi-architecture builds (only linux/arm64 verified on Apple Silicon).
- Behavior under concurrent multi-client access to one investigation.

**NOT SUPPORTED (current deployment model):**

- Production deployment / hardening (no TLS, no auth gateway, no secret
  manager — the compose file passes `ANTHROPIC_API_KEY` through as an
  environment variable by design for local MVP use).
- Horizontal scaling of the backend (SQLite single-writer).
- Hosting the Risk Platform inside this Compose project.
- Any guarantee that the running image equals a pure git-HEAD build:
  images are built from the working tree, and the currently shipped
  images include uncommitted working-tree changes on top of the tagged
  commit. Rebuild with a clean tree if provenance matters.

## 7. Known Image / Build Notes

- Base images: `python:3.12-slim`, `node:20-alpine`, `nginx:1.27-alpine`.
- The frontend build stage installs dependencies with `npm ci` against
  `package-lock.json`; dependency downloads go to the public npm registry.
- Registry metadata for base images is fetched from Docker Hub (a
  DaoCloud registry mirror may be configured in Docker Desktop). A
  transient `DeadlineExceeded` while loading base-image metadata is a
  network condition — retry the build; it is not a project defect.
- `backend/.dockerignore` excludes `.env`, virtualenvs, `data/`, and
  databases from the build context; `frontend/.dockerignore` excludes
  `node_modules/`, `dist/`, and `.env`.
