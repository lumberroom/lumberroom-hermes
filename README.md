# lumberroom for Hermes Agent

lumberroom as Hermes's memory provider. With no `memory.lumberroom` block, or a block with no
`base_url`, the plugin talks to lumberroom.cloud with browser sign-in. Setting `base_url` points it
at a self-hosted OSS engine instead, running the same code with a static token or OAuth. A
cloud-only capability, such as dreaming consolidation, runs on the server and reaches the plugin
through the same tools; the plugin carries no cloud-specific code path (spec
[`docs/spec.md`](docs/spec.md) decision 4, `config.py`,
`provider.py`, `bridge.py`).

## What it does

- **Recall on every turn.** The provider calls `prefetch` before each non-trivial turn. The first
  such turn after init, a reset or a compression carries the `context_bootstrap` digest; every turn
  carries `memory_search` hits for the message (`provider.py:281-343`, spec §1, §9).
- **The engine's own tools.** The model sees `memory_search`, `memory_write`, `registry_get` and,
  when the grant carries `mayDelete`, `memory_forget`, with the live descriptions and schemas the
  engine serves from `tools/list` (`provider.py:210-221`, `schemas.py`).
- **One durable store.** Setup turns off Hermes's `MEMORY.md`, `USER.md`, and built-in write nudge
  (`wizard.py:_BUILTIN_OFF` sets `memory_enabled`, `user_profile_enabled` and `nudge_interval` to
  off). `import-builtin` sends their entries to the engine's proposal queue for review. It writes
  nothing on its own: `sync_turn` stores no turn, calls nothing, and only counts toward lumberroom's
  own write nudge (`provider.py:348-359`). The model writes when it calls `memory_write`.
- **Dreaming review, lumberroom.cloud only.** With `memory.lumberroom.dreaming_review: true`, the
  model also gets `review_queue` and `review_decide` and may list and act on lumberroom.cloud's own
  dreaming proposals; the server's own tool descriptions tell it to work the queue only when the
  person asks. Off by default. Setup asks for it only on the hosted path, and a self-hosted engine
  never sees the two tools even if the key is set by hand (`config.py:REVIEW_TOOLS`,
  `provider.py:_ensure_candidate_tools`).

## Install

From a release, into the directory Hermes loads providers from:

```bash
git clone --depth 1 --branch v1.0.0 https://github.com/lumberroom/lumberroom-hermes \
  ~/.hermes/plugins/lumberroom
hermes memory setup lumberroom
```

Hermes loads a directory provider from `<dir>/__init__.py` and finds `cli.py` in the same place
(spec §3). A Hermes plugin-catalog install, `hermes plugins install lumberroom`, lands the same way
once the catalog lists the plugin. A symlink to a working copy works for development.

An owner-managed Hermes build, such as Nix, where Hermes's own docs say not to pip-inject into a
package-manager-managed install, takes the wheel instead: each GitHub release attaches it, and
`pip install lumberroom-hermes` works once the package is on PyPI.

## Setup

Run `hermes memory setup lumberroom`, or set up one of the three rows below by hand.

| deployment | auth | owner action |
|---|---|---|
| lumberroom.cloud (default, setup's first choice) | `oauth` (default, browser sign-in) or `token` with an `lr_` dashboard token | `hermes lumberroom login`, or paste a dashboard token |
| self-hosted, `AUTH_MODE=token` | `token` | mint a token, add an `AUTH_TOKENS` entry, paste the token at setup |
| self-hosted, `AUTH_MODE=oauth` | `oauth` or `token` | `hermes lumberroom login`, consent with a profile (`full` for `import-builtin`) |

The wizard (`wizard.py:post_setup`) lists lumberroom.cloud first and picks it, with browser
sign-in, on a bare Enter; self-hosted is the second choice and asks for the URL and auth mode. It
re-prompts on an invalid choice before touching `config.yaml` or `.env`, then writes the credential
(`LUMBERROOM_HERMES_TOKEN` in the profile `.env`, or an OAuth login), sets `memory.provider:
lumberroom`, turns off `memory_enabled`, `user_profile_enabled` and Hermes's own write nudge, offers
the live check, and offers `import-builtin` when `MEMORY.md` or `USER.md` holds entries.

Right after the credential step, and only on the lumberroom.cloud path, the wizard asks "Let Hermes
review and act on lumberroom.cloud dreaming proposals?". A bare Enter or `no` writes nothing; `yes`
writes `memory.lumberroom.dreaming_review: true`. The self-hosted path never asks and never writes
the key.

Consent profiles: only `full` carries `mayIngest` and `mayDelete`. A `standard` consent gives read
and write on `user:me`, `global` and `project:*` at `open`, enough for recall and writes (spec §6.3).

## Configuration

`memory.lumberroom` in the profile's `config.yaml`. The one secret, `LUMBERROOM_HERMES_TOKEN`, lives
in the profile `.env` and is read with `get_secret`, never `os.environ`, so a multiplexed gateway
reads the right profile's token (`config.py`, spec §4).

| key | default | meaning |
|---|---|---|
| `base_url` | `https://mcp.lumberroom.cloud` | Engine origin, such as `http://127.0.0.1:8787` for a self-hosted engine. The plugin strips one trailing `/` and appends `/mcp` unless the value already ends in `/mcp`. |
| `auth` | `oauth` | `token` or `oauth`. Setup writes it explicitly; the plugin never infers it from which secrets exist. |
| `LUMBERROOM_HERMES_TOKEN` (`.env`) | unset | Static bearer for `auth: token`. Its own name keeps it apart from a `LUMBERROOM_TOKEN` exported for the CLI. |
| `project` | `auto` | `auto`: the git root above `cwd` when Hermes passed one, else no project. `none`: never send one. Anything else: a slug or path sent as given. |
| `recall` | `true` | Master switch for `prefetch`. |
| `digest` | `true` | Inject `context_bootstrap` on the first non-trivial turn after init, a reset or a compression. |
| `dreaming_review` | `false` | Lets the model call `review_queue` and `review_decide` on lumberroom.cloud's dreaming proposals; the server's own tool descriptions tell it to touch the queue only when asked. Reaches the model only when this is `true`, the engine is lumberroom.cloud, and the live `tools/list` offers both tools. `hermes lumberroom status` reports `off` with one of three reasons: `setting off`, `not lumberroom.cloud`, or `the server's grant lacks the tools`. Setup asks for it only on the hosted path. |
| `digest_max_chars` | `6000` | Client-side cap, 0 to 50,000. Matches the engine default `BOOTSTRAP_MAX_CHARS`. |
| `recall_limit` | `4` | `memory_search` limit per turn, 1 to 20. |
| `recall_max_chars` | `1200` | Cap on the per-turn hits block, 0 to 20,000. |
| `review_interval` | `10` | Turns between write nudges in a primary session, 0 to 1,000. `0` turns the nudge off. |
| `tools` | `[memory_search, memory_write, registry_get, memory_forget]` | Allowlist. The server's grant filters further. `context_bootstrap` is left out; the digest already arrives through `prefetch`. |
| `owner_user_ids` | `[]` | Gateway owners as `platform:id`, such as `telegram:123456789`. Empty refuses every gateway session. `api_server:` entries are refused at parse time (`config.py:125-129`): an `api_server` turn's author comes from the request body, so listing one would hand the owner's memory to anyone holding the API server key. |
| `local_platforms` | `[cli, tui, desktop, acp, cron]` | Platforms whose sessions count as the owner. `cron` is in by default; remove it to keep cron jobs away from memory. |
| `prefetch_timeout_s` | `3.0` | Bound on one prefetch, 0.5 to 7.5, under the host's 8.0-second join. |
| `tool_timeout_s` | `20.0` | Bound on one tool call, 1.0 to 120.0. |
| `connect_timeout_s` | `3.0` | TCP and TLS connect bound, 0.5 to 30.0. |
| `oauth_callback_port` | `47631` | Loopback port for the OAuth redirect. Fixed, because a DCR client registers one exact redirect URI and a random port would register a new client on every login. |

`config.parse` raises `ConfigError` naming the key for an unknown key, a `base_url` that is not a
string or does not start with `http://` or `https://`, an `auth` outside `token`/`oauth`, an
`owner_user_ids` entry without a `platform:` prefix or with `api_server:`, a `local_platforms` entry
outside `cli`, `tui`, `desktop`, `acp`, `cron`, and a number outside its range.

## Commands

`hermes lumberroom <command>` appears only while lumberroom is the active provider.

| command | does | exit |
|---|---|---|
| `login [--no-browser]` | OAuth login. Refuses in token mode. | 0 logged in, 1 failed |
| `logout` | deletes `oauth.json` | 0 |
| `status [--json]` | config, auth mode, credential presence (OAuth mode reads the stored token pair from `oauth.json`, no network), built-in store flags, then a live `tools/list`: server version, tools the grant exposes, round-trip time, the reason when unreachable, and a dreaming-review line (`on`, or `off` with one of the three reasons above) | 0 reachable, 1 otherwise |
| `import-builtin [--dry-run]` | reads `MEMORY.md` and `USER.md`, posts each entry to the engine's proposal queue for review, never to the live store | 0, 1, or 2 for a missing grant |

`status` exists because the host's "activated" log proves nothing about reachability (`cli.py`).

`import-builtin` batches entries at 100 per request, is idempotent on a re-run (the source key is
`file_path#entry_uuid`), and never auto-approves: every proposal's speaker is `main_model`
(`importer.py`). A 403 means the credential lacks `mayIngest`; the command names the fix and exits 2.
`--dry-run` prints the entries and namespaces and posts nothing.

## The owner gate

The digest is the owner's whole readable store, so only the owner's turns reach it. The gate runs
per session in `initialize` and per turn before every prefetch and tool call, and fails closed
wherever it cannot tell who is speaking (`gate.py`, spec §8).

- A session on a `local_platforms` entry is the owner's; every turn in it is allowed.
- Any other platform is a gateway. An empty `owner_user_ids` refuses every gateway session.
- A gateway DM is allowed when `platform:user_id` is listed; every turn in it is allowed unless its
  author is present and not an owner.
- A gateway group, guild, thread, channel or webhook is allowed once `owner_user_ids` is non-empty,
  but each turn needs its own author in the list. A turn with no author is refused there.
- A refused session gets an empty prompt block, no tools, and `""` from `prefetch`. A refused turn in
  an allowed session gets the same refusal from every tool call and no prefetch, but the tools stay
  listed.

Two costs the owner accepted on 25 September 2026: recall injected into an owner's turn in a shared
chat replays on every later turn of that transcript, so another member can ask the agent to repeat
it; and `cron` counts as the owner by default, so a cron run's recall can reach whatever chat the
cron job posts to. Remove `cron` from `local_platforms` to turn that off.

## Failure table

| condition | prefetch | tool call |
|---|---|---|
| engine down or connect refused | first failure of an outage: the unreachable line; then `""`; breaker opens after 3 consecutive failures for 60 seconds (design targets, `recall.py:Breaker`) | `{"error": "lumberroom unreachable at <host>: <reason>. Nothing was stored."}` for `memory_write`; `Nothing was deleted.` for `memory_forget`; `No memory was read.` for the rest |
| read timeout after the request left | `""` or the unreachable line, as above | `{"error": "lumberroom did not answer within <n>s. The call may have taken effect; search before retrying."}` |
| engine returns a tool error | hits block omitted for that call, digest stays armed to retry | `{"error": "<engine text>"}` |
| 401, token mode | `""` | `{"error": "lumberroom refused LUMBERROOM_HERMES_TOKEN (401). Check the token and its grant."}` |
| OAuth, no token file, or a refresh the token endpoint refused (4xx) | the login line once per session | `{"error": "lumberroom is not logged in. Run hermes lumberroom login."}` |
| OAuth, a due refresh answered 5xx or failed to connect, and the stored access token is already expired | treated as unreachable, as above | treated as unreachable, as above |
| OAuth, a refresh reached the engine and got no answer (a read timeout, the fence's own wait bound, or another failure after the send) | the login line once per session | the stored refresh token is dropped first, then `{"error": "lumberroom is not logged in. Run hermes lumberroom login."}`; costs one sign-in even when the refresh landed |
| OAuth, the engine rotated the pair and the plugin could not write it to `oauth.json` (disk full, permissions) | treated as unreachable, as above | treated as unreachable, as above |
| OAuth, cold process, access token expired, refresh token live | the fence refreshes 60 seconds before expiry (design target, `tokens.py:REFRESH_SKEW_S`); the call proceeds | same |
| peer process refreshing at the same moment | the fence waits on a file lock, adopts the peer's token | same |
| fence wait exceeds its timeout | treated as unreachable | treated as unreachable |
| `oauth.json` unreadable or corrupt | reads as logged out; the file is left in place | login line |
| config invalid | plugin stays inert; `unavailable_reason` names the key | `{"error": "<the reason>"}` |
| no `cwd` | `project: auto` sends no project | same |
| gated session or turn | `""` | the owner refusal |
| engine refuses a tripwire or a grant on write | n/a | the engine's text, returned unchanged; the plugin never retries it |

There is no local write buffer. A buffer would be a second durable store and would drop
`possible_conflicts` from `memory_write`.

When Hermes shuts down, the bridge first waits up to 3.0 seconds (a bound the plugin chose) for a
token refresh in flight, or retries saving a rotated pair the disk refused, before it cancels
anything. A refresh still running after that is cancelled, and the plugin drops the refresh token
rather than risk presenting a spent one, which costs one sign-in. A caller's own deadline never
reaches into a refresh: the refresh runs as its own task, so it finishes and its rotated pair
reaches disk after the caller who asked for it stops waiting (`bridge.py:_shutdown`,
`auth.py:OAuthAuth.settle`).

## What is not in v1

- Automatic capture of any kind.
- A local write queue.
- Revocation on `logout`. `hermes lumberroom logout` deletes the token file and revokes nothing on
  the server.
- `hermes lumberroom setup` as a non-interactive command. An integration script writes `config.yaml`
  directly instead.
- A GitHub Actions job for the plugin.

## Development

The plugin talks to the [lumberroom engine](https://github.com/lumberroom/lumberroom) over MCP and
ships on its own release cycle. Tags are `vX.Y.Z` in this repository.

```bash
python3.14 -m venv .venv-hermes-plugin
.venv-hermes-plugin/bin/pip install -e /path/to/hermes-agent[mcp]   # Hermes refuses a wheel build
.venv-hermes-plugin/bin/pip install "mcp>=2.0.0,<3" "httpx2>=2.7.0,<3" "filelock>=3.12,<4" \
  "pytest==9.1.1" "pytest-asyncio==1.3.0"
.venv-hermes-plugin/bin/python -m pytest -q
```

The end-to-end gate drives the plugin through Hermes's own loader and `MemoryManager` against
scratch engines built from an engine checkout, in token and OAuth mode:

```bash
POSTGRES_PASSWORD=... ./scripts/hermes-plugin-test.sh --engine-src ../lumberroom
```

It needs the engine's `lumberroom-server:0.4.0` image built from that checkout
(`docker build --target runtime -t lumberroom-server:0.4.0 .` in the engine) and its compose
database. `--capture` rewrites `tools_snapshot.json` and the test transcript from the engine it
starts, which is how the plugin picks up a change to the engine's tools.

Implemented against [`docs/spec.md`](docs/spec.md); read that spec for the transport, the OAuth
token-refresh fence, and the full hook-by-hook contract. [`docs/plan.md`](docs/plan.md) is the
build plan, written when the plugin lived at `client/hermes/` in the engine repository.
