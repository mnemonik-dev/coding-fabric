# Code Research — blogger-linkedin

Date: 2026-06-11.
Scope: map the existing publishing pipeline so a LinkedIn personal-profile adapter
can be added upstream (`mnemonik-dev/blogger`) with the worker, queue, and
attestation layers untouched. Cycle 2 of the user-spec interview.

The repo has TWO source trees worth knowing about:
1. **Live upstream** at `/Users/syi/src/sessions/blogger/` — the real
   mnemonik-dev/blogger repo on this machine. This is where the LinkedIn
   adapter MUST live (per the feature brief — upstream package, not a local
   shim) and what the Ansible role re-clones at the pinned SHA on deploy.
2. **Installed snapshot** at `fabric/content-publisher/.venv/lib/python3.13/
   site-packages/mnemonik_blogger/` — an OLDER copy (v0.1.0, no
   `run_campaign_from_article`, no `content/ingest.py`, no `quality.py`).
   The worker (`publish.py`) calls APIs that exist only in the live upstream.

Everything below cites the **live upstream** at `/Users/syi/src/sessions/blogger/`
unless noted otherwise.

---

## File map

### Upstream `mnemonik-dev/blogger` (where the adapter lands)

| File | Role | Key symbols |
|------|------|-------------|
| `src/mnemonik_blogger/publish/base.py` | Publisher Protocol + `with_retry` + `dry_run_result` | `Publisher` (Protocol), `with_retry(fn, attempts=4, base_delay=2.0)`, `dry_run_result(post)` |
| `src/mnemonik_blogger/publish/__init__.py` | Registry. **One-liner switch** that returns a Publisher per `Platform`. | `build_publisher(platform, settings) -> Publisher` |
| `src/mnemonik_blogger/publish/telegram.py` | Reference adapter (in production). httpx + retry + thread reply-chain + escape. | `TelegramPublisher.publish(post) -> PublishResult` |
| `src/mnemonik_blogger/publish/discord.py` | Webhook OR bot-token branch — closest structural sibling for "two scope variants" pattern. | `DiscordPublisher.publish`, `_via_webhook`, `_via_bot` |
| `src/mnemonik_blogger/publish/twitter.py` | Optional-dep adapter (tweepy). Wraps a non-httpx client; catches `Exception` not just `httpx.HTTPError`. | `TwitterPublisher.publish` |
| `src/mnemonik_blogger/publish/farcaster.py` | Headers-based auth (`api_key`); thread via `parent` field. Closest to LinkedIn's `Authorization: Bearer` shape. | `FarcasterPublisher.publish` |
| `src/mnemonik_blogger/config.py` | `Platform` StrEnum + per-platform `*Config` (`BaseSettings`) + top-level `Settings`. SecretStr-based. | `Platform`, `TelegramConfig`, `Settings`, `Settings.configured_platforms()` |
| `src/mnemonik_blogger/models.py` | `SourcePost`, `RenderedPost`, `PublishResult`, `ProtocolFact`. | `RenderedPost.segments: list[str]`, `PublishResult(platform, ok, urls, ids, error, dry_run)` |
| `src/mnemonik_blogger/agent.py` | Orchestration entry points called by the worker. | `run_campaign(...)`, `run_campaign_from_article(...)`, `CampaignResult` |
| `src/mnemonik_blogger/content/formatters.py` | Per-platform render. **Dispatch is a `_RENDERERS: dict[Platform, fn]`** — adding LinkedIn means one new function + one map entry. | `_RENDERERS`, `render(platform, post)`, `render_telegram`, `render_discord`, etc. |
| `src/mnemonik_blogger/content/ingest.py` | Markdown + front-matter → `SourcePost`. Same `SourcePost` is then rendered per platform — no per-platform ingest forking. | `ingest_article(path) -> SourcePost` |
| `src/mnemonik_blogger/content/generate.py` | Brief-mode construction + guardrails. | `build_source_post(...)`, `enforce_guardrails(text)`, `render_all(post, platforms)` |
| `src/mnemonik_blogger/cli.py` | `mnemonik-blogger preview/post --platforms tg,x,...`. Reads `--platforms` aliases. | `_parse_platforms` (aliases dict — add `"li": Platform.LINKEDIN`) |
| `src/mnemonik_blogger/quality.py` | claude-blog scorer wrapper (gate before publish). Untouched by LinkedIn. | `score_article` |
| `tests/test_publishers.py` | The exact shape LinkedIn unit tests should mirror. `httpx.MockTransport` pattern. | `test_telegram_publish_sends_thread`, `test_unconfigured_publisher_fails_cleanly` |
| `tests/test_config.py` | Credential-detection tests; LinkedIn needs equivalents. | `test_telegram_detected_when_credentials_present`, `test_secret_not_in_repr` |
| `tests/test_agent.py` | `run_campaign` smoke with `list(Platform)`. Adding LinkedIn enlarges this. | `test_dry_run_campaign_does_not_publish` |
| `pyproject.toml` | Optional extras. `twitter = ["tweepy>=4.14"]` is the pattern for `linkedin = [...]` if a heavyweight dep is needed (httpx already in core, so likely none). | `[project.optional-dependencies]` |

### Worker (`fabric/content-publisher/`) — should not change much

| File | Role | Why it matters for LinkedIn |
|------|------|----------------------------|
| `src/content_publisher/publish.py` | The call site that invokes upstream. Hardcoded `platforms=[Platform.TELEGRAM]`. **Must change** to support multi-target. | `publish_step(job_id)`, `_run_campaign_from_article`, `_build_settings` |
| `src/content_publisher/render.py` | Preview render — also hardcoded to `Platform.TELEGRAM`. Affects what the operator sees in Telegram before approving a publish. | `render_preview(article_path)` |
| `src/content_publisher/env.py` | `restricted_env('blogger_inproc')` returns the full parent env (in-process call) — no allowlist needed for a new `LINKEDIN_*` var. | `restricted_env(kind)`, `EnvKind` Literal |
| `src/content_publisher/models.py` | `Job` shape. `post_url: str \| None` + `post_message_ids: list[int]`. **Single string url** — fan-out needs a decision (see Open Questions). | `Job`, `JobStatus` |
| `src/content_publisher/attest.py` | Attestation. Passes a single `post_url` to MCP — **does not include platform name**. | `attest_once`, `sign_memory_via_mcp` |
| `src/content_publisher/mcp_client.py` | MCP-stdio JSON-RPC. `mnemonic_sign_memory` arguments are `{content, content_sha256, post_url, score, prompt}` — no platform field. | `sign_memory(...)`, `_TOOL_NAME = "mnemonic_sign_memory"` |
| `src/content_publisher/main.py` | Boots queue-poll, deadline, cleanup loops. Untouched by LinkedIn unless platform-list-per-job is introduced. | `queue_poll_once`, `lifespan` |
| `tests/test_publish_handlers.py` | Worker test shape — mocks `_run_campaign_from_article` with a `_CR([_PR(...)])` double. LinkedIn fan-out tests reuse this. | `test_publish_uses_keyword_only_signature` |

### Infra (Ansible)

| File | Role |
|------|------|
| `infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2` | Renders `/etc/blogger.env` (mode 0600 root:root). All platform credentials flow through here. New `LINKEDIN_*` vars slot in alongside `TELEGRAM_*`. |
| `infrastructure/ansible/roles/content-publisher/defaults/main.yml` | Non-secret defaults + sops key skeleton. |
| `infrastructure/ansible/roles/content-publisher/tasks/main.yml` | Preflight asserts (token length etc.) + render + smoke. **Per-platform deploy-time smoke** (`getMe` + `getChatMember`) is in tasks lines 545–607 — LinkedIn would need an equivalent `userinfo` smoke. |
| `infrastructure/ansible/roles/content-publisher/templates/content-publisher.service.j2` | systemd unit. `EnvironmentFile=/etc/blogger.env` — new env vars are picked up automatically. |
| `infrastructure/secrets/secrets.sops.yml` | sops-encrypted. Existing keys at lines 90–100: `blogger_telegram_bot_token`, `blogger_telegram_channel`, `publish_min_score`, `publish_approval_timeout_min`, `publish_mode`, `blogger_repo_ref`, `publish_callback_hmac_secrets`, `publish_auto_mode_token`, `publish_auto_mode_token_confirm`, `blogger_prompts_topic_id`. **Naming convention**: `blogger_<platform>_<field>`. New keys would follow this. |
| `infrastructure/ansible/playbooks/deploy.yml:314–340` | Where the role is invoked with `vault_*` → role-var mapping. New LinkedIn vault vars map here. |

---

## Pattern catalog

### P1. Publisher Protocol (the LinkedIn class must satisfy this)

`publish/base.py:17-20`
```python
class Publisher(Protocol):
    platform: Platform
    def publish(self, post: RenderedPost) -> PublishResult: ...
```

- **Class attribute `platform`** (not instance) — set to `Platform.LINKEDIN`.
- `publish()` is **synchronous**, takes a `RenderedPost`, returns a
  `PublishResult`. Never raises on transport/permission errors — converts them
  into `PublishResult(ok=False, error=...)`. (See P5 for the exception
  contract.)
- The worker calls `publish()` via `run_campaign_from_article` → `_publish_post`
  (agent.py:96–109) inside `asyncio.create_subprocess`-spawned `claude` is not
  involved here; the publish call is **in-process** in the worker (the
  `blogger_inproc` env kind).

### P2. Constructor shape: `(config, *, dry_run=True)`

Every adapter:
```python
def __init__(self, config: XxxConfig, *, dry_run: bool = True) -> None:
    self.config = config
    self.dry_run = dry_run
```

Then in `publish()`:
```python
if self.dry_run:
    return dry_run_result(post)
if not self.config.configured:
    return PublishResult(post.platform, ok=False, error="<Name> not configured")
```

`dry_run_result` (base.py:47) returns `PublishResult(platform=post.platform,
ok=True, dry_run=True)`.

### P3. Registry switch — single-line addition

`publish/__init__.py:14-23`
```python
def build_publisher(platform: Platform, settings: Settings) -> Publisher:
    dry = settings.dry_run
    if platform is Platform.TELEGRAM:
        return TelegramPublisher(settings.telegram, dry_run=dry)
    if platform is Platform.DISCORD:
        return DiscordPublisher(settings.discord, dry_run=dry)
    if platform is Platform.TWITTER:
        return TwitterPublisher(settings.twitter, dry_run=dry)
    if platform is Platform.FARCASTER:
        return FarcasterPublisher(settings.farcaster, dry_run=dry)
    raise ValueError(f"Unknown platform: {platform}")
```

LinkedIn needs:
1. `Platform.LINKEDIN = "linkedin"` in `config.py`,
2. `LinkedInConfig` field on `Settings` (`linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig)`),
3. `LinkedInConfig.configured` joining `Settings.configured_platforms()`,
4. `LinkedInPublisher` class,
5. one new `if platform is Platform.LINKEDIN:` branch above.

### P4. SecretStr-based config + env prefix

`config.py:32-56` — every config has its own env prefix:

```python
def _cfg(prefix: str | None = None) -> SettingsConfigDict: ...
class TelegramConfig(_Base):
    model_config = _cfg("TELEGRAM_")
    bot_token: SecretStr | None = None
    channel: str | None = None
    @property
    def configured(self) -> bool:
        return self.bot_token is not None and bool(self.channel)
```

Existing prefixes: `TELEGRAM_`, `DISCORD_`, `X_` (yes, not `TWITTER_`),
`FARCASTER_`, `MNEMONIK_` (top-level). For LinkedIn the obvious analogue is
`LINKEDIN_`. Fields likely needed (mirrors LinkedIn Member API):
`access_token: SecretStr | None`, `refresh_token: SecretStr | None`,
`client_id: SecretStr | None`, `client_secret: SecretStr | None`,
`member_urn: str | None` (the `urn:li:person:<id>` author URN — the personal vs
company switch — see Open Question Q7).

`test_config.py:28-32` proves the `SecretStr` contract: secret values must
**not appear in `repr()`**. The LinkedIn config will need the same regression.

### P5. Error handling — the contract you must replicate

The Telegram + Farcaster pattern (httpx-based):

```python
try:
    with httpx.Client(timeout=30) as client:
        for segment in post.segments:
            def _send(...): r = client.post(...); r.raise_for_status(); return r.json()
            data = with_retry(_send)["result"]
            ...
except httpx.HTTPError as exc:
    return PublishResult(post.platform, ok=False, error=str(exc), ids=ids, urls=urls)
return PublishResult(post.platform, ok=True, ids=ids, urls=urls)
```

`with_retry` (base.py:23-44) — exponential backoff (2s → 4s → 8s), 4 attempts.
Critical detail at base.py:38-40:

```python
status = getattr(getattr(exc, "response", None), "status_code", None)
# Don't retry client errors except rate-limiting.
if status is not None and 400 <= status < 500 and status != 429:
    raise
```

Mapping for LinkedIn:
- **401** (`UNAUTHENTICATED`, expired access_token) → NOT retried by `with_retry`
  → bubbles up → caught by outer `except httpx.HTTPError` → `PublishResult(ok=False)`.
  Worker (`publish.py:225-238`) CASes to `publish-failed` with
  `notify_pending=True`.
- **429** (rate-limited) → retried with backoff. Eventually surfaces as success
  or after 4 attempts as `httpx.HTTPStatusError` → `ok=False`.
- **5xx** → retried (the `if 400 <= status < 500` filter lets them through).
- **403** (`PERMISSION_DENIED` — missing `w_member_social` scope) → NOT
  retried, surfaces as `ok=False, error="403 ..."`.

This is the same shape Telegram already uses for its own 401/429 — the worker
treats every `ok=False` identically. **There is no "dead-letter" mechanism**
beyond `JobStatus.PUBLISH_FAILED` — that status is terminal in the state
machine (`models.py:135`). The operator gets `notify_pending=True` and decides
whether to manually requeue.

### P6. Render dispatch — a dict, not a switch

`content/formatters.py:150-155`
```python
_RENDERERS = {
    Platform.TELEGRAM: render_telegram,
    Platform.DISCORD: render_discord,
    Platform.TWITTER: render_twitter,
    Platform.FARCASTER: render_farcaster,
}
def render(platform: Platform, post: SourcePost) -> RenderedPost:
    return _RENDERERS[platform](post)
```

For LinkedIn: add `render_linkedin(post: SourcePost) -> RenderedPost` and one
map entry. LinkedIn post limits (relevant to your spec): UGC posts allow ~3000
chars for text-only `shareCommentary`. Existing pattern produces `segments:
list[str]` — LinkedIn doesn't natively support threads the way Twitter does,
so the natural shape is a single segment with `_pack(units, LINKEDIN_MAX)`. The
adapter would iterate `post.segments` and POST one UGC post per segment (same
loop other adapters use) but in practice `len(segments) == 1` for LinkedIn.

### P7. Adapter unit test shape — `httpx.MockTransport`

`tests/test_publishers.py:20-49` is the template. Key moves:

```python
def handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    calls.append(payload)
    return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})

transport = httpx.MockTransport(handler)
real_client = httpx.Client
def fake_client(*args, **kwargs):
    kwargs["transport"] = transport
    return real_client(*args, **kwargs)
monkeypatch.setattr(httpx, "Client", fake_client)
```

Plus the `_no_sleep` autouse fixture that monkeypatches `base.time.sleep` to
make `with_retry`'s backoff instant. LinkedIn unit tests should reuse both.

The `tests/test_publishers.py:77-81` "unconfigured fails cleanly" test is a
must-mirror — it verifies the `not configured` short-circuit returns a
predictable error string.

---

## Integration points (where each layer reaches the next)

### IP1. Worker → upstream agent — the exact call site

`fabric/content-publisher/src/content_publisher/publish.py:189-199`
```python
from mnemonik_blogger.config import Platform  # deferred import — Decision 11

settings = _build_settings()
result = _run_campaign_from_article(
    article=article,
    platforms=[Platform.TELEGRAM],     # <— HARDCODED. The fan-out edit point.
    settings=settings,
    attest=False,
    min_score=_min_score_env(),
)
pr = result.results[0]                  # <— assumes ONE result. See Q1.
```

`_build_settings` (publish.py:58-81) currently only fills `TelegramConfig` from
env. It must learn to fill `LinkedInConfig` symmetrically:

```python
return Settings(
    dry_run=False,
    min_score=_min_score_env(),
    claude_blog_path=claude_blog_path,
    telegram=TelegramConfig(bot_token=SecretStr(...), channel=...),
)
```

### IP2. Per-job platform list — does NOT exist

`Job` model (`models.py:40-97`) has **no `platforms` field**. The job carries
`post_url: str | None` and `post_message_ids: list[int]` — singular URL,
singular message-id list. The worker has always assumed one platform per job
(`platforms=[Platform.TELEGRAM]`, `result.results[0]`).

For fan-out (one source → N platforms), three options surface as a design
decision (see Open Question Q1):

1. **One job per platform** — bot enqueues N jobs, each posts to one platform.
   Pro: zero schema change. Con: N attestation receipts (Q5), N rate-ceiling
   slots, no "all-or-nothing" semantics.
2. **One job, list of platforms, list of post-URLs** — `Job.platforms:
   list[Platform]`, `Job.post_urls: list[str]`. Pro: atomic. Con: schema +
   state-machine + queue migration; attest receipt design (Q5); partial
   failure UX (one OK, one 401) needs a new `partial-publish-failed` state.
3. **Sequential same-job, retry-of linkage** — first job posts TG, on success
   spawns a retries-of-self job for LinkedIn. Pro: no schema change. Con: ugly,
   serializes by definition, breaks the implicit "one attestation per article"
   invariant.

The user-spec / tech-spec templates in `work/blogger-linkedin/` are still
empty — this MUST be decided in Cycle 2 before implementation can start.

### IP3. Render preview — also hardcoded to Telegram

`fabric/content-publisher/src/content_publisher/render.py:24-32`
```python
def render_preview(article_path: Path) -> list[str]:
    from mnemonik_blogger.config import Platform
    from mnemonik_blogger.content.formatters import render
    from mnemonik_blogger.content.ingest import ingest_article

    src = ingest_article(article_path)
    rendered = render(Platform.TELEGRAM, src)   # <— hardcoded
    return list(rendered.segments)
```

If LinkedIn ships, what does the operator see in the Telegram approve-preview
message? Options: (a) still TG-rendered (status quo) — fine since the
post LOOKS the same on TG and approximately similar on LI; (b) two separate
preview blocks — needs bot UI change. The render module is otherwise stable.

### IP4. Attestation — platform name is invisible to the receipt

`mcp_client.py:172-185`
```python
"arguments": {
    "content": content,
    "content_sha256": content_sha256,
    "post_url": post_url,
    "score": score,
    "prompt": prompt,
},
```

No `platform` field. The receipt is bound to the article bytes
(`content_sha256`) and the canonical URL — both single-valued. For fan-out
this is the single biggest design question (Open Q5): one receipt per article
(bound to one canonical URL — which?) or one receipt per platform-post.

`attest.py:115-124` CASes `published → done` with `attestation_hash` and
`notify_pending=True`. The `Job` model has `attestation_hash: str | None` —
**singular**.

### IP5. Env-var flow — `/etc/blogger.env` → systemd → process

```
sops decrypt
   │
   ▼
deploy.yml passes vault_* → role var (deploy.yml:314-340)
   │
   ▼
content-publisher role renders templates/blogger.env.j2 (mode 0600 root:root)
   │
   ▼
systemd unit EnvironmentFile=/etc/blogger.env (service.j2:30)
   │
   ▼
process env → pydantic-settings TELEGRAM_BOT_TOKEN → TelegramConfig.bot_token
```

For LinkedIn, new env vars (e.g. `LINKEDIN_ACCESS_TOKEN`,
`LINKEDIN_REFRESH_TOKEN`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`,
`LINKEDIN_MEMBER_URN`) follow the same path. No new env-kind needed in
`env.py` — the publish call is in-process (`blogger_inproc` returns
`dict(os.environ)`).

### IP6. Ansible preflight smoke — per platform, deploy-time

`tasks/main.yml:545-607` does a two-step `getMe` + `getChatMember(@mnemonik,
publisher_bot_id)` and refuses to start the service if the bot isn't a channel
admin. The equivalent for LinkedIn would be calling the `userinfo` (or `me`)
endpoint with `Authorization: Bearer <access_token>` and asserting the
returned `sub` matches the configured `LINKEDIN_MEMBER_URN`. This is the
correct place to fail-loud on stale/expired access tokens.

---

## Existing tests as a pattern

| Test | Where | What LinkedIn tests should mirror |
|------|-------|-----------------------------------|
| `tests/test_publishers.py::test_telegram_publish_sends_thread` | `/Users/syi/src/sessions/blogger/tests/` | Use `httpx.MockTransport`. Capture posted payloads in a list. Assert the body the adapter sent matches the expected UGC-Posts shape. |
| `tests/test_publishers.py::test_unconfigured_publisher_fails_cleanly` | same | Build `LinkedInConfig()` with no token → publish() returns `ok=False, error="LinkedIn not configured"`. |
| `tests/test_publishers.py::_no_sleep` autouse fixture | same | Reuse to keep `with_retry` instant. |
| `tests/test_config.py::test_secret_not_in_repr` | same | LinkedIn access/refresh tokens must not appear in repr. |
| `tests/test_config.py::test_*_detected_when_credentials_present` | same | A platform-readiness test for LinkedIn (env vars set → `Platform.LINKEDIN in settings.configured_platforms()`). |
| `tests/test_ingest.py` | same | Untouched — ingest is platform-agnostic. |
| `fabric/content-publisher/tests/test_publish_handlers.py::test_publish_uses_keyword_only_signature` | `fabric/content-publisher/tests/` | Worker-side: if you change the hardcoded `platforms=[Platform.TELEGRAM]` to support fan-out, the kwargs-shape regression test needs to be updated. |
| `tests/test_separate_bot_token.py` | `fabric/content-publisher/tests/` | If LinkedIn introduces a "personal vs corporate" split, the analog for "two tokens that must not be confused" lives here. |

Test framework: pytest + pytest-asyncio (`asyncio_mode = "auto"`). httpx 0.27+.
No mocks framework, no fixtures library — plain monkeypatch.

---

## Shared utilities (already exist, reuse don't reinvent)

| Utility | Where | Use for LinkedIn |
|---------|-------|------------------|
| `with_retry(fn, attempts=4, base_delay=2.0)` | `publish/base.py:23-44` | Wrap every LinkedIn HTTP call. Already handles 401/4xx vs 5xx/429 correctly. |
| `dry_run_result(post)` | `publish/base.py:47-48` | Standard short-circuit when `Settings.dry_run`. |
| `_pack(units, max_len, joiner)` | `content/formatters.py:30-50` | Greedy paragraph/sentence packer — use for LinkedIn render. |
| `_paragraphs(text)` | `content/formatters.py:26-27` | Splits body on blank lines. |
| `_tagline(tags)` | `content/formatters.py:77-79` | Builds the `#tag1 #tag2` footer. LinkedIn supports hashtags identically. |
| `Settings.configured_platforms()` | `config.py:117-128` | Add a `LinkedInConfig.configured` check and one `if self.linkedin.configured: ready.append(Platform.LINKEDIN)`. |
| `ProtocolFact` grounding via `default_grounding(settings.facts_path)` | `grounding/mnemonik.py` | Untouched — the body is platform-agnostic. |
| `enforce_guardrails(text)` | `content/generate.py:17-21` (re-exported as `enforce_guardrails`) | Already called by `ingest_article`. No re-call needed in the adapter. |

---

## Constraints & infrastructure

### Versions / runtime

- Python `>= 3.11` (upstream `pyproject.toml`).
- httpx `>= 0.27` — already in core deps. **No new dep needed for LinkedIn**;
  REST API + Bearer token works with plain httpx (in contrast to twitter which
  bolted on tweepy as an optional extra).
- pydantic `>= 2.7`, pydantic-settings `>= 2.3`.
- typer `>= 0.12` for the CLI (`mnemonik-blogger preview/post`).

### Deploy contract (`tasks/main.yml`)

- `blogger_repo_ref` must be **40-char SHA** (line 26), tag/branch refused at
  preflight. LinkedIn lands at upstream → operator bumps the SHA in
  `defaults/main.yml` or sops → deploy re-clones.
- `requirements.locked.txt` is currently a placeholder
  (`infrastructure/ansible/roles/content-publisher/requirements.locked.txt:21-27`).
  When LinkedIn is added upstream and (if any) new transitive deps land, the
  lockfile must be regenerated offline per `README.md` lines 173-209.
- `tasks/main.yml:215-235` runs a deploy-time import + signature assertion
  against the installed `mnemonik_blogger`:
  ```python
  required = {'article','platforms','settings'}
  assert required <= set(sig.parameters)
  assert all(sig.parameters[n].kind.name == 'KEYWORD_ONLY' for n in required)
  assert Platform.TELEGRAM.value == 'telegram'
  ```
  This assertion **does not currently check for `Platform.LINKEDIN`**. If the
  worker is updated to depend on `Platform.LINKEDIN`, add a matching assert so
  a stale upstream SHA fails the deploy loud.
- The unit is hardened: `ProtectSystem=strict`, `NoNewPrivileges=true`,
  `ProtectHome=true`, `PrivateTmp=true`. The worker can only write to
  `ReadWritePaths=` (`content-publisher_work_dir` + the persistent volume).
  **A naive "write refreshed access_token to disk" plan must use one of those
  paths** — see Open Q4.

### Secrets convention

sops keys at `infrastructure/secrets/secrets.sops.yml:90-100`:
```
blogger_telegram_bot_token
blogger_telegram_channel
blogger_repo_ref
publish_callback_hmac_secrets
publish_auto_mode_token
publish_auto_mode_token_confirm
publish_mode
publish_min_score
publish_approval_timeout_min
blogger_prompts_topic_id
```

Naming convention is `blogger_<platform>_<field>` for adapter-bound secrets
and `publish_<concept>` for pipeline-level secrets. Predicted new keys
(decision-pending, see Q3 + Q4):

```
blogger_linkedin_client_id
blogger_linkedin_client_secret
blogger_linkedin_access_token
blogger_linkedin_refresh_token
blogger_linkedin_member_urn      # urn:li:person:<id>
# possibly also:
blogger_linkedin_token_expires_at   # ISO-8601 timestamp for refresh scheduling
```

Each new key must be templated into `blogger.env.j2` and passed in
`deploy.yml:314-340` via `vault_*` lookup.

### LinkedIn-specific operational truth

- LinkedIn access_token has a **~60-day lifetime** (per feature brief).
  The refresh_token lives ~1 year. There is **no existing token-refresh
  pattern in this codebase** — Telegram bot tokens don't expire, X uses
  long-lived access tokens, Farcaster uses a managed signer.
- The systemd unit is `Restart=always` with `EnvironmentFile=/etc/blogger.env`
  read at start. Refreshing an env var at runtime requires either
  (a) a deploy that re-renders the env-file + restart, or (b) the adapter
  reading the token from a file it can re-read every publish.
- See Open Q4 for the design choice.

---

## External libraries

No Context7 lookup needed for this scan: LinkedIn's UGC Posts API contract is
already decided per the feature brief (Member API, `w_member_social`,
`urn:li:person:<id>`, `Authorization: Bearer <access_token>`, POST
`https://api.linkedin.com/v2/ugcPosts`). The actual implementation will need
the live LinkedIn docs at code-writing time — but for code-research purposes
the relevant facts are:

1. The adapter is plain httpx (no SDK dep) — fits the Telegram/Discord/
   Farcaster pattern, not the Twitter (tweepy) pattern.
2. Response shape: `201 Created` with an `id` header `x-restli-id` carrying
   the URN of the created post (e.g. `urn:li:share:<id>`) — that becomes the
   `PublishResult.ids[0]`. The viewable URL is
   `https://www.linkedin.com/feed/update/urn:li:share:<id>/` →
   `PublishResult.urls[0]`.

---

## Potential problems (flag for tech-spec author)

### Risk 1: Worker hardcoding makes LinkedIn invisible until edited

`publish.py:194` (`platforms=[Platform.TELEGRAM]`) and `render.py:30`
(`render(Platform.TELEGRAM, src)`) are both hardcoded. The LinkedIn adapter
shipping upstream is necessary but **not sufficient** — the worker change is
the integration point and it lives in this repo.

### Risk 2: `result.results[0]` assumes one platform

`publish.py:202` reads `pr = result.results[0]`. If a future fan-out passes
`platforms=[Platform.TELEGRAM, Platform.LINKEDIN]`, `result.results` has TWO
entries with no current code path to consume the second.

### Risk 3: Attestation is single-URL-bound

`Job.post_url: str | None` and `mnemonic_sign_memory` accepts one `post_url`.
If a fan-out semantic is wanted (one article, two posts), the choice is:
(a) attest one canonical URL (LinkedIn? TG?), (b) attest twice, (c) change
the MCP tool signature.

### Risk 4: Access-token expiry has no scaffolding

The 60-day expiry is the single biggest operational risk for this feature.
No existing pattern fits. The systemd unit is `ProtectSystem=strict +
ReadWritePaths=` — writing back to `/etc/blogger.env` requires either
`sudo` (not available to the `op` service user) or a sidecar writer. See
Open Q4 for design options.

### Risk 5: The installed snapshot at `.venv/.../mnemonik_blogger` is stale

The snapshot (v0.1.0) does NOT have `run_campaign_from_article`,
`content/ingest.py`, or `quality.py`. Local dev tests that import from the
snapshot rather than the live upstream will silently use the old API.
`publish.py:44-51`'s deferred import is a defense against this, but local
tests still need the live upstream installed (`pip install -e
/Users/syi/src/sessions/blogger`).

### Risk 6: Per-platform deploy-time smoke is per-deploy fail-loud

The `tasks/main.yml:545-607` `getMe + getChatMember` smoke is the model that
catches stale credentials at deploy time. **Without an equivalent for
LinkedIn**, an expired LinkedIn access_token shipped in sops won't be
detected until the next publish attempt (operator-facing 401 several days
into the 60-day window). Add `userinfo` (or `GET /v2/me`) call in the role's
smoke section — fail loud at deploy if token doesn't authenticate.

### Risk 7: Markdown → LinkedIn formatting

LinkedIn UGC Posts use plaintext + URL detection (no markdown, no html).
The current `_RENDERERS` for Telegram emits MarkdownV2 with backslash escapes
(`_escape_telegram`). A LinkedIn renderer must NOT pass through any MarkdownV2
escapes — it needs its own `render_linkedin` that strips formatting markers
or emits a friendly plaintext shape. Hashtag handling is similar to other
platforms.

### Risk 8: Rate-ceiling is global (`MNEMONIK_POSTS_PER_HOUR_CEILING=20`)

`publish.py:41` + `_count_published_last_hour` (88-110) is a single counter,
not per-platform. With fan-out, one article = N counted publishes. Either
(a) accept this and tune the ceiling, or (b) make the counter per-platform.

---

## Open questions for Cycle 2 (only the user can answer)

These are the design decisions that block tech-spec authoring. They are
ordered by leverage — answering Q1, Q3, Q4 unlocks ~80% of the spec.

### Q1. Fan-out model: one job per platform OR one job with platform list?

The codebase has zero precedent for fan-out — every existing field
(`post_url`, `post_message_ids`, `attestation_hash`) is single-valued.
Three options (see IP2):

- **(a) One job per platform.** Bot enqueues two jobs from one operator
  click. Zero schema change. Simple. Loses "atomic publish" semantics — TG
  can succeed while LinkedIn fails 60 minutes later when rate-limited.
- **(b) Schema change: `Job.platforms: list[Platform]`, list-typed results.**
  Atomic. Requires queue migration, state-machine new "partial-publish-failed"
  state, attest receipt redesign (Q5). ~2x work.
- **(c) Same-job sequential (TG first, then LinkedIn).** No schema change.
  Single attestation receipt over the article. But LinkedIn failure rolls
  back nothing — TG post stays live. And serializes publish latency.

**Recommendation pre-Cycle-2:** start with (a) for v1. The bot already
enqueues; adding "now enqueue one per requested platform" is mechanical.
Revisit if the operator complains about partial-publish UX. **Confirm with
user.**

### Q2. Personal vs Company adapter — parameterize NOW or fork later?

Brief says future Company-page adapter is 95% reuse, only `author` URN +
scopes differ. Two ways:

- **(a) Subclass.** `class LinkedInCompanyPublisher(LinkedInPublisher):
  AUTHOR_PREFIX = "urn:li:organization:"`. One line override. Clean.
- **(b) Strategy.** `LinkedInPublisher(__init__(author_urn_prefix: str, ...))`.
  Single class, both modes constructed via different config classes.
- **(c) Defer.** Personal-only v1. Touch nothing for company. Subclass when
  the feature actually ships.

**Recommendation:** (a). One protected class attribute `_AUTHOR_KIND = "person"`
(or `_AUTHOR_PREFIX_URN`) read by `publish()` to build the `author` field.
Future Company adapter is `class LinkedInCompanyPublisher(LinkedInPublisher):
_AUTHOR_KIND = "organization"`. Tests can swap by subclassing the same fixture.

### Q3. What goes in `LinkedInConfig`?

Mandatory: `access_token: SecretStr`, `member_urn: str` (e.g.
`urn:li:person:abc123`).

Optional / for refresh: `refresh_token: SecretStr`, `client_id: SecretStr`,
`client_secret: SecretStr`, `token_expires_at: datetime | None`.

Is refresh-handling in scope for v1 (Q4)? If no, only the mandatory fields
need to land + a runbook ("renew token manually every 50 days, push to sops").

### Q4. Access-token refresh — who, when, where stored?

The 60-day expiry has no codebase precedent. Options:

- **(a) Manual rotation.** Operator runs a script (locally? on the VM?)
  every 50 days. Same model as `blogger_telegram_bot_token` rotation
  (README.md:117-145). New value goes to sops → deploy. Pro: zero new
  runtime code. Con: someone has to remember; pages-out failures.
- **(b) Sidecar refresh script + systemd timer.** A separate `linkedin-token-
  refresh.service` triggered by a 7-day timer reads `refresh_token` from
  `/etc/blogger.env`, calls LinkedIn `/oauth/v2/accessToken`, writes the new
  `access_token` to a side file (e.g.
  `/var/lib/content-publisher/linkedin-token.json`). The adapter reads from
  that file on each publish. Pro: hands-off. Con: extra unit, new env-kind
  needed in `env.py` to scope the refresh-service's env access, new
  `ReadWritePaths=` in service.j2, file format + atomic-rewrite semantics.
- **(c) Inline refresh on 401.** The adapter catches its own 401, calls
  refresh, retries the publish. Refreshed token is persisted to the side
  file. Pro: minimal moving parts. Con: refresh code lives in upstream
  blogger (synchronous on a 60-day cliff edge) — surprising; tests need a
  mock refresh server; rotation of the refresh_token itself is still manual.

The codebase is `ProtectSystem=strict` so any approach must write to
`ReadWritePaths` only. The persistent volume mount at
`{{ content_publisher_volume_mount }}/content-publisher/` is the right home
for a token side-file (survives VM destroy). `ProtectHome=true,
PrivateTmp=true` rule out `~` and `/tmp`.

**Recommendation:** (a) for v1 — ship LinkedIn with a manual-rotation runbook
mirroring the Telegram bot-token runbook (`README.md:117-145`). Add (b) or
(c) as a follow-up feature only if rotation hygiene proves painful.
**This is the single biggest call the user has to make.**

### Q5. Attestation receipt — one or N per article?

Current `mnemonic_sign_memory` payload includes ONE `post_url` and ONE
`content_sha256` — they're proving "this exact byte content was published
to this exact URL". If TG + LinkedIn each get their own post URL, three
options:

- **(a) One receipt, canonical URL = TG.** Telegram is the source-of-truth.
  LinkedIn URL captured in `Job.post_message_ids` (extra field) but not
  attested. Pro: zero MCP-side change. Con: LinkedIn post is "unattested" —
  fine for v1 if Q1's "one job per platform" is chosen because each job
  attests independently.
- **(b) Two receipts.** With Q1=(a), this falls out for free — each
  per-platform job runs its own attest cycle.
- **(c) One receipt covering both URLs.** Needs MCP tool signature change
  (`post_url: str` → `post_urls: list[str]`). Out of scope of this feature
  per brief ("mnemonic-mcp role internals (separate refactor in flight)").

**Recommendation:** Q1=(a) + Q5 implicit = each per-platform job gets its own
receipt. Zero MCP change.

### Q6. Render preview shape — TG-only or per-platform?

`render_preview` (`render.py`) is hardcoded to `Platform.TELEGRAM`. Before
the operator clicks approve, they see TG-formatted segments in their
Telegram-bot DM. Do they want to see the LinkedIn-formatted version too?

- **(a) Keep TG-only preview.** Operator approves once, all platforms ship.
  Fine if rendering differences are minor.
- **(b) Render both.** Bot sends N preview messages. Doubles the bot UX
  surface (which approve button binds to which platform's preview?).

**Recommendation:** (a). The article body is platform-agnostic — what
differs is hashtag positioning and char limits, which the operator usually
trusts the renderer to handle.

### Q7. `member_urn` shape — env var or hardcoded?

`urn:li:person:<id>` is a stable per-LinkedIn-account identifier. It does
not rotate. Two options:

- **(a) `LINKEDIN_MEMBER_URN` env var** sourced from sops. Future Company
  adapter swaps to `LINKEDIN_ORG_URN`.
- **(b) Derived from `userinfo`/`GET /v2/me` at startup** — adapter calls
  it once on first publish, caches in-process. Pro: one less sops key. Con:
  extra startup latency; deploy-time smoke (Risk 6) still needs the URN
  explicitly to compare.

**Recommendation:** (a). The deploy-time smoke needs the URN explicitly
anyway.

### Q8. Should the operator be able to disable LinkedIn per-publish?

User-spec is blank but the feature brief implies "agent prepares one blog
post and it gets fan-out published". Today the bot's "/publish-content"
flow has no platform selector. Q1=(a) suggests adding it (operator chooses:
TG only, TG + LinkedIn, LinkedIn only). This is bot-side UX, NOT adapter
work — out of scope of the adapter feature itself, but worth flagging so
Cycle 2 doesn't underscope.

---

## Recommended sequencing (for Cycle 2 to validate)

Wave 1 (upstream, parallelisable, no infra):
1. `Platform.LINKEDIN = "linkedin"` + `LinkedInConfig` in `config.py`.
2. `LinkedInPublisher` in `publish/linkedin.py`.
3. `build_publisher` registry entry.
4. `render_linkedin` + `_RENDERERS` entry in `content/formatters.py`.
5. Unit tests mirroring `test_publishers.py`'s shape.
6. CLI `_parse_platforms` alias `"li": Platform.LINKEDIN`.

Wave 2 (infra + secrets):
1. New sops keys (Q3 final list).
2. `blogger.env.j2` adds `LINKEDIN_*` vars.
3. `deploy.yml:314-340` adds `blogger_linkedin_*: "{{ vault_blogger_linkedin_* }}"`.
4. `tasks/main.yml` adds LinkedIn-equivalent of the `getMe + getChatMember`
   preflight (userinfo + URN match).
5. Bump `blogger_repo_ref` to the SHA where the LinkedIn adapter lands.
6. Regenerate `requirements.locked.txt` (no new deps expected; sanity
   regen).
7. Adapt the `tasks/main.yml:215-235` signature assertion to also check
   `Platform.LINKEDIN.value == "linkedin"`.

Wave 3 (worker — enables the feature):
1. `publish.py:_build_settings` fills `linkedin=LinkedInConfig(...)` from
   env (same shape as Telegram).
2. `publish.py:194` becomes a function of `Job` (per Q1 decision).
3. If Q1=(b): `Job` model + state machine + queue migration + multi-CAS in
   publish-step.
4. Worker tests updated.

Wave 4 (optional / follow-up):
- Refresh-token automation (Q4 b or c) if v1 manual rotation proves painful.
- Bot UX per-publish platform selector (Q8) if needed.

---

## Quick reference — absolute paths

- Upstream adapter target dir: `/Users/syi/src/sessions/blogger/src/mnemonik_blogger/publish/`
- Upstream tests target dir: `/Users/syi/src/sessions/blogger/tests/`
- Worker call site: `/Users/syi/src/sessions/coding-fabric/fabric/content-publisher/src/content_publisher/publish.py`
- Worker render call site: `/Users/syi/src/sessions/coding-fabric/fabric/content-publisher/src/content_publisher/render.py`
- Worker tests: `/Users/syi/src/sessions/coding-fabric/fabric/content-publisher/tests/test_publish_handlers.py`
- Env template: `/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/templates/blogger.env.j2`
- Ansible tasks: `/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/tasks/main.yml`
- Role defaults: `/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/defaults/main.yml`
- Sops keys: `/Users/syi/src/sessions/coding-fabric/infrastructure/secrets/secrets.sops.yml` (lines 90-100)
- Deploy invocation: `/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/playbooks/deploy.yml:314-340`
- Role runbooks: `/Users/syi/src/sessions/coding-fabric/infrastructure/ansible/roles/content-publisher/README.md`
