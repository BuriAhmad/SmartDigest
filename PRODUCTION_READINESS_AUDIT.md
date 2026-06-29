# SmartDigest Production Readiness Audit

**Audit date:** 2026-06-29

**Repository:** SmartDigest

**Target platform:** Google Cloud Run, Cloud Run worker pools, Secret Manager, Artifact Registry

**Target region:** `us-east1`

**Current verdict:** **Not ready for untrusted public production traffic**

This document replaces the previous audit. It is a current, code-backed launch assessment rather than a historical checklist. It covers the web application, authentication, API authorization, outbound scraping, database access, Redis/ARQ processing, retrieval and LLM behavior, email delivery, container packaging, Google Cloud deployment, observability, and tests.

No secret values are recorded here. Provider state is a point-in-time snapshot and should be rechecked before every launch.

## 1. Executive Summary

SmartDigest has a credible production foundation:

- The FastAPI web process and ARQ worker have separate runtime roles.
- Production configuration is validated per role at startup.
- Database migrations and source seeding are separated into a release job.
- Secret Manager mappings and least-privilege runtime service accounts are represented in the deployment script.
- Redis TLS, Neon/asyncpg TLS handling, explicit ARQ concurrency, retry behavior, duplicate-run protection, advisory locks, and structured logs are implemented.
- The retrieval pipeline has meaningful unit coverage, including moving-window behavior, failure states, retries, reranking, LLM relevance, and summarization.

The system should not receive untrusted public users yet. The most important blockers are not generic cloud setup tasks; they are application security boundaries:

1. User-supplied source URLs can drive server-side requests without a safe allowlist or private-network protection.
2. Job status lookup does not verify that the digest belongs to the authenticated user.
3. Global pipeline metrics and recent error details are exposed to every authenticated user.
4. Cookie-authenticated state-changing routes do not have a complete CSRF defense.
5. Untrusted feed, LLM, and user content reaches HTML-building paths without consistent escaping.
6. The current Cloud Run dependency/image path does not contain the semantic and reranker libraries or model weights, while those stages are part of the intended product behavior.
7. The Google Cloud runtime has not been deployed or smoke-tested yet.

The recommended release posture is:

- **Now:** local development and controlled internal validation.
- **After P0 fixes:** private staging on Google Cloud with real managed services.
- **After staging acceptance and P1 controls:** limited public launch with alerts and rollback procedures.

## 2. Status Definitions

| Status | Meaning |
|---|---|
| READY | Implemented and verified at the level described in this audit. |
| PARTIAL | Useful controls exist, but important production work remains. |
| BLOCKED | Must be resolved before untrusted public traffic. |
| EXTERNAL | Requires an operator action in Google Cloud or another provider. |
| DEFERRED | Acceptable to postpone for a limited launch if the risk is explicitly accepted. |

## 3. Architecture Under Review

```text
Browser
  -> Cloud Run FastAPI service (public HTTPS)
       -> Firebase token verification
       -> signed SmartDigest session cookie
       -> Neon PostgreSQL
       -> Upstash Redis / ARQ queue

Cloud Run worker pool (not public)
  -> scheduled and manually queued digest jobs
  -> external RSS/article fetching
  -> BM25 + semantic retrieval + reranking
  -> Gemini relevance and summarization
  -> Resend email delivery
  -> Neon PostgreSQL + Redis

Cloud Run release job (run during deployment)
  -> Alembic migrations
  -> curated source seeding
  -> Neon PostgreSQL
```

These processes may use one image, but they should remain separate deployments. The web process must listen on `$PORT`; the ARQ worker is a long-running queue consumer and should not be exposed as an HTTP service. Separate deployments also allow independent IAM, secrets, scaling, memory, and failure isolation.

## 4. Readiness Scorecard

| Area | Status | Current assessment |
|---|---|---|
| Runtime role separation | READY | `web`, `worker`, and `release` roles are represented and validated. |
| Secret inventory | READY | Seven required Secret Manager entries exist with enabled version 1. |
| Least-privilege runtime IAM | PARTIAL | Deployment script defines it; service accounts and bindings do not yet exist in Google Cloud. |
| Database connectivity | READY | Neon TLS conversion, smoke tooling, migrations, and seeding exist. |
| Redis/ARQ connectivity | READY | `rediss://` is supported and worker concurrency is explicit. |
| Authentication | PARTIAL | Firebase exchange and secure production cookie settings exist; CSRF and auth abuse controls remain. |
| API authorization | BLOCKED | Job status and global metrics cross tenant boundaries. |
| Outbound fetch security | BLOCKED | Arbitrary URLs, redirects, private addresses, and response size are not safely constrained. |
| Retrieval semantics | PARTIAL | Core behavior is tested; the latest final-selection changes are still uncommitted at audit time. |
| Semantic/reranker deployment | BLOCKED | Current Cloud Run dependency path excludes the required ML runtime and weights. |
| Email safety | BLOCKED | Untrusted values are interpolated into HTML without a single escaping boundary. |
| Observability | PARTIAL | Structured logs and pipeline events exist; alerts, queue depth, request correlation, and accurate global metrics do not. |
| Container design | PARTIAL | Multi-stage/non-root/role entrypoints are sound; ML packaging and a verified target-platform build remain. |
| Cloud infrastructure | EXTERNAL | Artifact repository exists, but Cloud Run/Cloud Build APIs and deployed runtimes were absent at the snapshot time. |
| Automated tests | PARTIAL | 61 targeted tests pass; security, container, and managed-service integration coverage is missing. |
| CI/CD and supply chain | PARTIAL | Artifact scanning is active, but no CI gates, dependency update automation, or image promotion process exists. |
| Data lifecycle/privacy | PARTIAL | Core records are modeled; retention, deletion, export, and restore policy are undefined. |

## 5. P0 Public-Launch Blockers

### P0.1 Restrict and harden outbound source fetching

**Risk:** Server-side request forgery, cloud metadata access, internal-network probing, unbounded fan-out, and memory exhaustion.

**Evidence:**

- Briefing create/update accepts source URL strings without enforcing membership in the active curated-source table.
- The worker passes those stored URLs to the fetcher.
- Unknown sources fall back to the generic RSS scraper.
- HTTP clients follow redirects and do not reject loopback, private, link-local, or metadata destinations.
- Feed and article responses are converted to text without an explicit byte limit.
- There is no enforced maximum source count per briefing or feed-entry processing limit.

Relevant paths: `app/api/briefings.py`, `app/schemas/briefing.py`, `app/services/fetcher.py`, `app/services/scrapers/`.

**Required remediation:**

- Store stable curated-source identifiers rather than accepting arbitrary URL authority from clients.
- Verify every selected source is active and present in `CuratedSource` during create and update.
- Enforce a small maximum source count per briefing.
- Add defense-in-depth URL validation in the network layer: HTTPS-only where possible, DNS resolution checks, blocked private/loopback/link-local/multicast/reserved ranges, blocked cloud metadata hosts, and redirect revalidation on every hop.
- Limit redirect count, response bytes, feed entries, article fetches, and accepted content types.
- Apply bounded concurrency and explicit retry/backoff rules to source fetching.

**Acceptance criteria:** Automated tests prove that unknown sources, encoded IP forms, IPv6 local addresses, DNS-to-private resolution, and redirects to blocked destinations are rejected before a request is sent. Oversized responses and excessive source lists are terminated safely.

### P0.2 Enforce digest ownership in job status

**Risk:** An authenticated user can inspect another user's digest state, event count, timing, queue state, and recent error text by guessing a digest job ID.

**Evidence:** `app/api/jobs.py` loads a `Digest` by ID but does not join through `Briefing.user_id` before returning details.

**Required remediation:** Resolve the digest through its briefing and authenticated user, returning `404` for inaccessible jobs. Do not distinguish missing from unauthorized resources.

**Acceptance criteria:** Integration tests with two users prove that each user can read only their own job IDs.

### P0.3 Restrict operational metrics and error details

**Risk:** Every authenticated user can view global delivery/failure counts and recent system error information. This crosses tenant boundaries and leaks internal provider/runtime behavior.

**Evidence:** Pipeline metric API and dashboard routes require authentication but have no administrator role or user scope.

Relevant paths: `app/api/metrics.py`, `app/routes/metrics.py`, `app/routes/dashboard.py`, `app/services/metrics.py`.

**Required remediation:** Choose one of these models before launch:

- Remove product-facing global metrics and rely on Cloud Monitoring.
- Add a real administrator authorization claim and protect every metrics route.
- Scope metrics to the authenticated user's briefings and redact provider error text.

**Acceptance criteria:** A normal user cannot access global counts, another user's failures, or raw internal error messages.

### P0.4 Add a complete CSRF boundary

**Risk:** The application authenticates through cookies, but POST/PATCH/DELETE routes do not consistently require a CSRF token or validate request origin.

`HttpOnly`, `Secure`, and `SameSite=Lax` are valuable controls, but `SameSite` alone is not the application's CSRF policy.

**Required remediation:**

- Add synchronizer tokens or a signed double-submit token for browser mutations.
- Validate `Origin`/`Referer` against the production origin as defense in depth.
- Cover logout, briefing mutations, and manual digest triggering.
- Keep JSON content-type checks and reject unexpected form/simple requests where appropriate.

**Acceptance criteria:** Cross-origin mutation attempts fail while normal HTMX/browser flows and Firebase session creation continue to work.

### P0.5 Escape untrusted HTML and validate links

**Risk:** Feed titles/content, model output, source names, article URLs, and user-entered tags can reach browser or email HTML sinks. Malicious content could create markup, misleading links, or script-capable browser content.

**Evidence:**

- Digest email markup is assembled with direct string interpolation.
- Some frontend rendering uses `innerHTML` with dynamic values.
- Third-party scripts and modules are loaded from CDNs without a defined content security policy.

Relevant paths: `app/services/scheduler.py`, `app/templates/`, `app/static/`.

**Required remediation:**

- Escape every text value at the email HTML boundary and allow only validated `http`/`https` links.
- Replace dynamic `innerHTML` construction with DOM text nodes or a trusted templating path.
- Add tests containing HTML, quotes, event attributes, `javascript:` links, and malicious model output.

**Acceptance criteria:** Untrusted strings render as text in web and email outputs, and unsafe URL schemes are never emitted as links.

### P0.6 Build a production image that actually supports semantic retrieval and reranking

**Risk:** Product behavior and deployed behavior diverge. The current Cloud Run dependency path excludes `sentence-transformers`/`transformers`, and the worker environment disables semantic retrieval and reranking.

The local model caches are approximately 87 MB for the semantic model and 265 MB for the reranker, before framework/runtime dependencies. Local caches are excluded from the Docker context. The semantic loader is local-cache-only and fails soft; the required reranker can fail the pipeline when enabled but unavailable.

Relevant paths: `Dockerfile`, `requirements.cloudrun.txt`, `requirements.txt`, `.dockerignore`, `deploy/worker.env.yaml`, `app/services/filters/semantic.py`, `app/services/filters/reranker.py`, `worker.py`.

**Required remediation if these stages are launch requirements:**

- Include compatible pinned ML dependencies in the production worker image.
- Download and pin model revisions during image build, not at job execution time.
- Store weights at deterministic image paths and run both loaders in local-only mode.
- Warm both models during ARQ worker startup and fail startup if a required model cannot load.
- Re-enable semantic and reranker flags in the worker environment.
- Size memory/startup limits from measured container behavior.
- Build for Cloud Run's supported Linux architecture. On this Apple Silicon host, prefer Cloud Build or explicitly build `linux/amd64`.

**Acceptance criteria:** A clean container with no host cache and no model-download network access starts the worker, warms both models, processes a representative digest, and reports measured cold-start, peak memory, and job duration.

If a lexical-only launch is intentionally accepted, rename/document it as a temporary reduced-quality release and do not imply semantic/reranker behavior is active.

### P0.7 Complete deployment and managed-service smoke testing

**Risk:** The deployment scripts are plausible but the actual Cloud Run service, worker pool, IAM behavior, release job, networking, and provider integrations remain unproven.

At the 2026-06-29 read-only snapshot:

- Project `smartdigest-500718` was active and billing-enabled.
- Artifact Registry repository `smartdigest` existed in `us-east1` and contained no pushed images.
- Secret Manager was enabled and all seven expected secrets had enabled version 1.
- Cloud Run Admin and Cloud Build APIs were disabled/not yet used.
- No dedicated runtime service accounts or per-secret IAM bindings existed.
- No Cloud Run service, worker pool, or release job had been created.

`scripts/deploy_gcloud.sh` is designed to perform these mutations later; this audit did not run it.

**Acceptance criteria:** In a private staging deployment, the release job succeeds, `/healthz` responds, authenticated web flows work, one manual and one scheduled digest complete, Redis queueing survives a worker restart, email reaches a controlled inbox, logs contain no secrets, and rollback to the previous image is exercised.

## 6. P1 Controls Before Broad Public Use

### P1.1 Production HTTP hardening

- Disable or protect `/docs`, `/redoc`, and `/openapi.json` in production.
- Add trusted-host validation for the final Cloud Run/custom domain.
- Add a content security policy compatible with Firebase and HTMX, plus `X-Content-Type-Options`, `Referrer-Policy`, frame restrictions, and a deliberate HSTS policy at the public domain.
- Pin or self-host frontend dependencies instead of relying on floating CDN assets.
- Keep CORS closed unless a separate trusted frontend genuinely needs it.

### P1.2 Distributed rate and quota controls

The manual trigger currently has a 3/hour limit, but the default limiter state is process-local. Cloud Run scaling or restarts make that unsuitable as the only abuse/cost control.

- Move rate-limit state to Redis or enforce it at a gateway.
- Rate-limit session creation and mutation endpoints.
- Add per-user limits for active briefings, sources per briefing, manual runs, and concurrent jobs.
- Add provider-cost ceilings and alerts for LLM and email usage.

### P1.3 Observability and alerting

Structured production logs and persisted `pipeline_events` are a good base. Add:

- A request/correlation ID propagated from web enqueue through ARQ and pipeline events.
- Alerts for failed digest rate, no successful deliveries, worker restarts, queue age/depth, Redis errors, DB saturation, LLM failures, Resend failures, and release-job failure.
- Accurate job metrics. Current totals are derived mainly from terminal delivery events and can undercount failures that terminate in earlier stages.
- Token/model/cost metadata for LLM calls without logging prompts or article content.
- A runbook linking each alert to queries and corrective actions.

### P1.4 Database capacity and lifecycle

- Set and document SQLAlchemy pool size, overflow, checkout timeout, and Cloud Run instance assumptions against Neon's connection limit.
- Confirm provider backups and perform a restore drill.
- Define retention for article content, summaries, digest items, pipeline events, and error text.
- Implement account data export/deletion and decide how soft-deleted briefings are purged.
- Document privacy and acceptable-use policy before collecting public user data.

### P1.5 CI/CD and supply-chain controls

- Add CI for unit tests, compile checks, shell checks, migration checks, linting, and dependency auditing.
- Build the exact production image in CI and smoke each role from that image.
- Pin deployment by immutable image digest rather than a mutable tag.
- Gate promotion on Artifact Registry vulnerability findings.
- Add automated dependency update review and a rotation process for secrets and credentials.

## 7. What Is Already Sound

### 7.1 Configuration and secrets

`app/config.py` is the environment contract and validates production roles before runtime work begins.

- Production database URLs must be non-local PostgreSQL.
- Production Redis URLs must use an accepted Redis scheme, be non-local, and use TLS.
- The web role requires a strong JWT secret and structurally valid Firebase service-account JSON.
- The worker role requires LLM and Resend credentials.
- `ARQ_MAX_JOBS` must be at least 1 and is explicitly set to 2 in worker deployment configuration.
- Secret values are not embedded in tracked deployment files.

Secret access should remain role-specific:

| Secret | Web | Worker | Release |
|---|---:|---:|---:|
| `DATABASE_URL` | Yes | Yes | Yes |
| `REDIS_URL` | Yes | Yes | No |
| `LLM_API_KEY` | No | Yes | No |
| `RESEND_API_KEY` | No | Yes | No |
| `RESEND_FROM_EMAIL` | No | Yes | No |
| `JWT_SECRET` | Yes | No | No |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | No | No |

`RESEND_FROM_EMAIL` is configuration rather than a credential, but keeping it in Secret Manager is operationally harmless. Secret rotation requires creating a new version, updating `SECRET_VERSION`, and deploying a new revision because the deployment script intentionally pins a version.

### 7.2 Database and release workflow

- Neon `sslmode=require` is translated for asyncpg instead of being passed as an unsupported query option.
- The same URL normalization is used by application and migration paths.
- SQLAlchemy uses `pool_pre_ping`.
- Alembic migrations and source seeding run as a release task rather than during every web/worker startup.
- Release, web, and worker roles can receive different secrets and permissions.

### 7.3 Queue and worker behavior

- The ARQ worker has an explicit low concurrency of 2 rather than the default 10.
- Job retries, stage retry deferral, timeout configuration, and terminal states are represented.
- PostgreSQL advisory locks reduce duplicate simultaneous processing.
- Deterministic job IDs and stale-job recovery reduce duplicate scheduling and stuck records.
- Scheduled catch-up and manual triggers converge on the same queue/pipeline path.
- The worker pool is configured as one instance initially, which is conservative for DB, Redis, LLM, email, and model memory pressure.

Concurrency 2 means one worker process can execute at most two digest jobs at the same time. Each job can itself perform multiple outbound operations. Keeping this low protects shared sockets, DB connections, provider quotas, memory, and CPU while real production measurements are collected.

### 7.4 Retrieval and delivery semantics

- The moving window is based on the last delivered digest rather than the last attempted digest.
- Quiet windows become `skipped`, preserving the window for later content.
- Publication-date provenance and recovery behavior are modeled and tested.
- BM25 and semantic candidates are merged and capped before expensive stages.
- Reranker and LLM failures have explicit behavior rather than silently becoming successful empty digests.
- The current worktree changes LLM relevance into the final PASS/FAIL selection stage and makes summarization one-to-one transformation. This is the correct conceptual boundary, but it is uncommitted at audit time and must be reviewed as part of the release commit.
- Delivery email is checked against the authenticated/local user identity before processing.

### 7.5 Container and deployment shape

- The Dockerfile uses Python 3.11, a multi-stage build, dependency verification, a non-root runtime user, and Uvicorn bound to `$PORT`.
- One image can serve `web`, `worker`, and `release` roles without combining them into one running process.
- `.dockerignore` excludes local environments, caches, Git metadata, tests, docs, local databases, and secret env files.
- The deployment script creates role-specific service accounts, grants secret access per role, runs the release job, and then deploys web and worker runtimes.

The unresolved container issue is model completeness, not the overall role design.

## 8. Additional Risks and Caveats

### 8.1 Authentication/session behavior

- Session cookies are `HttpOnly`, production-`Secure`, and `SameSite=Lax` with a 72-hour lifetime.
- Firebase ID tokens are exchanged for local signed JWT cookies.
- There is no application-level role model for administrators.
- Session revocation is limited by local JWT lifetime unless additional checks are introduced.
- Authentication/session creation needs rate limiting and dedicated tests for invalid, expired, revoked, and mismatched Firebase identities.

### 8.2 Liveness versus readiness

`/healthz` is intentionally lightweight and public, which is suitable for process liveness. It does not prove DB, Redis, model, LLM, or Resend availability.

Do not turn the Cloud Run liveness probe into a dependency fan-out. Instead, add an authenticated operational readiness diagnostic or synthetic monitor that tests dependencies independently.

### 8.3 LLM and prompt safety

- Article text is untrusted external data and can contain prompt-injection instructions.
- JSON schemas and explicit relevance behavior reduce format drift but do not make external text trustworthy.
- Prompts should consistently delimit article data, state that it is non-instructional, and avoid exposing secrets or privileged tools to the model.
- Model output must always be treated as untrusted text at browser/email boundaries.
- Retries and fallback models improve resilience but can multiply cost; record attempts and token usage.

### 8.4 Provider and regional dependency risk

The application depends on Google Cloud, Firebase, Neon, Upstash, Gemini, external publishers, and Resend. A digest can fail even when the container is healthy.

- Document provider status pages and support contacts.
- Define retryable versus terminal errors per provider.
- Measure cross-region latency for Neon and Upstash relative to `us-east1`.
- Avoid retry storms when a provider has a broad outage.

### 8.5 Human access

The deployment account had project Owner access at audit time. That is convenient for bootstrap but broad for routine operation.

- Require MFA on privileged accounts.
- Use a narrower deployment role or CI identity after bootstrap.
- Keep runtime service accounts separate and without service-account keys.
- Review project IAM and Secret Manager access regularly.

### 8.6 Stale model and schema artifacts

`app/models/api_key.py` appears to be a stale model not imported into active metadata, while historical migrations removed the table. It is not a launch blocker, but should be removed or restored deliberately to prevent future schema confusion.

## 9. Test and Verification Evidence

Verification run on the current worktree on 2026-06-29:

| Check | Result |
|---|---|
| `python -m unittest tests.test_production_readiness tests.test_retrieval_pipeline` | PASS, 61 tests |
| `python -m compileall -q app worker.py tests` | PASS |
| `python -m pip check` | PASS, no broken requirements |
| Shell syntax checks for deployment scripts | PASS |
| `git diff --check` | PASS |

The passing tests are primarily isolated/unit tests with mocks. They do not prove:

- Tenant isolation for jobs or metrics.
- SSRF/private-network defenses.
- CSRF behavior.
- Safe HTML/email rendering.
- A clean production container with semantic and reranker models.
- Migration from an empty managed database using the built release image.
- Real Firebase, Gemini, Resend, Neon, Upstash, Cloud Run, and worker-pool integration.
- Load, memory, queue-backlog, or provider-rate behavior.

Recorded repository smoke tools exist for DB and Redis. Those should be rerun from the deployed revision or an equivalent private execution environment; local success alone does not validate Cloud IAM or runtime networking.

## 10. Required Deployment Topology

| Runtime | Exposure | Scaling baseline | Required dependencies |
|---|---|---|---|
| FastAPI web service | Public HTTPS | Max 3 instances, HTTP concurrency 20 initially | DB, Redis, Firebase, JWT |
| ARQ worker pool | No public ingress | 1 instance, `ARQ_MAX_JOBS=2` initially | DB, Redis, LLM, Resend, packaged models |
| Release job | Operator/CI invoked | 1 task per deployment | DB only |

Initial values are conservative baselines, not permanent tuning. Raise them only after measuring:

- DB pool usage and Neon connection limits.
- Redis connection counts and queue age.
- Worker memory with both models loaded and two concurrent jobs.
- LLM/Resend rate limits and cost.
- P50/P95/P99 job duration and failure rate.

Do not run the web server and ARQ worker in the same Cloud Run service instance. Doing so couples independent lifecycles, exposes worker resources to HTTP autoscaling, complicates graceful shutdown, duplicates schedulers as web instances scale, and prevents least-privilege secret/IAM separation.

## 11. Launch Plan and Exit Criteria

### Phase A: Close repository blockers

- Implement source allowlisting and network-layer SSRF/resource limits.
- Fix job ownership and metrics authorization.
- Add CSRF controls and untrusted HTML escaping.
- Finalize and commit the LLM final-selection/summarization behavior.
- Produce the model-complete worker image and clean-container model tests.
- Add focused regression tests for every P0 item.

**Exit:** All P0 code acceptance criteria pass locally and in the production image.

### Phase B: Private Google Cloud staging

- Review current project/account and run the deployment script intentionally.
- Enable required APIs and create role-specific service accounts/IAM.
- Build and push an immutable target-platform image.
- Run release migration/seeding job.
- Deploy private/limited web access and one worker-pool instance.
- Execute managed-service smoke tests and one controlled end-to-end digest.

**Exit:** The full path works from browser authentication to delivered email with models enabled and no secrets in logs.

### Phase C: Reliability and security acceptance

- Configure alerts, dashboards, correlation IDs, queue monitoring, and provider-cost monitoring.
- Run tenant-isolation, CSRF, SSRF, container, migration, restart, and rollback tests.
- Load-test expected launch traffic with realistic source and model workloads.
- Confirm database connection headroom, retention, backup, restore, and incident runbooks.

**Exit:** Alerts fire in a drill, rollback is proven, and no known BLOCKED item remains.

### Phase D: Limited public release

- Start with low Cloud Run and worker maximums.
- Monitor queue age, failed stages, model memory, provider errors, DB connections, and spend daily.
- Increase concurrency/instances only from measured evidence.

## 12. Rollback and Incident Minimums

Before public traffic, document and rehearse:

- Roll back web and worker independently to the previous image digest.
- Pause the worker pool without taking down account/briefing access.
- Disable manual triggering during a cost or provider incident.
- Rotate each secret and deploy the corresponding role.
- Recover or requeue stale digests without duplicate delivery.
- Handle a failed migration using a forward fix; do not assume every schema change is safely reversible.
- Query logs and `pipeline_events` by digest and correlation ID.
- Notify users when delivery is delayed or content quality is intentionally degraded.

## 13. Definition of Production Ready

SmartDigest is ready for public production only when all of the following are true:

- No P0 item in this audit remains open.
- The deployed worker runs semantic retrieval and reranking if those are advertised product behavior.
- Tenant isolation is tested across every user-owned resource and operational endpoint.
- Outbound fetches cannot reach internal/private destinations and have bounded resource use.
- Browser and email rendering treat all external/model/user data as untrusted.
- The release job, web service, and worker pool pass a staging end-to-end test using the exact promoted image digest.
- Alerts, rollback, backup/restore, secret rotation, and incident ownership are documented and exercised.
- Scaling limits are based on measured DB, Redis, model-memory, provider-rate, latency, and cost data.

Until then, a successful container build or a populated Secret Manager should be treated as deployment progress, not proof of production readiness.

## 14. Canonical Operational References

- `CLOUD_RUN_DEPLOYMENT.md`: operator commands and deployment walkthrough.
- `MY_DEPLOYMENT_PREP_CHECKLIST.md`: human pre-deployment checklist.
- `scripts/deploy_gcloud.sh`: mutating Google Cloud bootstrap/deploy flow.
- `scripts/upload_gcloud_secrets.sh`: local secret upload flow.
- `scripts/run_release_tasks.sh`: migrations and source seeding.
- `scripts/smoke_db.py` and `scripts/smoke_redis.py`: provider connectivity checks.
- `app/config.py`: authoritative runtime environment contract.
- `deploy/*.env.yaml`: non-secret role configuration.

This audit should hold the risk assessment and launch gates. It should not duplicate command-by-command deployment instructions or secret values.
