# DreamCast AI

An AI creative workspace for organising movie/content projects.

- **Phase 1** (done): accounts, projects, characters, references, usage, admin, storage, provider abstraction.
- **Phase 2** (done, see [Phase 2](#phase-2--universal-create-refinement-and-background-generation)): the Universal Create flow,
  prompt refinement, and the background generation engine (jobs, worker, history, notifications, limits).
- **Phase 3** (done, see [Phase 3](#phase-3--story-script-lyrics-music-and-voice)): the first real generators — **Story, Script, Lyrics,
  Music and Voice** — with versioned, editable project assets.
- **Phase 4** (this version, see [Phase 4](#phase-4--video-image-to-video-and-face-replacement)): **video generation** (text-to-video, image-to-video,
  reference images, characters, script scenes, story sections) and **face replacement**, with thumbnails and secure streaming.
- **Not yet built:** AI avatars and interactive avatars (Phase 5+). The optional **development simulator** now serves *only* those two generators
  and labels every result as simulated; every other generator uses a real provider and never falls back to fake output.

| Layer | Choice |
|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS |
| Backend | Python + FastAPI + SQLAlchemy 2 + Alembic |
| Database | SQLite locally (PostgreSQL-ready via `DATABASE_URL`) |
| Auth | Built-in email/password + JWT **or** Supabase Auth (Google login) — switch with `AUTH_PROVIDER` |
| Storage | Local filesystem behind a `Storage` interface (`storage/projects`, `uploads`, `generated`) |
| Jobs | DB-backed queue (`generation_jobs`) + in-process worker threads (or a standalone `python -m app.worker`) |

## Requirements

- Python 3.11+ (developed on 3.14)
- Node.js 18+ and npm

## Quick start (macOS / Linux)

```bash
./scripts/setup.sh     # venv + deps + .env (random secret) + DB migrations + npm install
./scripts/dev.sh       # backend :8000 and frontend :5173
```

Open http://localhost:5173, click **Get started**, and register. Interactive API docs: http://localhost:8000/docs (development only).

### Manual commands (any OS)

```bash
# 1. Install dependencies
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt     # Windows: backend\.venv\Scripts\pip
cd frontend && npm install && cd ..

# 2. Configure environment variables
cp .env.example .env
#    then set AUTH_SECRET_KEY to a long random string, e.g.:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Initialise the database (creates backend/dreamcast.db)
cd backend && .venv/bin/alembic upgrade head

# 4. Run the backend (from backend/)
.venv/bin/uvicorn app.main:app --reload --port 8000

# 5. Run the frontend (new terminal, from frontend/)
npm run dev

# 6. Run the tests
cd backend && .venv/bin/pytest -q            # backend (171 tests)
cd frontend && npm run typecheck && npm run build   # frontend type-check + production build
./scripts/test.sh                            # all of the above
```

## Becoming an admin

Either set `ADMIN_EMAILS=you@example.com` in `.env` before registering (restart the backend), **or** register first and run:

```bash
cd backend && .venv/bin/python -m app.cli make-admin you@example.com
```

Admins get an **Admin** entry in the sidebar (users, enable/disable, roles, generation limits, failed jobs, providers). Every
`/api/admin/*` route is enforced on the backend.

## Authentication

**`AUTH_PROVIDER=local` (default, zero setup).** Register/login with email + password. Passwords are bcrypt-hashed, sessions are
signed JWTs. New accounts verify their email with a 6-digit code (SMTP, see below); *Forgot password* emails a single-use reset link;
**Continue with Google** works when the Google variables are set. Without SMTP those email features answer "email isn't set up" (nothing is faked).

**`AUTH_PROVIDER=supabase` (Google login, email confirmation, emailed password reset).**
1. Create a free project at supabase.com. *Authentication → Providers*: enable Google (needs a Google OAuth client) and Email.
2. *Authentication → URL Configuration*: Site URL `http://localhost:5173` (your deployed frontend URL in production) and add
   `http://localhost:5173/auth/callback` to Redirect URLs.
3. In `.env`: `AUTH_PROVIDER=supabase`, `AUTH_URL=https://<ref>.supabase.co`, `AUTH_PUBLIC_KEY=<anon/publishable key>`.
   For legacy HS256 projects also set `AUTH_SECRET_KEY=<JWT secret>`; projects using asymmetric signing keys are verified through the
   project's JWKS endpoint automatically.
4. Restart the backend. The browser talks to Supabase for sign-in and sends the resulting token to this API, which verifies it and
   creates the local user record on first use. Roles, project ownership and limits always live in this app's database.

> ⚠️ The Supabase mode is implemented but **could not be tested end-to-end here** (it needs your Supabase project and Google credentials).
> The local mode is fully tested.

## Environment variables (`.env.example`)

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` or `production` (production hides API docs and refuses the default secret) |
| `DATABASE_URL` | SQLite by default; relative sqlite paths resolve from the project root |
| `CORS_ORIGINS`, `FRONTEND_URL` | Allowed browser origins / link base |
| `AUTH_PROVIDER`, `AUTH_URL`, `AUTH_PUBLIC_KEY`, `AUTH_SECRET_KEY` | See *Authentication* |
| `ADMIN_EMAILS` | Comma-separated emails that get the ADMIN role |
| `STORAGE_BACKEND`, `STORAGE_DIR`, `MAX_UPLOAD_MB` | Storage location and the 10 MB image limit |
| `RATE_LIMIT_AUTH_PER_MINUTE` | Login/register/reset rate limit per IP (0 = off) |

Secrets exist only in `.env` (git-ignored). Nothing secret is sent to the browser; provider API keys added later stay server-side.

## Project layout

```
backend/
  app/
    main.py config.py db.py models.py schemas.py errors.py security.py deps.py uploads.py generators.py cli.py
    routers/      auth, projects, characters, references, files, jobs, notifications, admin, meta
    services/     projects, usage, jobs, notifications, auth   (business logic)
    providers/    base.py (Provider interface), registry.py     (future AI adapters plug in here)
    storage/      base.py (interface), local.py                 (swap for S3/R2 later)
  alembic/        migrations
  tests/          pytest suite
frontend/src/
  components/ui/  design system: feedback, Modal, Menu, Tabs, Field, AuthImage
  components/layout/  AppShell (responsive drawer), NotificationBell, UserMenu, Guards
  pages/          Landing, auth, Dashboard, Create, Projects, ProjectWorkspace(+project/), Usage, Settings, Account, Admin
  context/ hooks/ lib/   auth + theme state, API client, types
scripts/          setup.sh, dev.sh, test.sh
```

## Database

`users`, `projects`, `characters`, `reference_assets`, `generation_jobs`, `generated_assets`, `usage_records`, `notifications`,
`app_settings` (admin-editable limits). Project deletion cascades to characters, references, assets and jobs, and removes the
project's files. Large files live on disk/object storage; the DB stores keys and metadata only. To move to PostgreSQL:
`pip install "psycopg[binary]"`, set `DATABASE_URL=postgresql+psycopg://…`, run `alembic upgrade head`.

## Deployment notes (provider-neutral)

- **Frontend:** static site. `cd frontend && npm run build` → serve `frontend/dist` on any static host. By default the app calls relative
  `/api/...` (same domain behind a proxy); to call a separate backend set `VITE_API_URL` at build time. See **Production Deployment Checklist** below.
  Add an SPA fallback so unknown paths serve `index.html`.
- **Backend:** any host that runs Python: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Run `alembic upgrade head` on deploy.
  Set `APP_ENV=production`, a strong `AUTH_SECRET_KEY`, `CORS_ORIGINS` and `FRONTEND_URL` to your real domain.
- **Database:** SQLite is fine for a single-instance demo **only if the disk is persistent**; free hosts with ephemeral disks need
  PostgreSQL (many free tiers exist).
- **Storage:** local disk has the same persistence caveat. `Storage` is an interface; add an object-storage implementation before
  scaling beyond one instance (Prompt 6).
- **Rate limiting** is in-memory per process; use a shared store if you run several instances. Behind a proxy, configure it to
  pass the client IP (`uvicorn --proxy-headers`).

## Phase 2 — Universal Create, refinement and background generation

### The flow
1. **Create** → pick a generator (Video, Music, Voice, Lyrics, Story, Script, Face Replacement, AI Avatar, Interactive Avatar).
2. Only that generator's options appear (rendered from `GET /api/generate/schema`, so backend and UI never drift). Everything is optional except
   what the generator truly needs (a prompt; a face image for Face Replacement).
3. Type a natural request and click **Refine prompt** → **one** LLM call turns it into a generator-specific prompt, using only relevant
   project context (characters, story, references — never the whole database).
4. **Review**: edit the refined prompt, regenerate the refinement, or go back. **Nothing is generated until you click Generate.**
5. **Generate** → the API validates, checks your daily limit and provider availability, creates a `QUEUED` job and returns immediately
   ("Generation started."). Leave the page; keep using DreamCast.
6. A background worker runs the job, saves the result to the project, and creates a notification (🔔). Track it on **History** or the job page.

Durations are enforced server-side: video/music max 30 s (10/20/30). If the prompt or form asks for more (e.g. "make it 45 seconds")
the refine step shows *"Maximum video duration is 30 seconds. Duration adjusted to 30 seconds."* and submission rejects anything above 30.

### Prompt refinement
`services/refinement.py` (`PromptRefinementService`): input generator + prompt + normalised options + `ProjectContextService` context →
`{refined_prompt, structured_prompt, metadata}`. The system prompt is generator-specific (video: subject/action/camera/lighting/motion…;
music: genre/mood/instruments/tempo…; script: plot/characters/scenes/dialogue…) and forbids inventing plot, characters or changing
genre/duration/language. Prompts are kept short (word limits per generator) to save tokens.

**Provider:** any OpenAI-compatible chat API via `LLM_PROVIDER` (`groq` default, `gemini`, `openrouter`, `ollama`, `custom`). Free tiers exist for
Groq and Gemini. **Without a key the app still works**: a deterministic built-in template is used and the UI says so. If the LLM fails or
rate-limits, it falls back to the template with a warning. Refinements are capped per user per day (`REFINE_DAILY_LIMIT`), and only real
LLM calls count.

To enable (server-side only; the key never reaches the browser):
```bash
# .env
LLM_PROVIDER=groq
LLM_API_KEY=<your key from console.groq.com>     # or gemini / openrouter …; leave empty for the template fallback
```

### Generation jobs and the worker
- **One process by default.** The API starts worker threads at startup (`WORKER_ENABLED=true`, `WORKER_CONCURRENCY=2`). `./scripts/dev.sh` is
  all you need: backend + workers + frontend.
- **Separate worker (optional):** set `WORKER_ENABLED=false` for the API and run `cd backend && .venv/bin/python -m app.worker` in another terminal.
  Several workers are safe: jobs are claimed atomically in the database.
- Statuses: `QUEUED → PROCESSING → COMPLETED | FAILED | CANCELLED`, plus `RETRYING`. Stages: Queued → Preparing → Generating → Processing → Completed.
  **No fake percentages**: `progress` is null unless a provider reports real progress.
- **Retry:** at most **one** automatic retry, only for transient errors (provider unavailable / rate-limited / timeout). Invalid requests, bad API keys,
  provider quota and configuration errors are never retried. Failed jobs can be retried manually (creates a new linked job).
- **Cancel:** queued jobs cancel instantly. Running jobs are flagged; the worker calls the provider's `cancel()` if it supports it, otherwise stops locally
  and the job page says the provider may still finish remotely. It never claims a remote cancellation that didn't happen.
- **Restart safety:** on startup, jobs left in `PROCESSING` by a crash go back to the queue.
- **Regenerate** (same settings) and **Edit prompt** (Create prefilled, submit with `parent_id`) both create a **new** job; the original is kept.
- Error categories: `API_NOT_CONFIGURED`, `PROVIDER_UNAVAILABLE`, `INVALID_REQUEST`, `QUOTA_EXCEEDED`, `AUTHENTICATION_ERROR`, `RATE_LIMITED`,
  `GENERATION_FAILED`, `STORAGE_ERROR`, `UNKNOWN_ERROR`. Users see plain-language messages; technical details go only to the server log.

### Provider architecture
```
providers/base.py       Provider (= BaseGenerationProvider): is_configured / validate_config / generate / get_status / cancel
providers/text.py       TextProvider + PromptRefinementProvider (OpenAI-compatible LLM client)
providers/simulator.py  DevSimulatorProvider (fake, clearly labelled; ENABLE_DEV_SIMULATOR=true)
providers/registry.py   registry + register_default_providers()  ← register real Video/Music/Voice/... providers here in Phase 3
services/provider_settings.py   admin enable/disable + optional daily cap per provider; selection + availability
```
A future provider only subclasses `Provider`, lists the generator ids it serves, and registers itself. Providers return a `ProviderResult` (text and/or file);
the runner stores it as a `GeneratedAsset` in the project (files via the storage abstraction). DreamCast **never invents provider quota numbers**: the admin
view shows "unknown" unless a provider reports it.

**Development simulator.** With no real avatar provider yet, `ENABLE_DEV_SIMULATOR=true` (default in `.env.example`) lets you run the complete pipeline. Its
output is a text note marked `[SIMULATED OUTPUT]` (asset status `SIMULATED`, badges in the UI). Prompt markers trigger behaviours for testing:
`[simulate:fail]`, `[simulate:transient]` (fails once, succeeds on retry), `[simulate:invalid]`, `[simulate:noquota]`, `[simulate:slow]` (~30 s, to test cancel),
`[simulate:nocancel]`. Turn it off in production. With it off and no provider registered, Generate is blocked with an "isn't set up yet" message.

### Usage limits (per user, per day, UTC)
Video 3 · Music 3 · Voice 5 · Lyrics 5 · Story 5 · Script 3 · Face 3 · Avatar 3 · Interactive Avatar 12 (4× the standard allowance).
Defined once in `generators.py`, editable at runtime by admins (Admin → Limits, stored in `app_settings`). Order on submit: authenticate → validate
(generator, options, project ownership, references) → daily limit → provider availability/cap → create job + reserve one unit.
**Accounting:** a unit is reserved when the job is created and *returned* if the job never reached a provider (config/auth/invalid/quota errors, provider
missing, cancelled while queued). It stays spent if the provider ran (including provider-side failure or a cancel after start). Retrying inside one job
never double-counts. Admins can also disable a provider or cap it per day.

### API (all under `/api`, Bearer token required)
| Method & path | Purpose |
|---|---|
| `GET /generate/schema` | per-generator fields, prompt rules, availability |
| `POST /generate/refine` | `{generator_type, prompt, options, project_id, reference_assets}` → `{refined_prompt, structured_prompt, metadata}` |
| `POST /generations` | `{generator_type, original_prompt, refined_prompt, options, project_id, reference_assets, parent_id?}` → `{job_id, status:"QUEUED"}` (returns immediately) |
| `GET /jobs` | history; filters `type` (comma list; `FACE`/`AVATAR` aliases ok), `status`, `project_id`, `limit`, `offset` |
| `GET /jobs/{id}` | id, type, status, stage, progress (real or null), prompts, options, output, assets, error, timestamps |
| `POST /jobs/{id}/cancel` · `/regenerate` · `DELETE /jobs/{id}` | cancel, same-settings regenerate/retry (new job), delete finished job |
| `GET /notifications` · `POST /notifications/{id}/read` · `POST /notifications/read-all` | notification list/unread/mark read |
| `GET/PUT /admin/providers[/{name}]` | admin: provider status, enable/disable, daily cap |
Spec-style names (`VIDEO`, `FACE`, `AVATAR`, `INTERACTIVE_AVATAR`) are accepted as generator types.

### Phase 2 environment variables
`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS`, `REFINE_DAILY_LIMIT`, `REFINE_RATE_LIMIT_PER_MINUTE`, `WORKER_ENABLED`,
`WORKER_CONCURRENCY`, `WORKER_POLL_SECONDS`, `PROVIDER_POLL_SECONDS`, `JOB_TIMEOUT_SECONDS`, `JOB_MAX_AUTO_RETRIES`, `JOB_RETRY_DELAY_SECONDS`,
`ENABLE_DEV_SIMULATOR`, `DEV_SIMULATOR_STEP_SECONDS` — all documented in `.env.example`. Upgrading from Phase 1: `alembic upgrade head` (migration `0002`, keeps existing data).

### Running everything
```bash
./scripts/setup.sh          # once (or after pulling): deps + .env + migrations
./scripts/dev.sh            # backend (:8000, includes worker threads) + frontend (:5173)

# Separate worker variant:
WORKER_ENABLED=false ./scripts/dev.sh            # terminal 1
cd backend && .venv/bin/python -m app.worker     # terminal 2 (reads the same .env)

# Try the LLM code path without an API key (dev only): a fake OpenAI-compatible server
python3 scripts/fake_llm_server.py               # then in .env: LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:9099/v1 LLM_MODEL=fake LLM_API_KEY=dev
```

### Testing
`./scripts/test.sh` runs the backend suite (171 tests: auth, projects/ownership, refinement incl. mocked LLM success/failure/daily cap, submission validation,
usage limits and refunds, job lifecycle, retry, cancellation, worker threads, admin controls, notifications) plus the frontend type-check and production build.

## Phase 3 — Story, Script, Lyrics, Music and Voice

Five generators now run end to end through the same pipeline as everything else (Create → refinement → confirm → background job → provider →
`GeneratedAsset` in the project → notification), reusing the existing jobs, usage limits, providers registry, storage and admin panel. Nothing is duplicated.

| Generator | Output | Provider | Calls per request |
|---|---|---|---|
| **Story** | editable text outline: TITLE, LOGLINE, GENRE, SETTING, MAIN CHARACTERS, ACT 1–3, ENDING (genre, English/Hindi/Telugu, Short/Medium/Long or your own length) | LLM (`LLM_*`) | 1 refinement + 1 generation |
| **Script** | detailed scene-by-scene screenplay: characters, scene list, then per scene INT/EXT, location, time, environment, action, dialogue, camera, lighting, sound, music cue, transition, `END` (genre, language, length, optional 5/10/20/30-minute target) | LLM (`LLM_*`) | 1 + 1 |
| **Lyrics** | editable lyrics with `[VERSE 1] [CHORUS] …` (prompt + optional language only) | LLM (`LLM_*`) | 1 + 1 |
| **Music** | audio file (genre, mood, 10/20/30 s, optional reference lyrics) | Hugging Face MusicGen (`MUSIC_*`) | 1 refinement + 1 provider call |
| **Voice** | audio file (gender, accent, emotion, language, text) | Google Cloud Text-to-Speech (`VOICE_*`) | 1 provider call, **no LLM** |

### Providers, keys and what happens without them
All keys are server-side environment variables (`.env`); nothing secret reaches the browser, logs or API responses (the Google key is sent in a header, not the URL).
The app **starts and the Create pages work with nothing configured**: the request is accepted, the job fails cleanly (allowance returned, notification sent, no asset
created) with a specific message — *"Text generation provider is not configured."*, *"Music provider is not configured…"*, *"Voice provider is not configured…"*.

| Need | Variables | Where to get a key |
|---|---|---|
| Story, script, lyrics **and** prompt refinement | `LLM_PROVIDER` (`groq`/`gemini`/`openrouter`/`ollama`/`custom`), `LLM_API_KEY`, optional `LLM_MODEL`, `LLM_BASE_URL`, `LLM_GENERATION_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS` | free tiers: console.groq.com or aistudio.google.com |
| Music | `MUSIC_PROVIDER=huggingface`, `MUSIC_API_KEY`, `MUSIC_MODEL` (default `facebook/musicgen-small`), `MUSIC_MAX_SECONDS`, `MUSIC_BASE_URL`, `MUSIC_TIMEOUT_SECONDS` | free Hugging Face token (huggingface.co/settings/tokens) |
| Voice | `VOICE_PROVIDER=google`, `VOICE_API_KEY`, `VOICE_BASE_URL`, `VOICE_TIMEOUT_SECONDS` | Google Cloud API key with Text-to-Speech enabled (free monthly tier) |

*Admin → Providers* and *Settings → API Configuration* show each provider as configured / not configured (a settings check only; no API call is made to test health),
and let admins enable/disable a provider or cap it per day. **Limitation:** keys are environment-only. There is deliberately no admin form that stores keys in the
database (SQLite isn't a safe place for secrets); change a key by editing `.env` and restarting.

Adding another vendor later means one new class: subclass `MusicProvider` / `VoiceProvider` / `TextProvider` (or `SyncProvider`) and register it in
`providers/registry.py`. The runner and UI never mention vendors.

### Languages and provider capabilities (nothing is faked)
- **Text generators:** English, Hindi (Devanagari) and Telugu (Telugu script) are requested from the LLM; structural labels stay in English. Quality depends on the model.
- **Voice:** English (Indian / American / British), Hindi, Telugu; male/female. Google has no emotion control, so emotion is **approximated with speaking rate and
  pitch** and the asset lists that as a note. Accent *Other* uses American English (noted). If a provider can't do a language, the request is rejected up front with
  an explanation instead of producing wrong output. Text is limited to ~4,800 bytes per request (pick a scene for long scripts).
- **Music:** durations offered = 10/20/30 s limited by `MUSIC_MAX_SECONDS`; DreamCast never requests a longer clip than the provider supports (validated in the UI, at refine
  time and again before the call). MusicGen makes **instrumental** clips: lyrics you select only inform the mood of the music prompt; they are not sung.
- **Originality:** all prompts require original output and forbid reproducing existing scripts, songs or lyrics.

### Workflow hand-offs (all pre-fill Create; nothing is generated without your confirmation)
Story → **Generate Script from Story** (the story is supplied automatically) · Lyrics → **Generate Music** · Script → **Generate Voice** (entire script, one scene, or one
scene's dialogue; scene links in the script sidebar also offer Music for a scene) · any text asset → **Generate Voice**.

### Assets, versions and editing
- Every result is a `GeneratedAsset` in the project (never global). **Regenerate never overwrites**: it adds *Script v2, v3…* to the same lineage; open any version and use **Compare**.
- Story/script/lyrics are plain editable text: **Edit → Save** (no AI call), **Copy**, **Download .txt / .md**, **Duplicate**, **Delete**. Audio assets have a native player
  (play/pause/seek), **Download** (real file), Regenerate, Duplicate, Delete. Project → **Assets** tab filters All / Story / Script / Lyrics / Music / Voice.
- **Scenes:** scripts must use `SCENE 01` headings; the app derives the scene list (number, heading, offsets) from the text with a small deterministic parser, and re-derives it when
  you edit — no extra AI call and no fragile JSON. Phase 4 can use `asset.meta.scenes` to pick a scene for video.
- Database: migration `0003` adds `version`, `lineage_id`, `format`, `language`, `mime_type`, `duration_seconds`, `updated_at` to `generated_assets` (existing assets become v1).

### Background behaviour and limits
Exactly the Phase 2 job system. Stages: Queued → Preparing → Generating (*Provider processing* for music/voice) → Saving result → Completed. One automatic retry, transient errors only.
A failed job never leaves an asset behind. Daily limits (existing usage service, admin-editable): Story 5 · Script 3 · Lyrics 5 · Music 3 · Voice 5 (Face 3, Avatar 3, Interactive Avatar 12).
Cost control: refinement is one LLM call; voice makes no LLM call (the text you enter is spoken as written; "Say this in a calm voice: …" only sets the emotion);
prompts sent to the LLM contain only concise, relevant context (the selected story for a script, a single scene for music), never the whole project.
A 30-minute script is one LLM call condensed to a fixed scene count per duration and capped by `LLM_MAX_OUTPUT_TOKENS`; for a longer or more detailed script, regenerate or edit.

### Testing (Phase 3)
`./scripts/test.sh` — provider HTTP is fully mocked (no credits used). To try the whole thing locally **without any keys**, `python3 scripts/fake_llm_server.py` starts stand-ins for the LLM
(:9099), music (:9098, an audible tone) and voice (:9097, silent valid MP3); the `.env` values to point at them are listed at the top of that script.

## Phase 4 — Video, image-to-video and face replacement

Video and Face Replacement are now real generators on the same pipeline as everything else: Create → prompt refinement (one call) → **you confirm** → usage
check → background job → provider → download into storage → project asset (+ thumbnail) → notification. No second queue, usage or notification system was added.

### Providers, cost and keys (read this first)
| Feature | Provider (adapter) | Cost | Key variable |
|---|---|---|---|
| Text-to-video, image-to-video | **fal.ai** queue API, default model **Kling v3 Standard** (Kling 1.6 and 2.1 are deprecated by fal.ai) | **Paid, pay-as-you-go** (no free tier you can rely on; fal may grant starter credit) | `VIDEO_PROVIDER_API_KEY` |
| Face replacement (image sources) | **fal.ai** `fal-ai/face-swap` | Paid, pay-as-you-go (much cheaper than video) | `FACE_PROVIDER_API_KEY` |

I found no video API that is genuinely free for 10-second clips, so nothing here runs, or costs anything, until you add a key. Without keys the whole app still starts;
Create works; a video request is accepted and its job fails immediately with *"Video generation is currently unavailable because the video provider has not been
configured."* (face: *"Face replacement provider is not configured."*), the daily allowance is returned and no file is made. **DreamCast never generates automatically, never
regenerates automatically and always makes exactly one video per Generate click**; asking for "4 versions" just shows how many generations you have left. Add keys to `.env`
(server-side only; keys are never sent to the browser or logged and admin screens show only "Configured / Not configured"):

```bash
VIDEO_PROVIDER_API_KEY=<fal key>       # + optional VIDEO_PROVIDER_MODEL / VIDEO_PROVIDER_I2V_MODEL / VIDEO_MAX_SECONDS / VIDEO_ASPECT_RATIOS / VIDEO_POLL_SECONDS
FACE_PROVIDER_API_KEY=<fal key>        # can be the same key; + FACE_PROVIDER_MODEL
```
Provider classes: `providers/video.py` (`VideoProvider` interface + `FalVideoProvider`), `providers/face.py` (`FaceProvider` + `FalFaceProvider`), shared queue client in `providers/fal.py`.
Swapping vendors means one new subclass registered in `providers/registry.py`; nothing else knows the vendor. Model payload field names (`prompt`, `duration`, `aspect_ratio`,
`start_image_url`, `generate_audio`) follow the Kling v3 schema published by fal.ai (checked against fal.ai's docs; older models keep `image_url`), so a different model may need a small change in `build_request`.

### Video creation
- **Method:** Text to Video or Image to Video. **Style** (Cinematic, Realistic, Anime, 3D, Cartoon, Fantasy, Horror, Sci-Fi, Documentary, Custom), **duration**, **aspect ratio**, prompt
  ("What should happen in the video?"), optional characters, references, script scene and story section. Each option is optional; the prompt is optional for image-to-video.
- **Duration rules (enforced at three layers: UI/refine, backend submit, provider adapter):** default **10 s**; allowed 10/20/30; maximum **30 s**. "Make it 45/60/100 seconds" is capped to 30
  with *"Maximum video duration is 30 seconds. Duration adjusted to 30 seconds."* and the refined prompt states the final duration; "make the video longer" uses the 30 s maximum. The server
  never trusts the client: submitting more than 30, or more than the provider supports, is rejected. **Provider reality check:** Kling makes 5 or 10 second clips, so with the default
  `VIDEO_MAX_SECONDS=10` a 20/30 s request is reduced to 10 s *before generation, with an explanation* (the UI only offers durations the provider supports). Set `VIDEO_MAX_SECONDS=20|30`
  only with a model that truly supports it. DreamCast does not stitch clips together (that is multi-scene generation, a later phase).
- **Aspect ratios:** 16:9, 9:16, 1:1. If the provider supports fewer, the closest is used with a notice; nothing is stretched.
- **Prompt refinement (one call):** a video-specific template produces Subject / Action / Environment / Lighting / Camera / Motion / Mood / Style lines, keeping your concept and inventing
  no major characters or plot. Editing the refined prompt never triggers another AI call. Project **visual style** (Edit project → "Visual style") is added as context only when you didn't pick a style.
- **Image-to-video:** upload or pick a project image (identical files are never stored twice); it is sent to the provider as the first frame. If the provider has no image-to-video model:
  *"This provider does not support image-to-video."* Nothing is faked.
- **References and characters:** choose characters (none by default) and reference images. This provider does not accept multi-image references, so they are **described in the prompt**
  (character appearance/clothing, reference names/types), shown as "Character reference", never as guaranteed consistency. Only the image-to-video source image is sent as an image.
- **Script scene → Video:** every scene on a script page has **Generate Video**. It fills the project, scene, matching characters, and a draft prompt from the scene's location, environment, action,
  lighting, camera and mood (dialogue goes to the refinement as context only); only that scene is sent. **Story → Video:** pick a section (Setting, Act 1–3, Ending) and **Create Video**.
  Both open Create for review; each video is a separate generation you confirm.

### Background behaviour, storage and playback
Stages: Queued → Preparing → Submitting → Provider processing → Downloading → Storing → Completed (no invented percentages; the provider gives none). The worker polls every `VIDEO_POLL_SECONDS`
(default 8 s, not every second). The provider job reference is saved on the job, so if the worker restarts mid-generation it **resumes polling instead of resubmitting and paying twice**;
short polling errors never cause a resubmit. Provider output URLs are followed only over https, streamed to a temp file (size-capped), probed with the bundled ffmpeg
(`imageio-ffmpeg`, no system install needed), given a JPEG thumbnail, and moved into storage, so nothing depends on a temporary provider URL and videos are never held in memory.
Retry: one automatic retry for transient errors only; **Retry** on a failed job makes a new job. Cancel: the provider is asked when it supports it (fal only cancels jobs still queued);
otherwise the job is stopped locally and the page says so, never claiming a remote cancellation.
Media are private. `<video>` plays through a **short-lived signed URL** (30 min, one file, one user) issued after an ownership check, which enables seeking (HTTP Range) without loading the
whole file; the player is lazy (thumbnail first, no media request until Play), with native play/pause/seek/volume/fullscreen. Download streams from disk. Thumbnails appear in the project,
history, dashboard and asset lists (lazy-loaded). Set a real `AUTH_SECRET_KEY` in production: it also signs these URLs.

### Face replacement
Source (image, or a validated video upload) + face image + optional note + the required confirmation *"I confirm that I have permission to use the face/image uploaded for this generation."*
(enforced by the server, not just the checkbox). Uploads are validated by content, not file name: images (PNG/JPEG/WebP/GIF, `MAX_UPLOAD_MB`, face at least 64×64) and videos
(MP4/MOV/WebM, `MAX_VIDEO_UPLOAD_MB`=100, at most 2 minutes, must be decodable), streamed to disk, safe file names, private. The built-in provider swaps faces in **images**; a video source is accepted for
upload but refused before generation with a clear message (the architecture is ready for a video-capable provider). It is a creative editing tool: no identity verification, no face recognition and no face database.
The note is stored with the request but this model ignores text instructions. Face refinement is local (no LLM call).

### Limits, admin and endpoints
Video 3/day, Face Replacement 3/day (admin-editable, same usage service). Invalid input is rejected before a job exists, so it never consumes allowance; jobs the provider never accepted are refunded; a job the
provider accepted stays counted. Admin → Providers lists Text, Music, Voice, Video and Face providers with Configured/Not configured, Enabled/Disabled, model and API-key status (never the key).
New/changed API: source-video upload `POST /api/projects/{id}/references/video`; `POST /api/media/stream-url`, `GET /api/media/{token}`; `GET /api/files/thumbnail/{asset}`;
`GET /api/assets/{id}/scenes/{n}` and `/sections`; project `style`; `permission_confirmed`, `source_asset_id`, `face_asset_id`, `method`, `story_section` generation options. Migration `0004` adds
`notifications.asset_id` (notifications now open the finished asset). Local setup is unchanged (`./scripts/setup.sh` installs the new `imageio-ffmpeg`).

### Testing (Phase 4) and troubleshooting
Tests mock the fal.ai queue (submit → status → result → file → cancel) and use real tiny MP4/PNG fixtures, so probing, thumbnails, storage, streaming and Range requests are exercised for real. To try the whole flow without a key:
`backend/.venv/bin/python scripts/fake_llm_server.py` also starts a fal-style stand-in on :9096 (see the header of that script for the `.env` values; it makes real test-pattern files and is **not** a generator).
- *"…video provider has not been configured"* → set `VIDEO_PROVIDER_API_KEY` and restart the backend. *Duration was reduced* → your model's limit (`VIDEO_MAX_SECONDS`). *Thumbnail missing* → ffmpeg could not run; the video still plays.
- *Video won't play after a while* → press Play again (the signed link expired). *"Provider rejected its API key"* → check the key/credit on fal.ai. *Jobs stuck queued* → is the worker running (`WORKER_ENABLED`, or `python -m app.worker`)?

## Known limitations
- **Real video and face providers were NOT exercised** (no `VIDEO_PROVIDER_API_KEY` / `FACE_PROVIDER_API_KEY` available). The fal.ai adapter is tested against a faithful mock and a local stand-in; the real service's model input names, limits and pricing must be verified with your key.
- **20/30 second videos need a model that supports them** (the default Kling model makes 10 s at most). No clip stitching.
- **Face replacement works on images only** with the built-in provider; references are described in the prompt, not sent as images (only the image-to-video source is). Character consistency is not guaranteed.
- **Avatars and interactive avatars are not built** (Phase 5); only the development simulator (fake, labelled) runs those jobs.
- The **real** Groq/Gemini, Hugging Face and Google TTS endpoints were **not exercised** (no keys available here). The adapters are unit-tested against mocked HTTP and were run end
  to end against local stand-in servers; model names/URLs may need adjusting via `LLM_MODEL`, `MUSIC_MODEL`, `MUSIC_BASE_URL`. Free Hugging Face models can be slow on first use (cold start) or temporarily unavailable.
- Hindi/Telugu text quality depends on the LLM; TTS Telugu voices are Standard (not Neural).
- API keys are environment-only (no in-app key entry); keys are shared by the refinement and text-generation features.
- A single LLM call bounds script length (see above). Scene detection depends on the `SCENE nn` heading format.
- Face Replacement and reference images require choosing a project (uploads are stored in project references).
- Job progress is stage-based; percentages appear only if a provider reports them. Polling (4–6 s while active), no websockets. No browser push notifications yet.
- Daily-limit checks are not atomic across processes (fine for one instance). Rate limiting is in-memory per process.
- Supabase/Google login is still untested without credentials. Real SMTP delivery and real Google sign-in have only been tested with mocks.
- Frontend has no automated component tests (type-check + build + manual browser verification).


## Phase 5 — subscriptions, Razorpay payments, movie scenes and assembly

**Plans** (`backend/app/plans.py`, prices from `.env`): **Trailer** (free), **Indie** (₹199/month), **Blockbuster** (₹499/month).
Each paid tier allows 4x the previous one's generations per day. One generated clip is at most 30 s on every plan.
Every new user starts on Trailer, and users who existed before are moved to Trailer by the migration (nobody is charged or locked out).

**Payments (Razorpay, behind `app/payments/PaymentProvider`).**
1. `POST /api/subscription/checkout {plan_id}` creates an order at the **server's** price and returns the public key id.
2. The browser opens Razorpay Checkout. When it finishes, `POST /api/subscription/verify` sends the three Razorpay values.
3. The server checks the HMAC signature with `RAZORPAY_KEY_SECRET`. Only then is the plan activated, once (repeats and webhooks are harmless).
4. `POST /api/payments/razorpay/webhook` (signature-checked with `RAZORPAY_WEBHOOK_SECRET`) covers the case where the browser closes early.
Without keys the app works and upgrades answer "payments aren't available yet". Test mode: use Razorpay test keys and its test cards.
Secrets live only in the server environment. The response to the browser contains the public key id and nothing else.

**Movie.** Project → **Movie** tab: scenes (number, title, description, script, characters, visual prompt, length), **Generate Video** per scene
(one normal video generation from your allowance), then **Assemble Movie**, which joins the scenes' clips in order with the bundled FFmpeg as a
background job (Queued → Preparing → Assembling → Finalizing → Completed). Missing clips block assembly with "Scene N has not been generated yet."
Assembling uses no video allowance. The final movie may be longer than 30 s; only individual clips are capped.

**Production notes.**
- Render (backend): root directory `backend`; build `pip install -r requirements.txt`; start
  `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` (note `:app`: `uvicorn app.main` alone fails). Set `APP_ENV=production`
  and a long random `AUTH_SECRET_KEY`. The dev simulator is always off in production, and storage folders are created at startup.
- Netlify (frontend): `VITE_API_URL=https://dreamcast-ai-backend.onrender.com`; `frontend/public/_redirects` keeps page refreshes from 404-ing.
- `GET /health` returns `{"status":"ok"}`. CORS allows exact origins only (Netlify site always included).
- Render free disks are ephemeral: SQLite data and media vanish on redeploy. Use a persistent disk or hosted Postgres before real users.


## Images and the Library

**Image generation** (Create → Image) uses fal.ai text-to-image (default `fal-ai/flux/schnell`, the cheapest model) behind the same provider
interface as video. Set `VIDEO_PROVIDER_API_KEY` (or a separate `IMAGE_PROVIDER_API_KEY`) on the server; without a key an image request fails with a clear
message, costs nothing and creates no fake image. Each image is stored as a real asset (file, thumbnail, width/height, prompt, model, provider job id, owner, project).
Results created without choosing a project are saved in an automatic **Quick creations** project so a generated file is never lost. Trailer allows 8 images a day (Indie 32, Blockbuster 128).

**Library** (sidebar) lists everything you generated across all projects: images, videos, assembled movies, stories, scripts, music and voice, each with preview,
title, date, status, project, duration and prompt, filterable and newest first. **History** still shows the jobs (including running and failed ones, with Retry).
Both read from the database, so nothing is lost on logout or refresh (on Render's free disk they are lost on redeploy, see the checklist below).

## Production Deployment Checklist

Backend on **Render**, frontend on **Netlify**. Do the steps in this order: Render, then Netlify, then Razorpay.

**A. Netlify (frontend)** — site: https://dreamcaastai.netlify.app
- Base directory `frontend`, build command `npm run build`, publish directory `dist` (if your site has no base directory set: `frontend/dist`).
- Environment variable `VITE_API_URL` = `https://dreamcast-ai-backend.onrender.com` (no trailing slash).
- Vite reads it **while building**, so after changing it use *Deploys → Trigger deploy → Clear cache and deploy site*.
- `frontend/public/_redirects` makes refreshes on pages like `/plans` or `/projects/...` work. Keep it.

**B. Render (backend)** — https://dreamcast-ai-backend.onrender.com
- Root directory `backend`. Build command `pip install -r requirements.txt`.
- Start command: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` (it must end in `app.main:app`).
- Environment variables (names only, set the values in Render, never in Git):
  `APP_ENV=production`, `AUTH_SECRET_KEY` (**required**, at least 32 random characters, e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`; the backend refuses to start without it),
  `CORS_ORIGINS=https://dreamcaastai.netlify.app`, `FRONTEND_URL=https://dreamcaastai.netlify.app`, `ADMIN_EMAILS` (your email), and later the Razorpay and fal.ai values below.
  Leave `DATABASE_URL` empty for the SQLite default. `ENABLE_DEV_SIMULATOR` is always off in production. `WORKER_ENABLED=true` (default) runs jobs inside the web process.
- Check it works: open `/health`; it should show `{"status":"ok"}`.

**C. Razorpay (payments)** — use **TEST** keys first.
- Render variables: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. Only the key id is ever sent to the browser; **never put the secret key in the frontend or in a `VITE_` variable**.
- In the Razorpay dashboard (Test mode) add the webhook URL `https://dreamcast-ai-backend.onrender.com/api/payments/razorpay/webhook`, tick the events `payment.captured`, `order.paid` and `payment.failed`, and type the same secret you set as `RAZORPAY_WEBHOOK_SECRET`.
- Prices come from the server (`PLAN_INDIE_PRICE_INR`, `PLAN_BLOCKBUSTER_PRICE_INR`). One payment = one 30-day plan; there is no auto-renewal, refund, invoice or tax handling.

**D. fal.ai (video)**
- Render variable `VIDEO_PROVIDER_API_KEY` (server-side only, never `VITE_`). Without it the app works and video requests say the provider isn't configured; no fake video is made and nothing is charged.
- Each generated clip costs money at fal.ai; keep the plan limits low while testing. The same key also powers image generation (`IMAGE_PROVIDER_API_KEY` is optional).

**E. Database and media storage (permanent data)**
- **PostgreSQL holds all records** (users, verification codes, projects, scenes, jobs, assets, usage, subscriptions, payments, movie metadata) and **S3-compatible object storage holds all files** (images, videos, thumbnails, movies). Render's own disk is never used for permanent data, so redeploys and restarts lose nothing.
- `DATABASE_URL` = your PostgreSQL URL. `alembic upgrade head` (part of the start command) creates and updates every table; never edit tables by hand.
- `STORAGE_BACKEND=s3` plus `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL` (empty for AWS), `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. Create the bucket **private** (no public access, no public ACLs).
  Cloudflare R2 (free allowance, no egress fees): create a bucket, create an *R2 API token* with *Object Read & Write* on that bucket, then `S3_REGION=auto` and `S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com`.
  AWS S3: create a private bucket and an IAM user limited to that bucket (`s3:GetObject`, `PutObject`, `DeleteObject`, `ListBucket`); leave `S3_ENDPOINT_URL` empty and set `S3_REGION` (for example `ap-south-1`).
- **No bucket CORS is needed**: the browser never talks to the bucket. It asks the backend, which checks you own the file and then streams it (with Range support for video seeking) or sends you a short-lived signed download link.
- Admin → System shows whether the database is PostgreSQL and whether the bucket is reachable. Local development keeps working with SQLite and a local `storage/` folder (the defaults).

**F. Email (sign-up codes and password reset)** and **G. Google sign-in**
- Render variables: `SMTP_HOST`, `SMTP_PORT` (587 or 465), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`. Server-side only.
  **Render's free tier blocks outgoing SMTP ports (25/465/587).** If sign-up says "We couldn't send the email right now", use the HTTPS provider instead:
  create a free Brevo account, verify your sender address, make an API key, then set `EMAIL_PROVIDER=brevo`, `BREVO_API_KEY`, and `SMTP_FROM_EMAIL` / `SMTP_FROM_NAME` (the verified sender).
  Gmail: turn on 2-step verification, create an *App password*, use host `smtp.gmail.com`, port 587, your Gmail address as username and the app password as password.
- `REQUIRE_EMAIL_VERIFICATION=true` (default): new accounts must enter the emailed 6-digit code (10 minutes, single use, 5 wrong tries) before signing in.
  **Registration needs SMTP**: without it sign-up answers "email isn't set up". Accounts created before this feature are treated as verified. Set it to `false` only as a stop-gap.
- Password reset sends a single-use link (30 minutes) by email. Reset links and codes are never written to logs or returned by the API.
- Google: in Google Cloud Console create an *OAuth client ID* (type *Web application*). Add the authorized redirect URI
  `https://dreamcast-ai-backend.onrender.com/api/auth/google/callback` (it must match `GOOGLE_REDIRECT_URI` exactly; add `http://localhost:8000/api/auth/google/callback` for local testing).
  Render variables: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`. Never put the secret in the frontend. Also set `FRONTEND_URL` (where Google sign-in returns to).
- A Google sign-in with an email that already has a password account links to it; if that account's email was never verified, its password is removed first.


## First real tests after deploying (do these in order)

1. **Deploy the latest code** (Render backend, then Netlify frontend). Open `https://dreamcast-ai-backend.onrender.com/health` (the first request after a quiet period can take about a minute on the free tier).
2. **Check the server's configuration.** Sign in with an admin account (an email listed in `ADMIN_EMAILS`) and open **Admin → System**. It lists, with yes/no only and never any secret, whether email, Google, Razorpay (and whether it is *test* or *live* mode), the webhook secret, fal.ai, the database and storage are set up. Fix everything marked *Needs attention* that you plan to use. It also tells you plainly that SQLite and media on Render's free disk are temporary.
3. **Email:** register a new account with a real inbox, enter the 6-digit code, sign in, then use *Forgot password* and open the emailed link.
4. **Google:** *Continue with Google* with a Google account that has no DreamCast account yet, then again with one whose email already has a password account (they link).
5. **Razorpay (TEST mode keys only):** on *Plans* press *Upgrade* on Indie, pay with Razorpay's test card, and confirm the plan changes. Register the webhook (see C above) and check the Razorpay dashboard shows deliveries answered 200.
6. **fal.ai:** generate **one** image and **one** 10-second video (each costs a little real money), open them in *Library*, play the video.
7. **Movie:** create a project with two scenes, give each a clip, press *Assemble Movie*, play and download it.
8. Log out, log in again, and confirm everything is still there. (On Render's free disk it disappears after a redeploy or restart; that is expected, see E.)

