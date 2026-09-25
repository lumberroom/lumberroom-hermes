# Changelog

All notable changes to lumberroom-hermes. The format follows Keep a Changelog, and versions follow
semantic versioning.

## [Unreleased]

## [1.0.1] - 2026-09-26

### Fixed

- **A token refresh answered with a 5xx no longer keeps the refresh token.** The engine spends the
  refresh token before the steps that can still fail with a 500, and a proxy can answer 5xx after
  the engine answered, so presenting the token again could trip the engine's replay check and
  revoke the whole sign-in on every machine. The plugin now drops it; the live access token serves
  until it expires, then `hermes lumberroom login` signs in again. A refresh that never connected
  still keeps the token.

## [1.0.0] - 2026-09-25

### Added

- **The lumberroom memory provider for Hermes Agent.** Recall through Hermes's `prefetch` hook on
  every turn, the digest on the first, and the engine's own `memory_search`, `memory_write`,
  `registry_get` and `memory_forget` from the live `tools/list`, over `/mcp` through the official
  `mcp` SDK. lumberroom.cloud with browser sign-in is the default; `base_url` points it at a
  self-hosted engine, which runs the same code with a static token or OAuth.
- Setup turns off Hermes's `MEMORY.md` and `USER.md`; `hermes lumberroom import-builtin` sends
  their entries to the engine's proposal queue.
- Gateway chats admit only listed owners, turn by turn; cron counts as the owner.
- `dreaming_review`, off by default: Hermes may list and act on lumberroom.cloud dreaming proposals
  through `review_queue` and `review_decide` when the host is lumberroom.cloud and the server offers
  them.
- A cross-process refresh fence: two Hermes processes on one profile never present the same
  refresh token twice.
- `scripts/hermes-plugin-test.sh`, the end-to-end gate, in token and OAuth mode against an engine
  checkout.

Moved from `client/hermes/` in the engine repository on 25 September 2026, with its history.
