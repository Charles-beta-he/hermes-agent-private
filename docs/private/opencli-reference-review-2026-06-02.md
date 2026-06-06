# OpenCLI Reference Review — 2026-06-02

## Source

- **claim**: openclaw/opencli — **CORRECTION**: repo is **jackwener/OpenCLI** (https://github.com/jackwener/opencli), 23.3K stars, Apache-2.0, actively maintained (last push: 2026-06-02). `openclaw/opencli` returns HTTP 404.
- **sister repos**: jackwener/wx-cli (3.2K★, Rust, WeChat local data), jackwener/tg-cli (245★, Python/Telethon, MTProto), jackwener/discord-cli (100★, Python/discord.py), openclaw/wacli (2.5K★, WhatsApp CLI), openclaw/spogo (230★, Spotify web-cookie CLI)

## Architecture (Three-Tier)

```
CLI (npm Node.js) ⇄ WebSocket localhost:19825 ⇄ Chrome Extension (background.ts/CDP) ⇄ Isolated Chrome Window
```

1. **CLI layer** (`opencli <site> <command>`, `opencli browser <session> <action>`)
2. **Daemon layer** (auto-start, persistent, listens on localhost:19825, HTTP /ping + WebSocket /ext)
3. **Extension layer** (Chrome service worker `background.ts`, CDP dispatch via `cdp.ts`, protocol `protocol.ts`)

### Key Design Points

- Extension uses Chrome `debugger` API (CDP) for navigation, click, type, extract, screenshot
- Cookie access per-domain only (never bulk dump)
- Commands: exec, navigate, tabs, cookies, screenshot, close-window, sessions, set-file-input, insert-text, bind, network-capture-start/read, wait-download, cdp, frames
- Isolated automation windows separate from user's browsing session
- Two window modes: foreground (visible) and background
- Session lifecycle with 10-min idle timeout; persistent site sessions bypass timeout

## Security Model

- **Daemon**: localhost-only (127.0.0.1:19825), no built-in authentication on WebSocket
- **Remote orchestration**: SSH reverse tunnel recommended (not direct daemon exposure); frp with strong auth as alternative
- **Extension**: 5 Chrome permissions (debugger, tabs, cookies, activeTab, alarms) — all auditable in manifest
- **Cookie access**: never written/modified/transmitted externally; domain-scoped only
- **Data flow**: strictly localhost; no analytics, telemetry, or external network requests from extension (confirmed in PRIVACY.md)
- **Risk**: daemon port on public network = full browser compromise (cookies + arbitrary JS execution)

## Messaging CLI Hub

External CLIs registered via `opencli external register`:

| CLI | Lang | Approach | Auth | Notes |
|-----|------|----------|------|-------|
| tg-cli | Python | MTProto (Telethon) | Telegram account | local SQLite cache, `tg listen --persist` for near-real-time |
| wx-cli | Rust | memory scan + local DB decrypt | macOS ad-hoc codesign | daemon architecture, **invasive** (TCC issues, fragile per macOS update) |
| discord-cli | Python | discord.py | Discord account | local SQLite cache |

## Mapping to Hermes Impact Classes

### ADOPT (high value, low risk)

1. **Browser Bridge Extension pattern** — A Chrome extension + local daemon providing CDP-based browser control is lighter than Hermes' current Playwright dependency for read-only operations (navigation, DOM extraction, cookie-reuse). Could ship as an optional Hermes toolset (`chrome-bridge` or similar). This is the highest-value transferable pattern.

2. **Adapter site-authoring workflow** — OpenCLI's `opencli-adapter-author` skill provides a structured pipeline: recon → endpoint discovery → auth mode selection → response field decoding → code generation → verification. This model could be adapted as a Hermes skill for adding web data extraction sources to the context pipeline.

### DEFER (medium value, needs investigation)

3. **wx-cli local WeChat data approach** — Interesting (memory scan + local SQLite decryption) but invasive on macOS: requires ad-hoc code signing, triggers TCC re-authorization, Apple Gatekeeper may break it on system updates. Worth monitoring but too fragile for production Hermes integration.

4. **SSH reverse tunnel remote orchestration** — Useful pattern if Hermes ever needs to drive a local browser from a remote agent server. The documented approach (autossh, -R flag, no daemon port exposure) is a clean model but low urgency today.

### REJECT (not applicable or duplicate)

5. **CLI hub unified discovery** — Hermes' tools/registry already provides built-in tool discovery (`opencli list` equivalent). Adding external binary passthrough would add indirection without proportional benefit.

6. **tg-cli MTProto approach** — Identical to Hermes' existing Telegram gateway (both use Telethon/MTProto). No transferable value.

## Minimal Security Slice for Hermes Chrome Bridge Adoption

If Hermes adopts the browser bridge pattern, the minimum surface must:

1. Chrome extension: load unpacked (dev) or Chrome Web Store install, manifest with only `debugger`, `tabs`, `cookies`, `activeTab`, `alarms` permissions
2. Daemon: localhost-only WebSocket, no auth (same as OpenCLI — the extension IS the auth boundary)
3. Cookie access: per-domain scoped only, never bulk dump, never written/modified
4. Session isolation: automation windows must be separate from user's real browsing (use Chrome's profile isolation)
5. No external network: extension must not make any non-localhost fetch/XMLHttpRequest
6. Daemon lifecycle: Hermes manages start/stop/health via the existing process/taskpool infrastructure
7. Avoid OpenCLI's daemon port risk: never expose to any network — if remote access is needed, enforce SSH reverse tunnel pattern

## Verification Commands Used

```bash
curl -s https://api.github.com/repos/openclaw/opencli     # → 404
curl -s https://api.github.com/orgs/openclaw/repos         # → no opencli
curl -s https://api.github.com/repos/jackwener/OpenCLI     # → 23.3K★, confirmed
curl -s https://api.github.com/repos/jackwener/wx-cli      # → 3.2K★
curl -s https://api.github.com/repos/jackwener/tg-cli      # → 245★
curl -s https://api.github.com/repos/jackwener/discord-cli # → 100★
curl -s https://api.github.com/search/repositories?q=opencli+in:name
```

Key files read from jackwener/OpenCLI: README.md, PRIVACY.md, extension/src/background.ts, extension/src/protocol.ts, docs/guide/browser-bridge.md, docs/guide/remote-orchestration.md, src/external-clis.yaml

Hermes context checked: AGENTS.md (gateway/platforms/), skill_view('browser-harness-integration') — confirms Playwright-based browser tooling.
