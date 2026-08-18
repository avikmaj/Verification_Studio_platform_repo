# Deployment — Vercel frontend, Railway backend

Two independently deployable halves, joined at runtime rather than at build
time. The dashboard works against a laptop, a staging box or production without
a rebuild, and the backend has no idea a dashboard exists.

```
   browser                Vercel                     Railway
  ┌────────┐        ┌──────────────┐          ┌────────────────────┐
  │  user  │──────▶ │ Next.js dash │ ──HTTP──▶│ FastAPI  (app.py)  │
  └────────┘        │  (static)    │          │   ├ uvmstudio.*    │
                    └──────────────┘          │   ├ Verilator 5.050│
                                              │   ├ Z3, GTKWave    │
                                              │   └ Accellera UVM  │
                                              └────────────────────┘
```

## Live

| half | URL | status |
|---|---|---|
| Frontend (Vercel) | https://uvm-verification-studio.vercel.app | deployed |
| Backend (Railway) | project `uvm-verification-studio`, service `api` | created, awaiting a GitHub source |

## Frontend — Vercel

Deployed from `apps/web`. Nothing about the backend is baked in.

The API base URL resolves at runtime, in this order:

1. `?api=https://…` query parameter (also persisted)
2. `localStorage["uvmstudio.apiBase"]`
3. `NEXT_PUBLIC_API_URL` at build time

So the dashboard is usable immediately: type a URL into the header field and
press **Connect**. Point it at `http://localhost:8000` for a local API, or at
the Railway URL once the backend is up.

The **bearer token is never persisted** — it lives in React state for the tab.
A URL is not a credential; a token that runs a simulator is.

Redeploy:

```bash
cd apps/web
npx vercel --prod           # or push apps/web to a Vercel-connected repo
```

## Backend — Railway

`apps/api/Dockerfile` builds the full execution stack: Verilator 5.050 from
source (the distro package lags on class/UVM support), Z3, GTKWave and the
Accellera UVM library. `railway.json` points Railway at it.

Railway deploys from a connected **GitHub repository** — it does not accept an
uploaded directory. So the sequence is:

```bash
# 1. push this repo to GitHub
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

Then, in Railway (project `uvm-verification-studio`, service `api`):

```
Source            : GitHub → <owner>/<repo>, branch main
Dockerfile path   : apps/api/Dockerfile
Healthcheck path  : /health
```

### Do not put `startCommand` in `railway.json`

Railway executes a configured `startCommand` **without a shell**, so
`--port $PORT` arrives as the literal four characters `$PORT`:

```
Error: Invalid value for '--port': '$PORT' is not a valid integer.
```

The Dockerfile's `CMD ["sh", "-c", "uvicorn ... --port ${PORT:-8000}"]` already
expands it correctly, so `railway.json` deliberately omits `startCommand` and
lets the image decide. Config-as-code overrides the dashboard, so setting the
command in the Railway UI will *not* rescue a bad `railway.json` — fix the file.

Set the variables (already staged except the token):

| variable | value | why |
|---|---|---|
| `UVMSTUDIO_API_TOKEN` | a long random string | **execution is disabled without it** |
| `UVMSTUDIO_WORKSPACE` | `/workspace` | where projects live |
| `UVM_HOME` | `/opt/uvm-core/src` | baked into the image |
| `UVMSTUDIO_CORS_ORIGINS` | your Vercel origin | tighten from `*` once the URL is known |

Generate a token:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Sizing — read this before choosing a plan

Verilator's codegen for Accellera UVM is **2021 C++ translation units** and took
~2 hours on 2 cores in development, producing a 21 MB binary. Consequences:

- The Docker build itself (Verilator from source) is long; it is the first layer
  so it caches, but the first build is slow.
- A **UVM** regression on a small container will be dominated by C++ compilation,
  not simulation. Budget CPU accordingly, or keep the built image warm by
  persisting `build/` on a volume.
- A **non-UVM** SystemVerilog project builds in seconds — the golden `demo`-style
  project compiles and runs 6 seeds in about 7 seconds.

Mount a Railway volume at `/workspace` if you want results, coverage databases
and the regression history to survive redeploys. Without one they are ephemeral;
the durable record is still written per run as `repro.json`.

### Security posture — stated plainly

This service runs a simulator, and a simulator runs arbitrary code (DPI, `$system`,
generated C++). It is built for a **trusted deployment**:

- every mutating route requires the bearer token; with no token configured, the
  execution routes return `503` rather than running anything
- project paths are resolved against the workspace root and rejected if they
  escape it; the same check guards artifact and waveform reads
- jobs run with a timeout and a bounded worker pool
- it is **not** hardened for untrusted multi-tenant input, and is not presented
  as if it were

Put it behind your own auth if it will face anything but your own team.

## Local development

```bash
# API
export UVMSTUDIO_WORKSPACE=/path/to/projects
export UVMSTUDIO_API_TOKEN=dev-token
export UVM_HOME=/path/to/uvm-core/src
uvicorn apps.api.app:app --reload --port 8000

# Dashboard
cd apps/web
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

## Driving the deployment from the CLI

The `remote` backend turns any deployment into an `ExecHost.REMOTE`, so a
Windows laptop with no WSL — or a thin CI runner — can execute real simulations:

```bash
export UVMSTUDIO_API_URL=https://your-api.up.railway.app
export UVMSTUDIO_API_TOKEN=…

python3 -c "
from uvmstudio.simulator.base import get_simulator
r = get_simulator('remote')
status, job = r.regress_remote('golden_apb', tier='L1', seed=1,
                               on_log=print)
print('STATUS:', status.value)
"
```

`build()` and `run()` deliberately raise `UnsupportedFeature` on this backend:
the server owns the workspace, so a local-looking build/run pair would silently
lose the log, waveform and coverage artefacts. Saying so beats pretending.

## API surface

| route | purpose |
|---|---|
| `GET /health` | liveness |
| `GET /env` | toolchain, capabilities, UVM version, execution enabled |
| `GET /projects` | workspace projects and their test databases |
| `GET /projects/{p}/design` | elaborated IR: hierarchy, classes, covergroups |
| `GET /projects/{p}/lint` | lint findings + rule catalogue with statuses |
| `GET /projects/{p}/regressions` | history, failure clusters, seed effectiveness |
| `GET /projects/{p}/regressions/{id}` | full report |
| `GET /projects/{p}/coverage` | merged coverage, by kind, with holes |
| `GET /projects/{p}/waveform` | VCD/FST summary |
| `GET /projects/{p}/waveform/signal` | value changes in a time window |
| `GET /projects/{p}/repro` | reproducibility record |
| `POST /jobs` | submit compile / lint / build / regress **(token)** |
| `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/log` | job status and streaming log |
| `POST /jobs/{id}/cancel` | cancel **(token)** |
