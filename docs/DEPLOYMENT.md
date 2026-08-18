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
| Backend (Railway) | https://uvmstudio-api-production.up.railway.app | deployed; verilator 5.050 + UVM 2020.3.1 live, UVM builds blocked by the 1 GB memory limit |

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

### Editing `railway.json` from Windows PowerShell

Railway's JSON parser rejects a UTF-8 **BOM**:

```
parse failure, failed to parse railway.json:
  failed to decode json file: invalid character 'ï' looking for beginning of value
```

Windows PowerShell 5.1's `Set-Content -Encoding utf8` writes UTF-8 **with** a
BOM — there is no `utf8NoBOM` option before PowerShell 7. Write the file
explicitly instead:

```powershell
[System.IO.File]::WriteAllText(
  "$PWD\railway.json", $json, (New-Object System.Text.UTF8Encoding $false))
```

Verify before committing — the first byte must be `7B` (`{`), not `EF BB BF`:

```powershell
Format-Hex railway.json | Select-Object -First 1
```

This applies to every JSON/YAML config in the repo, not just `railway.json`.

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

### Memory — the number that decides whether this works at all

Verilator concatenates its ~2,500 generated `.cpp` files into a small number of
aggregate translation units, and **the bucket count equals `--build-jobs`**.
That detail decides everything, and it is a trap: dropping `-j` to "save
memory" produces *fewer, larger* translation units and makes the peak worse.
A single `-j 1` build would put the entire UVM library in one compiler
invocation.

The platform therefore drives the two knobs separately — `--build-jobs` for how
many units are generated, `-MAKEFLAGS -jN` for how many compile at once — and
verifies the second actually caps concurrency.

Measured on 2 cores, gcc, `-Os`, no ccache, Accellera UVM 2020.3.1 plus a full
APB agent stack. Peak RSS of the largest single `cc1plus`:

| `--build-jobs` | translation units | peak `cc1plus` | C++ build |
|---:|---:|---:|---:|
| 2 (tied to `-j`, the old behaviour) | 4 | **2,620 MB** | 350.5 s |
| 16 (current default) | 31 | **1,096 MB** | 326.9 s |

**2.4× less memory and 7% faster** — so this is not a trade-off, and the split
is on by default. Override with `UVMSTUDIO_COMPILE_SPLIT`, or per project:

```yaml
backend_options:
  compile_split: 32
```

The floor moved once it was measured properly. At `-Os` the generated UVM
code needs ~1 GB per compile whatever the split. But **low-memory mode** —
`-O0`, the PCH stubbed out entirely, a 48-way split (91 TUs), `make -j1` —
peaks at **641 MB**, and the platform verified the full build plus a 6/6 L2
regression **inside a hard 1 GB cgroup** (swap off, 90 MB hog simulating the
API server). The mode engages automatically when the detected container limit
is under 2 GB; `UVMSTUDIO_LOW_MEMORY=1` forces it. Costs, measured: C++ build
746 s instead of ~330 s, simulation ~5x slower — for the golden project that
is 2.2 s per test instead of 0.4 s. **A 1 GB Railway container can therefore
run UVM regressions**; more memory buys back compile and sim speed rather
than unblocking anything.

| build | minimum container RAM |
|---|---:|
| low-memory mode (auto below 2 GB) | **~800 MB** |
| `-j 1`, normal `-Os` + PCH | ~2 GB |
| `-j 2` | ~3.5 GB |
| `-j 4` | ~6 GB |

Consequence for Railway: the 1 GB Trial container **runs UVM in low-memory
mode** (slowly); 2 GB+ gets normal mode back. Live limit read from the
deployment —

```
MEMORY_LIMIT_GB : 1.000
MEMORY_USAGE_GB : 0.085   (idle)
CPU_LIMIT       : 2
```

Below the floor the kernel kills the compiler and Verilator reports:

```
g++: fatal error: Killed signal terminated program cc1plus
```

which reads like a compiler bug and is not one. `uvmstudio build` classifies
this case explicitly and prints `CAUSE: compiler killed by the OOM killer ...`
with the measured figures rather than leaving you to decode it.

A non-UVM SystemVerilog project is nowhere near any of this — the scaffold
project builds and runs 6 seeds in about 7 seconds on a small container. **UVM
is the cost driver, and it is a memory cost before it is a CPU cost.**

### Sizing — read this before choosing a plan

Verilator's codegen for Accellera UVM is **2,506 generated C++ files**,
concatenated into 31 translation units. Measured end to end on 2 cores:
`uvmstudio build` completes in **305.8 s** and produces a 31 MB `simv`.
Consequences:

- The Docker build itself (Verilator from source) is long; it is the first layer
  so it caches, but the first build is slow.
- A **UVM** regression on a small container is dominated by C++ compilation,
  not simulation: ~5 minutes to build, ~0.4 s to run a test. Keep the built
  image warm by persisting `build/` on a volume — the build cache is keyed on
  source content, tool version and flags, so an unchanged project skips the
  compile entirely.
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
