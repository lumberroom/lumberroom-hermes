# The lumberroom memory provider for Hermes Agent

**Date:** 25 September 2026 · **Status:** design, nothing built · **Side:** upstream (engine repo)

Every behaviour below is a design. Nothing in `client/hermes/` exists yet, no line of it has run,
and every number is a design target unless it carries a file and line. Source citations name the
Hermes checkout at `fdec926e` (`HERMES/`, `/Users/aditya/work/open-source/hermes-agent`), the engine
at this branch (`ENG/`), and lumberroom-cloud at `a03eece` (`CLOUD/`).

Decision record: [`../decisions/0021-harness-plugins-talk-mcp.md`](../decisions/0021-harness-plugins-talk-mcp.md).
Plan: [`hermes-plugin-plan.md`](hermes-plugin-plan.md).
Research this starts from: [`../research/hermes-openclaw-memory-plugins.md`](../research/hermes-openclaw-memory-plugins.md).

## 1. What it does

A Hermes user who selects `memory.provider: lumberroom` gets three things:

- **Recall on every turn.** Hermes calls the provider's `prefetch` before each non-trivial turn.
  The first such turn carries the `context_bootstrap` digest; every turn carries `memory_search`
  hits for the user's message. Hermes fences the text in `<memory-context>` and appends it to the
  user message (`HERMES/agent/memory_manager.py:317-333`, `agent/turn_context.py:853-880`).
- **The engine's own tools.** The model sees `memory_search`, `memory_write`, `registry_get` and,
  when the grant carries `mayDelete`, `memory_forget`, with the descriptions and schemas the engine
  serves from `tools/list` (`ENG/src/mcp/mod.rs:191-360`). The write rule keeps one source.
- **One durable store.** Setup switches off Hermes's `MEMORY.md` and `USER.md`. An import command
  sends their entries to the engine's proposal queue for the owner to review.

It writes nothing on its own. `sync_turn` stores no turn. The model writes when it calls
`memory_write`.

It is the same plugin for a self-hosted engine and for lumberroom.cloud. The two differ in
`base_url` and in how the user signs in.

## 2. Owner decisions this spec builds on

Settled on 25 September 2026 and not reopened here:

1. Lumberroom is the sole durable store. Setup sets `memory.provider: lumberroom`,
   `memory.memory_enabled: false` and `memory.user_profile_enabled: false`.
   `import-builtin` posts to the proposal queue, never to the live store.
2. No automatic capture in v1.
3. Home: the engine repo at `client/hermes/`, its own `pyproject.toml`, PyPI name
   `lumberroom-hermes`, import package `lumberroom_hermes`, entry point group
   `hermes_agent.memory_providers`. The directory also loads when dropped or symlinked into
   `~/.hermes/plugins/lumberroom`. PyPI publication and the Hermes catalog PR need the owner's
   approval and no task performs them.
4. Hosted is one host: MCP at `https://mcp.lumberroom.cloud/mcp`, authorization server
   `https://lumberroom.cloud`, no per-tenant subdomain.
5. Both deployments are supported, and lumberroom.cloud is the default: an unset `base_url` means
   `https://mcp.lumberroom.cloud` with `auth: oauth`, because the hosted service carries more
   features. A self-hosted OSS engine runs the same code once `base_url` names it. This owner
   ruling of 25 September 2026 replaces the first one, under which `base_url` had no default and
   setup weighed both deployments equally. Cloud features such as dreaming run on the server and
   reach Hermes through the same MCP tools; the plugin carries no cloud-only code and builds no
   consolidation of its own.
6. Gateways fail closed, and a listed owner gets memory in DMs and in shared chats (group, guild,
   thread, channel, webhook), where each turn must come from a listed owner. An empty
   `owner_user_ids` refuses every gateway session. This owner ruling of 25 September 2026 replaces
   the first one, which refused every shared chat. Section 8 carries the rules and the accepted
   costs.
7. OAuth goes through the public `mcp` SDK `OAuthClientProvider` with a plugin-owned token store and
   cross-process lock, and never shares the lumberroom CLI's token file.
8. No Rust change in v1.
9. Hosted setup offers "Sign in with a browser" first and "Paste an API token (lr_...)" second
   (plan Q1, decided 25 September 2026).
10. Cron gets memory by default: `local_platforms` includes `cron` (plan Q2, decided 25 September
    2026).

**One correction to the premise behind decision 4.** The brief says hosted accepts OAuth only. The
fork refuses `AUTH_TOKENS` at boot (`CLOUD/src/cloud/auth.rs:143-152`), and it also accepts `lr_`
API tokens minted per tenant through `POST /api/v1/auth/tokens`, in every auth mode
(`CLOUD/src/adapters/auth/api_token.rs:1-45`, `CLOUD/src/adapters/auth/mod.rs:56-57`). The refusal
message itself sends the operator there. This spec therefore lets `auth: token` point at any
`base_url`, and hosted setup offers browser sign-in first and an `lr_` token second. The plugin code
is identical either way; only the setup picker changes. The owner confirmed this on 25 September
2026 (decision 9, plan Q1).

## 3. Package layout

`client/hermes/` is the plugin directory and the Python package at once. Hermes loads a directory
provider from `<dir>/__init__.py` (`HERMES/plugins/memory/__init__.py:63-73`) and reads `cli.py`
from the same directory (`:491-532`). A pip install maps the same files into `lumberroom_hermes/`,
and Hermes finds `cli.py` there through the entry point's package directory (`:124-158`).

```
client/hermes/
  __init__.py          register(ctx) -> ctx.register_memory_provider(LumberroomProvider())
  plugin.yaml          name lumberroom, version, description, hooks list
  pyproject.toml       dependencies, entry point, wheel mapping of this directory to lumberroom_hermes
  README.md            install, setup, config reference, failure table
  config.py            LumberroomConfig, parse, load, write_section
  schemas.py           snapshot, cache, MCP tool to Hermes schema, allowlist selection
  tools_snapshot.json  the engine's tools/list for the default tools plus server instructions
  bridge.py            Bridge: loop thread, two MCP sessions, admin HTTP
  auth.py              TokenAuth, OAuthAuth, build_auth, login, logout
  tokens.py            FileTokenStorage (SDK TokenStorage protocol), RefreshFence
  recall.py            digest and hit formatting, InjectedIds, Breaker, fixed status lines
  gate.py              SessionIdentity, session_allowed, turn_allowed
  provider.py          LumberroomProvider: hooks only
  wizard.py            get_config_schema, post_setup, apply_builtin_off
  importer.py          read_builtin_entries, import_builtin
  cli.py               register_cli, lumberroom_command
  tests/
    conftest.py        package loader, HERMES_HOME fixture, fake engine fixture
    fakes.py           FakeEngine: ASGI JSON-RPC fake replaying the captured transcript
    fixtures/          engine_transcript.json captured from a scratch engine
    test_*.py
```

**Three layout rules, each forced by a loader.**

- Modules import each other with relative imports only. Hermes loads a directory provider under a
  synthetic package name, `_hermes_user_memory.lumberroom__source_<digest>`
  (`HERMES/plugins/memory/__init__.py:80-87`), so `import lumberroom_hermes` fails there.
- `cli.py` imports nothing heavy at module level. Hermes imports it during argparse setup, before
  any provider loads (`:491-532`).
- No module is named `setup.py`. A `setup.py` beside `pyproject.toml` is the legacy build script
  and pip would run it. The setup wizard lives in `wizard.py`.

**Dependencies.** Declared in `pyproject.toml`, which is also the dependency surface Hermes's
package manager reads for a directory provider
(`HERMES/website/docs/developer-guide/memory-provider-plugin.md:40-50,283`):

| package | range | why |
|---|---|---|
| `mcp` | `>=2.0.0,<3` | The official SDK for the only surface the engine exposes to models. Hermes pins `mcp==2.0.0` in its `[mcp]` extra (`HERMES/pyproject.toml:398-402`), so this adds no new resolution. |
| `httpx2` | `>=2.7.0,<3` | mcp 2.0 sends its requests through `httpx2`; the plugin builds the client it hands the SDK (`HERMES/tools/mcp_tool.py:190-205`). |
| `filelock` | `>=3.12,<4` | Cross-process exclusive lock with a timeout on POSIX and Windows. The stdlib offers `fcntl` (POSIX only) and `msvcrt` (Windows only); Hermes hand-rolls both (`HERMES/tools/mcp_oauth.py:30-160`). Hermes's lock file already resolves `filelock` 3.32.4 transitively (`HERMES/uv.lock:1845`). |

Test-only: `pytest==9.1.1`, `pytest-asyncio==1.3.0`, the versions Hermes's own dev group pins
(`HERMES/pyproject.toml:535-545`). Build backend: `hatchling>=1.26,<2`.

The plugin imports these Hermes modules, all documented for providers: `agent.memory_provider`
(`MemoryProvider`, `RecallStatus`, `spawn_context_thread`), `agent.secret_scope.get_secret`,
`hermes_cli.config` (`load_config`, `save_config`, `save_env_value`), `hermes_constants.get_hermes_home`.
A contract test imports each one.

## 4. Configuration

The block lives at `memory.lumberroom` in the profile's `config.yaml`. The one secret lives in the
profile `.env` and the plugin reads it with `get_secret`, never `os.environ`, so a multiplexed
gateway reads the right profile (`HERMES/agent/secret_scope.py:200-228`).

| key | default | meaning |
|---|---|---|
| `base_url` | `https://mcp.lumberroom.cloud` | Engine origin, such as `http://127.0.0.1:8787` or `https://mcp.lumberroom.cloud`. The plugin strips one trailing `/` and appends `/mcp` unless the value already ends in `/mcp`. |
| `auth` | `oauth` | `token` or `oauth`. Setup writes it. No inference from which secrets happen to exist. |
| `LUMBERROOM_HERMES_TOKEN` (`.env`) | unset | Static bearer for `auth: token`. Its own name, so a `LUMBERROOM_TOKEN` exported for the CLI never becomes Hermes's credential by accident and the two clients stay apart in `lumberroom stats --by-client`. |
| `project` | `auto` | `auto`: the git root above `cwd` when Hermes passed one, else no project. `none`: never send one. Anything else: a slug or path sent as given. The engine reduces it to a slug (`ENG/src/domain/namespaces.rs:92-113`). |
| `recall` | `true` | Master switch for `prefetch`. |
| `digest` | `true` | Inject `context_bootstrap` on the first non-trivial turn after init, a reset or a compression. |
| `dreaming_review` | `false` | Expose the server's `review_queue` and `review_decide`, so Hermes can list and act on lumberroom.cloud dreaming proposals. Takes effect only on a lumberroom.cloud `base_url`; section 7.1. |
| `digest_max_chars` | `6000` | Client-side cap. Matches the engine default `BOOTSTRAP_MAX_CHARS` (`ENG/src/config.rs:863`). |
| `recall_limit` | `4` | `memory_search` limit per turn, 1 to 20. |
| `recall_max_chars` | `1200` | Cap on the per-turn hits block. |
| `review_interval` | `10` | Turns between write nudges in a primary session. `0` turns the nudge off. |
| `tools` | `[memory_search, memory_write, registry_get, memory_forget]` | Allowlist. The server's grant filters further. `context_bootstrap` is left out because the digest already arrives through `prefetch`. |
| `owner_user_ids` | `[]` | Gateway owners as `platform:id`, for example `telegram:123456789`. A listed owner gets memory in DMs and in group, guild, thread, channel and webhook chats; in a shared chat only a listed owner's own turns do. Empty refuses every gateway session. Section 8. |
| `local_platforms` | `[cli, tui, desktop, acp, cron]` | Platforms whose sessions count as the owner. Only these five are accepted: a gateway named here would count every member of its room as the owner, so gateway owners go in `owner_user_ids`. `cron` is in by default; remove it to keep cron jobs away from memory. Section 8 states the cost. |
| `prefetch_timeout_s` | `3.0` | Bound on one prefetch, under the host's 8.0s (`HERMES/agent/memory_manager.py:32`). |
| `tool_timeout_s` | `20.0` | Bound on one tool call. |
| `connect_timeout_s` | `3.0` | TCP and TLS connect bound. |
| `oauth_callback_port` | `47631` | Loopback port for the OAuth redirect. Fixed, because a DCR client registers one exact redirect URI and a random port would register a new client on every login. |

`config.parse` raises `ConfigError` naming the key for a missing `base_url` or `auth`, an unknown
`auth`, an `owner_user_ids` entry without a `platform:` prefix, and a number outside its range.

## 5. Transport

- One asyncio loop on one daemon thread, started through `spawn_context_thread` so the profile's
  contextvars travel with it (`HERMES/agent/memory_provider.py:22-33`). Hooks are synchronous and
  submit coroutines with `run_coroutine_threadsafe`.
- Two MCP sessions over `mcp.client.streamable_http.streamable_http_client(url, http_client=...)`,
  each with its own `httpx2.AsyncClient`:
  - the **hook session** sends `x-memory-invocation: hook`;
  - the **model session** sends no invocation header, which the engine reads as the model deciding
    (`ENG/src/domain/types.rs:287-310`).
  - Both send `x-session-id: <Hermes session id>` (`ENG/src/http/mod.rs:47-53`), updated in place on
    a session switch.
- The engine is stateless: `legacy_session_mode = false`, `json_response = true`
  (`ENG/src/http/mod.rs:66-82`). It issues no `Mcp-Session-Id`, so the SDK opens no standalone GET
  stream (mcp 2.0.0 `client/streamable_http.py`, `handle_get_stream`). A redeploy cannot strand the
  plugin, because the engine holds no session to lose (`ENG/src/http/mod.rs:68-70`).
- Admin HTTP for `import-builtin` goes through the same auth on a third plain client against the
  origin, not `/mcp`.
- Every call returns a `CallResult` with a `kind` of `ok`, `tool_error`, `unreachable`, `timeout`,
  `unauthorized` or `login_required`. A connect failure is `unreachable`. A read timeout after the
  request left is `timeout`, and the provider reports it differently because a write may have
  landed.

The engine returns tool failures as a result with `isError` set and text `"<tool> failed: <reason>"`
(`ENG/src/mcp/mod.rs:453-458`), and success as text plus `structuredContent` (`:421-426`). The
provider hands the model the structured payload as JSON, so `possible_conflicts` from
`memory_write` reaches it unchanged (`ENG/src/domain/types.rs:157-175`).

## 6. Auth

### 6.1 Static bearer

`auth: token`. The plugin sends `Authorization: Bearer <LUMBERROOM_HERMES_TOKEN>`.

- **Self-hosted:** one `AUTH_TOKENS` entry per Hermes profile. The engine honours static tokens in
  every `AUTH_MODE` whenever `AUTH_TOKENS` is set. A grant for a primary profile:
  `{"client":"hermes","token":"<openssl rand -hex 32>","read":[{"namespace":"*","max":"private"}],"write":["user:me","project:*","global"]}`,
  plus `"mayIngest":true` for `import-builtin` and `"mayDelete":true` to expose `memory_forget`
  (`ENG/docs/permissions.md:35-128`).
- **Hosted:** an `lr_` API token minted in the lumberroom.cloud dashboard. `AUTH_TOKENS` does not
  exist there.

### 6.2 OAuth

`auth: oauth`. Self-hosted with `AUTH_MODE=oauth`, and hosted.

The plugin builds the SDK's `OAuthClientProvider(server_url=mcp_url, client_metadata=...,
storage=FileTokenStorage(...), redirect_handler=..., callback_handler=...)` and passes it as `auth=`
on both MCP clients. The SDK does RFC 9728 and RFC 8414 discovery, RFC 7591 dynamic registration
and PKCE S256. The engine allows a plain-http loopback redirect on any port
(`ENG/src/domain/oauth.rs:477-517`), and DCR is on by default (`ENG/src/config.rs:837`).

Client metadata: `client_name: "Hermes Agent (lumberroom)"`, redirect
`http://127.0.0.1:<oauth_callback_port>/callback`, grant types `authorization_code` and
`refresh_token`, `token_endpoint_auth_method: none`. The consent screen shows that name and the
engine prints it as the writer's `source` (decision 0020).

**Token file.** `$HERMES_HOME/lumberroom/oauth.json`, mode 0600, written to a temp file and renamed.
It holds the tokens, an absolute `expires_at`, the DCR client info, the authorization-server
metadata and the `mcp_url` it belongs to. A file whose `mcp_url` differs from the configured one
reads as empty, so a changed `base_url` never sends one server's token to another. It never touches
`~/.config/lumberroom/`: two processes rotating one refresh family trip the engine's replay check,
which revokes the family (`ENG/src/authserver/routes.rs:779-790`).

**Why the plugin needs more than the SDK constructor.** mcp 2.0.0 has three gaps, and each one
would log a user out:

| gap in mcp 2.0.0 | effect | what the plugin does |
|---|---|---|
| `_initialize` loads tokens and never sets `token_expiry_time`, and the SDK refreshes only once a token has already expired | a restarted process sends an expired access token, gets a 401, and the SDK starts a full browser authorization instead of refreshing; a token the engine already counts as expired draws the same 401 | the plugin seeds `token_expiry_time` with `None`, so the SDK never refreshes on its own schedule, and `refresh_guard` refreshes inside the 60-second window itself |
| refresh posts to `oauth_metadata.token_endpoint`, or guesses `<origin>/token` when metadata is absent | a cold process guesses wrong: the engine serves `/oauth/token`, and hosted's authorization server sits on another origin | store the metadata after login; set `provider.context.oauth_metadata` from it after building the provider |
| nothing coordinates two processes holding one refresh token | the CLI, the gateway and cron on one profile can each refresh the same token; the engine revokes the family on the second | `RefreshFence`, below |

`token_expiry_time` and `oauth_metadata` are public fields of the SDK's `OAuthContext`. The plugin
overrides no underscore method. A contract test pins both fields against the installed SDK.

**RefreshFence.** The invariant: two Hermes processes on one profile never present the same
refresh token twice, because the engine revokes the whole family on a replay. Before each MCP
request, `OAuthAuth.refresh_guard()`:

1. If the token file's mtime changed since the provider was seeded, reseed the same provider's
   public context fields from disk in place. A peer refreshed.
2. If the stored access token expires within 60 seconds (design target), or the file records no
   expiry, start one refresh task per process, or join the one already running. The task takes the
   file lock `$HERMES_HOME/lumberroom/oauth.lock` (timeout 30s, design target) on a worker thread
   and re-reads the file.
   - The disk now holds a fresh token: a peer won the refresh. Reseed from it and release.
   - It does not: the task drives the SDK's public `async_auth_flow` with a probe request, so the
     SDK posts the `refresh_token` grant and `FileTokenStorage.set_tokens` persists the new pair.
     The task re-reads the file, reseeds, and only then releases the lock.
3. Otherwise proceed without the lock. The request itself never holds it.

Callers await the task under `asyncio.shield`, so a caller whose deadline passes (prefetch has
3.0s) cancels its own wait and leaves the refresh running, bounded by the fence timeout. Closing the
bridge calls `settle()` on the auth handle first, which waits up to 3.0s (a bound the plugin
chose on exit latency) for a pending refresh, or retries the save of a rotated pair the disk
refused. A refresh still running after that is cancelled and costs one sign-in. An SDK flow already queued on the SDK's `context.lock` when the task
forces a refresh may perform the grant itself; it does so inside the held fence.

Failures, as implemented and pinned by unit tests (the gate's step 13 covers the success path):

- The token endpoint answers 5xx or cannot be reached: `RefreshUnavailable`, reported as
  unreachable. Inside the skew window the still-live access token carries the call instead.
- It answers 4xx: `login_required`, latched until `oauth.json` changes, so a dead pair is not
  presented again.
- The refresh was sent and its answer never came back: `login_required`. The engine may already
  have rotated, and presenting the old pair again would trip the replay check. A lost answer costs
  one sign-in.
- The engine answered but the plugin could not write `oauth.json`: the rotated pair stays in
  memory, the plugin retries the write under the fence on the next guard, and it reports the disk
  error.

**Why not Hermes's own OAuth manager.** `tools.mcp_oauth_manager.get_manager().get_or_build_provider`
(`HERMES/tools/mcp_oauth_manager.py:282-311`) carries all three fixes and more: issuer binding,
dead-client detection, CIMD fallback. It is Hermes-internal, not a plugin API, and the owner ruled
for the public SDK path. The public path works in Hermes's process model: the plugin owns its loop
thread, so nothing in Hermes's MCP loop is shared, and the three gaps above close with public
fields plus a file lock. The cost is that the plugin maintains those fixes itself and does not
inherit Hermes's later ones. Reversal condition: if an mcp release renames either `OAuthContext`
field or changes refresh so the fence no longer brackets it, switch to `get_or_build_provider`
behind a contract test and a `requires_hermes` floor.

**Interactive and non-interactive.** Only `hermes lumberroom login` passes real handlers:

- `redirect_handler` opens the browser, or with `--no-browser` prints the URL.
- `callback_handler` races the loopback listener on `127.0.0.1:<oauth_callback_port>` against a line
  pasted on stdin (the full redirect URL from the browser's address bar), and returns code and
  state from whichever arrives first. This is how a headless host logs in: the engine has DCR and no
  device grant (`ENG/src/authserver/routes.rs:106-112`).

Every other path (provider hooks, `status`, `import-builtin`) builds handlers that raise
`LoginRequired`. The bridge maps that to `kind = "login_required"`. The provider asks again on
every turn rather than caching the answer, so a `hermes lumberroom login` in another terminal takes
effect on the next turn.

`hermes lumberroom logout` deletes `oauth.json` under the fence, so a peer mid-refresh cannot write
the file back. It revokes nothing on the server in v1.

### 6.3 What the owner sets up per deployment

| deployment | auth | owner action |
|---|---|---|
| self-hosted, `AUTH_MODE=token` | `token` | mint a token, add an `AUTH_TOKENS` entry, paste the token at setup |
| self-hosted, `AUTH_MODE=oauth` | `oauth` or `token` | `hermes lumberroom login`, consent with a profile (`full` for `import-builtin`) |
| lumberroom.cloud | `oauth` (setup's first choice) or `token` with an `lr_` token | `hermes lumberroom login`, or paste a dashboard token |

Consent profiles: only `full` carries `mayIngest` and `mayDelete` (`ENG/src/domain/oauth.rs:361-381`).
A `standard` consent gives read and write on `user:me`, `global` and `project:*` at `open`, which is
enough for recall and writes.

## 7. Hook by hook

The contract is `HERMES/agent/memory_provider.py:84-206`. The manager swallows every hook exception
(`HERMES/agent/memory_manager.py:367-377`) and logs "Memory provider activated" whether or not
`initialize` succeeded (`HERMES/agent/agent_init.py:1353-1354`). So the provider catches its own
failures, records a reason, and makes the reason visible in `prefetch`, in tool errors and in
`hermes lumberroom status`.

| hook | behaviour |
|---|---|
| `name` | `"lumberroom"` |
| `is_available()` | Config parses, and in token mode `LUMBERROOM_HERMES_TOKEN` is non-empty. No network. OAuth mode is available without a token file, so an unauthenticated plugin still loads and tells the user to log in. |
| `unavailable_reason()` | Names the missing or invalid key, or the missing env var. |
| `get_tool_schemas()` before `initialize` | The candidate set: `tools` allowlist names found in the schema cache `$HERMES_HOME/lumberroom/tools_cache.json`, else in `tools_snapshot.json`. `MemoryManager.add_provider` builds the routing table from this call, before `initialize` (`HERMES/agent/memory_manager.py:386-430`), so a name missing here never routes. |
| `initialize(session_id, **kw)` | Reads `hermes_home` and `platform`; `cwd`, `user_id`, `chat_type` and `agent_context` with `.get`, since the ABC does not guarantee them (`HERMES/agent/memory_provider.py:106-111`, `agent_init.py:1248-1283`). Loads config. Evaluates the session gate (section 8). A gated session stops here and opens no connection. Otherwise it resolves the project, builds auth in the caller's context (so `get_secret` runs inside the profile scope), starts the bridge, and fetches `tools/list` plus server instructions within 2.0s (design target). On success it writes the schema cache. On timeout it keeps the cache and marks itself degraded. On `login_required` it marks itself unauthenticated. It arms the digest. |
| `get_tool_schemas()` after `initialize` | Gated or inert: `[]`, so Hermes exposes no lumberroom tool (`HERMES/agent/memory_manager.py:614-635`). Otherwise the candidate names that the live list also carries, with the live description and schema. Degraded: the candidate set. Never a name outside the candidate set. |
| `system_prompt_block()` | Gated or inert: `""`. Otherwise the server instructions (live, else from the snapshot), then one line: "Lumberroom is the durable memory here. Record durable facts with memory_write." When both built-in flags are off it adds "The built-in MEMORY.md and USER.md are off." Static for the session. |
| `on_turn_start(turn, message, **kw)` | Records `kw.get("author_id")` for this turn. Hermes sends `None` when the transport names no author (`HERMES/agent/memory_provider.py:154-157`). Section 8 reads it. |
| `prefetch(query, *, session_id)` | Section 9. |
| `recall_status()` | `RecallStatus("lumberroom", n)` where `n` counts hits the last prefetch injected; `None` when it injected nothing. |
| `queue_prefetch` | Not implemented. |
| `sync_turn(...)` | Writes nothing and calls nothing. In a primary, allowed session it counts turns; at `review_interval` it marks the nudge due and resets. |
| `handle_tool_call(name, args, **kw)` | Refuses in this order: unknown name, inert, gated session or turn, unauthenticated. Then `tools/call` on the model session with `tool_timeout_s`. Returns a JSON string (section 10). |
| `on_session_switch(new_id, *, reset, rewound, **kw)` | Updates `x-session-id` on both sessions and clears the injected-id set. Re-arms the digest when `reset` is true or `kw.get("reason") == "compression"` (`HERMES/agent/conversation_compression.py:1715-1717,3457-3459`), since the summary may have dropped the earlier digest. |
| `on_pre_compress`, `on_session_end`, `on_delegation`, `on_memory_write` | Default no-ops. With the built-in store off, `on_memory_write` never fires. |
| `shutdown()` | Waits up to 3.0s for a token refresh in flight, then closes both sessions and stops the loop within 2.0s. Hermes bounds neither: its 5.0s drain (`HERMES/agent/memory_manager.py:31`) covers the sync executor, which runs first. |
| `get_config_schema()`, `save_config()`, `post_setup()` | Section 12. |
| `get_status_config(provider_config)` | `base_url`, `auth`, whether the credential is present, and a pointer to `hermes lumberroom status` for the live check (`HERMES/hermes_cli/memory_setup.py:381-383`). |

Subagents get no provider: `delegate_task` builds children with `skip_memory=True`
(`HERMES/tools/delegate_tool.py:241`). Cron agents get one with `platform="cron"`
(`HERMES/cron/scheduler.py:2437-2440`), and the default `local_platforms` counts them as the owner.

### 7.1 Dreaming review on lumberroom.cloud

Owner ruling, 25 September 2026. `review_queue` and `review_decide` reach the model only when all
three hold:

1. `dreaming_review` is `true`. Setup asks only on the lumberroom.cloud path, with No as the
   default.
2. The `base_url` host is `lumberroom.cloud` or one of its subdomains (`config.is_hosted`). The
   engine and the fork both answer `serverInfo` as `rmcp`, so the host is the only signal the
   plugin has; a staging copy of the hosted build under another domain reads as self-hosted.
   Reversal condition: the fork advertises a dreaming capability the plugin can read instead.
3. The server's live `tools/list` offers both tools, which it does only when the credential's grant
   carries the review capability.

Listing either tool in `tools` does nothing without the setting. The self-hosted engine carries the
same two tools for its cleanup and ingest proposals, and they stay off there.

Hermes builds its routing table before `initialize`, so the pre-init candidate set includes the two
tools when conditions 1 and 2 hold, and the post-init set drops them when condition 3 fails. The
descriptions and argument schemas come from the server unchanged; those descriptions tell the model
to work the queue only when the person asks and to give a reason with every proposal decision.

The plugin builds no consolidation of its own. Hermes, or its owner, may build dreaming-like
features beside it.

## 8. The owner gate

The digest is the owner's whole readable store. Only the owner's turns may reach it. The gate runs
twice: once per session in `initialize`, and once per turn before every prefetch and tool call. It
fails closed wherever it cannot tell who is speaking.

**Session level.**

- A session on a platform in `local_platforms` is the owner's. The default list includes `cron`.
- A `cron` session outside `local_platforms` is refused, so removing `cron` turns cron memory off.
- Any other platform is a gateway. An empty `owner_user_ids` refuses every gateway session.
- A gateway DM (`chat_type == "dm"`) is allowed when `f"{platform}:{user_id}"` appears in
  `owner_user_ids`.
- A gateway session on any other chat type (`group`, `guild`, `thread`, `channel`, `webhook`) or
  with no chat type is allowed when `owner_user_ids` is non-empty. The turn check decides each turn.

**Turn level.** Hermes passes each turn's author to `on_turn_start`, and `None` when the transport
names none (`HERMES/agent/turn_context.py:862-869`, `agent/memory_provider.py:154-157`).

- Local platforms: every turn is allowed.
- Gateway DM: a turn whose `author_id` is present and not an owner is refused. A DM turn with no
  author is allowed, since the session gate already matched the DM partner.
- Any other gateway chat: a turn is allowed only when `author_id` is present and
  `f"{platform}:{author_id}"` appears in `owner_user_ids`. A missing `author_id` refuses the turn,
  because the plugin cannot tell whose turn it is.

A refused session: `system_prompt_block` is empty, `get_tool_schemas` after init is empty, `prefetch`
returns `""`, `handle_tool_call` returns `{"error": "lumberroom tools are limited to the owner in
this chat."}`, and the plugin opens no connection. A refused turn in an allowed session: `prefetch`
returns `""`, every tool call returns the same refusal, and nothing reaches the engine. The tools
stay listed for the session.

**Where the author id comes from.** A gateway turn's author is the platform's sender id
(`HERMES/gateway/run_turn_runner.py:1700-1703`). Two transports need care when the owner lists an
id:

- `webhook`: the author is the route, `webhook:<route>` (`HERMES/gateway/platforms/webhook.py:663-664`),
  so the entry reads `webhook:webhook:<route>` and admits anyone who can post to that route.
- `api_server`: the author comes from the request body's `author` field, which Hermes documents as a
  memory label that grants nothing (`HERMES/gateway/platforms/api_server.py:669-677,4028`). An
  `api_server:` entry trusts whoever holds the API server key. Plan Q3 asks the owner about it.

**Costs the owner accepted on 25 September 2026.**

- Recall injected into an owner's turn in a shared chat is stamped into that user message and
  replays on every later turn of the shared transcript (`HERMES/agent/turn_context.py:884-900`). A
  later turn from another member runs against a transcript that holds the owner's recall, digest
  included, and that member can ask the agent to repeat it.
- `cron` counts as the owner. A cron job's output can be delivered to a chat, and the cron agent
  carries no user identity, so whatever a cron run recalls can reach that chat. The owner removes
  `cron` from `local_platforms` to turn it off.

The gateway `api_server` path reuses one initialized manager across rebuilt agents and never calls
`initialize` again (`HERMES/agent/agent_init.py:1327-1330`). The session gate and the project are
fixed at the first `initialize` for that gateway session; the per-turn author check still runs.

## 9. What prefetch injects

Hermes skips `prefetch` for trivial prompts (`HERMES/agent/turn_context.py:872-873`,
`agent/memory_provider.py:62-81`) and joins it on a thread for up to 8.0s.

Order of checks, each returning early:

1. Gated session or turn, `recall: false`, or inert: `""`.
2. Unauthenticated: the login line, once per session and again after each session switch.
3. Breaker open: `""`.

Then, on the hook session, bounded by `prefetch_timeout_s` in total:

- digest armed: `context_bootstrap {project}` and `memory_search {query, limit, project}` in
  parallel;
- otherwise `memory_search` alone.

The query is the user message clipped to 1000 characters (design target).

**Output, plain markdown.** The host adds the fence and the system note
(`HERMES/agent/memory_manager.py:317-333`), so the plugin never writes `<memory-context>`.

```
## lumberroom: what is already known
<context_bootstrap structuredContent.text, cut at the last newline within digest_max_chars>

## lumberroom: relevant to this message
- [user:me] Prefers draft PRs for engine changes. (id 3f0c9a4e-..., source Codex, occurred 2026-06-04)
- [project:lumberroom] ...

Review the recent turns. Write each decision, preference, constraint or durable fact not yet stored with memory_write, one fact per call.
```

- One bullet per hit. The content's newlines collapse to spaces. The id is the full UUID, because
  `memory_write` takes it as `supersedes`. `occurred` appears only when the hit carries `occurred_at`.
  Hit fields: `ENG/src/services/search.rs:56-83`.
- Hits whose id this session already injected are dropped. The set clears on every session switch.
- The nudge line appears when `sync_turn` marked it due, in primary sessions only.
- The digest disarms only after `context_bootstrap` succeeded.

**Size bounds, all design targets.**

| part | bound |
|---|---|
| digest | `digest_max_chars`, 6000 |
| hits block | `recall_max_chars`, 1200 |
| whole prefetch | 8000, under Hermes's 10,000-character spill threshold (`HERMES/tools/hook_output_spill.py:5,45`) |
| system prompt block | about 700 characters: the engine instructions plus two lines |

At Hermes's 2.75 characters per token, the first turn costs up to about 2,900 tokens and each later
turn up to about 450. Each block replays until compression, so 40 non-trivial turns add up to about
48,000 characters beyond the digest before dedupe. These numbers are ceilings from the caps, not
measurements. Replace them with figures from the engine's recall stats before changing a default.

**Breaker.** Three consecutive prefetch failures open it for 60 seconds (design targets). The first
failure of an outage returns `lumberroom unreachable: memory was not checked this turn.` so the model
does not claim it checked. Later failures in the same outage return `""`. Tool calls ignore the
breaker: the model asked for them.

## 10. Failure table

| condition | prefetch | tool call |
|---|---|---|
| engine down or connect refused | first failure of an outage: the unreachable line; then `""`; breaker after 3 | `{"error": "lumberroom unreachable at <host>: <reason>. Nothing was stored."}` for `memory_write`; `Nothing was deleted.` for `memory_forget`; `No memory was read.` for the rest |
| read timeout after the request left | `""` or the unreachable line, as above | `{"error": "lumberroom did not answer within <n>s. The call may have taken effect; search before retrying."}` |
| engine returns a tool error | hits block omitted for that call | `{"error": "<engine text>"}` |
| 401, token mode | `""` | `{"error": "lumberroom refused LUMBERROOM_HERMES_TOKEN (401). Check the token and its grant."}` |
| 401, OAuth, refresh fails or no token file | the login line once per session | `{"error": "lumberroom is not logged in. Run hermes lumberroom login."}` |
| OAuth, cold process, access token expired, refresh token live | the fence refreshes; the call proceeds | same |
| peer process refreshing at the same moment | the fence waits up to 30s, adopts the peer's token | same |
| fence wait exceeds 30s | treated as unreachable | treated as unreachable |
| `initialize` could not reach the engine | cached schemas and instructions; retries on the next call | same |
| `oauth.json` unreadable or corrupt | reads as logged out; the file is left in place | login line |
| config invalid | plugin stays inert; `unavailable_reason` names the key | `{"error": "<the reason>"}` |
| no `cwd` | `project: auto` sends no project | same |
| gated session or turn | `""` | the owner refusal |
| engine refuses a tripwire or a grant on write | n/a | the engine's text, returned unchanged; the plugin never retries it |

There is no local write buffer. A buffer would be a second durable store and would drop
`possible_conflicts`.

## 11. import-builtin

`hermes lumberroom import-builtin [--dry-run]` reads `$HERMES_HOME/memories/MEMORY.md` and `USER.md`
(`HERMES/tools/memory_tool.py:38-40`), splits each on `"\n§\n"` and drops empty entries
(`HERMES/tools/memory_tool_store.py:23,510-512`).

1. `POST /admin/ingest/runs` with `{"extractor": "hermes-builtin-import", "scope": {"profile": <name>, "hermes_home": <path>}}`
   returns `{"run_id": ...}` (`ENG/src/http/mod.rs:1464-1485`).
2. `POST /admin/ingest/proposals` with `{"extractor": "hermes-builtin-import", "facts": [...]}`, one
   fact per entry (`ENG/src/http/mod.rs:1681-1768`):
   ```json
   {"content": "<entry>", "namespace": "user:me", "tags": ["hermes-import"],
    "speaker": "main_model", "span_text": "<entry>",
    "source": {"file_path": "<abs path to USER.md>", "entry_uuid": "<sha256 hex of entry>", "run_id": "<run_id>"}}
   ```
   `USER.md` entries go to `user:me`, `MEMORY.md` entries to `global`. Speaker `main_model` never
   auto-approves (`ENG/src/services/ingest.rs:97-106`), and the credential tripwire runs before any
   proposal exists (`ENG/src/services/ingest.rs:154-163`). A re-run is idempotent: the source key is `file_path#entry_uuid`.
3. `POST /admin/ingest/runs/{id}/close` with `entries_seen`, `proposals_new` and
   `proposals_reinforced`.
4. Print the counts from the post report (`ENG/src/services/ingest.rs:145-152`) and the review
   command: `lumberroom ingest review`, or the console queue.

A 403 means the credential lacks `mayIngest`. The command prints the grant change (an
`AUTH_TOKENS` `"mayIngest":true`, or a `full` consent) and exits 2. `--dry-run` prints the entries
and namespaces and posts nothing. The files stay on disk; with the built-in store off, Hermes no
longer reads them.

## 12. Setup and the CLI

**`hermes memory setup lumberroom`** calls `post_setup(hermes_home, config)`, which owns the flow
(`HERMES/hermes_cli/memory_setup.py:157-166`):

1. Pick the deployment: "lumberroom.cloud", the Enter default, or "Self-hosted engine (enter its
   URL)".
2. Self-hosted: ask the URL, then `token` or `oauth`. Hosted: `base_url =
   https://mcp.lumberroom.cloud`, then "Sign in with a browser" or "Paste an API token (lr_...)".
3. Token: prompt for the secret and write it with `save_env_value("LUMBERROOM_HERMES_TOKEN", ...)`.
4. Write `memory.provider: lumberroom`, `memory.memory_enabled: false`,
   `memory.user_profile_enabled: false`, `memory.nudge_interval: 0` and the `memory.lumberroom`
   block with `save_config`, then print what changed.
5. OAuth: offer `hermes lumberroom login` now.
6. Run the live check from `status`.
7. When `MEMORY.md` or `USER.md` holds entries, offer `import-builtin`.

`get_config_schema()` returns `base_url` and `auth` for the dashboard panel. `save_config` writes
the `memory.lumberroom` block.

**`hermes lumberroom <command>`.** Hermes lists these only while lumberroom is the active provider
(`HERMES/plugins/memory/__init__.py:491-532`). The handler is `lumberroom_command`.

| command | does | exit |
|---|---|---|
| `login [--no-browser]` | OAuth login, section 6.2. Refuses in token mode. | 0 logged in, 1 failed |
| `logout` | deletes `oauth.json` | 0 |
| `status [--json]` | config, auth mode, credential presence, built-in store flags, then a live `tools/list`: tools the grant exposes, server version, round-trip ms | 0 reachable and authenticated, 1 otherwise |
| `import-builtin [--dry-run]` | section 11 | 0, 1, or 2 for a missing grant |

`status` exists because the host's "activated" log proves nothing.

## 13. What is not in v1

- Automatic capture of any kind.
- A local write queue.
- Revocation on `logout`.
- `hermes lumberroom setup` as a non-interactive command. The integration script writes
  `config.yaml` directly.
- A GitHub Actions job for the plugin. The owner's cost rule requires his approval first.
- PyPI publication and the `plugin-catalog/lumberroom.yaml` PR to NousResearch/hermes-agent.

## 14. Test strategy

Three layers. Only the third touches a real engine, and only the lead runs it.

**Unit, per module (pytest, no network).** A fake engine: a small ASGI app that answers the
JSON-RPC methods the SDK sends (`initialize`, `notifications/initialized`, `tools/list`,
`tools/call`) and the `/admin/ingest` routes, records every request's headers and body, and can
delay, fail or answer 401 on demand. It replays `tests/fixtures/engine_transcript.json`, which the
lock step captures from a scratch engine, so the fake serves the engine's real handshake result, tool
names, descriptions and schemas. The bridge reaches it through `httpx2.ASGITransport`. The SDK's own
`StreamableHTTPSessionManager` lost for this job: its `run()` task group binds to the loop that
entered it, and the bridge sends from its own loop thread. Each task's tests are listed in the
plan; the properties that matter most:

- pre-init schemas are a superset of post-init schemas, and a grant without `memory_forget` hides it;
- `initialize` with no `cwd`, `user_id` or `chat_type` key;
- `initialize` in OAuth mode with no token file does not raise and marks the plugin unauthenticated;
- a gated session opens no connection, and a gated turn calls nothing, including a shared-chat
  turn with no author;
- `sync_turn` makes no call;
- prefetch returns within `prefetch_timeout_s + 0.2s` against a fake that never answers;
- hook calls carry `x-memory-invocation: hook`, model calls carry none, both carry the session id,
  and a switch changes it;
- the breaker opens after three failures and closes after the cooldown on an injected clock;
- the fence: two processes sharing one token file refresh once.

**Hermes contract (pytest, Hermes installed in the venv).** Load the plugin from a temp
`HERMES_HOME/plugins/lumberroom` symlink through `plugins.memory.load_memory_provider`; run
`MemoryManager.add_provider` and assert every exposed tool routes; import every Hermes symbol the
plugin uses; assert the two `OAuthContext` fields exist in the installed mcp.

**Integration (`scripts/hermes-plugin-test.sh`, lead only).** The plan carries the full
script. In token mode and then `AUTH_MODE=oauth` against scratch engines, it installs the plugin
into a throwaway `HERMES_HOME`, runs `hermes lumberroom status`, writes a nonce through Hermes's own
`MemoryManager.handle_tool_call`, recalls it through `MemoryManager.prefetch_all` in a fresh session,
and checks `tool_calls` rows for the invocation flag and session id. It builds the wheel and loads the
provider through the entry point. In OAuth mode it shortens the access-token lifetime and refreshes
from two processes at once.

The script drives Hermes's `MemoryManager` rather than a model turn. `hermes -z` exists
(`HERMES/hermes_cli/oneshot.py:1-6`) and runs a real turn, but it needs a model provider, which bills
the owner, and a model decides whether to call `memory_write`, so a pass would measure the model.
The script offers the real turn as an opt-in step behind `HERMES_PLUGIN_LIVE_TURN=1` for the owner
to run by hand.

**Hosted acceptance, by hand, owner.** `hermes memory setup lumberroom` against lumberroom.cloud,
`hermes lumberroom login`, a nonce written from a real Hermes session, and `lumberroom search` from
another client.
