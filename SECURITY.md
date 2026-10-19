# Security

EconAgent is a **single-user, local** tool that handles API keys and drives an LLM with tool access. This page states what it
protects against, what it does not, and how to report problems.

## Design

| Concern | Control |
|---|---|
| Key storage | macOS Keychain (service `econagent`) or environment variables. Never written to files, never on command lines (the MCP config is a mode-0600 temp file deleted after each run). |
| Key leakage via logs | `httpx`/`httpcore` request logging (which prints URLs such as `...?apikey=...`) is pinned to WARNING in every process we control, including the patched FMP server. FMP and FRED error messages are scrubbed of keys. Tests cover this. |
| Agent can trade or change accounts | Order, cancel and account-config tools are denied by name via Claude Code's `--disallowedTools`; unused write tools of third-party servers are hidden the same way; built-in tools (shell, files, web) are disabled with `--tools ""`. Paper/live modes are refused by the runner. |
| Prompt injection from news / Fed text | Web-sourced text is returned inside an `untrusted_tool_output` envelope; prompts tell the model never to follow instructions found in data. The agent has no write-capable tools, so the blast radius of a successful injection is wrong analysis, not actions. |
| Malicious web page driving the local server | Binds to `127.0.0.1`; rejects unknown `Host` (DNS rebinding) and cross-site `Origin`; requires `application/json` for writes; CSP `default-src 'self'`, `frame-ancestors 'none'`. |
| XSS from model output | Markdown rendered with `marked`, sanitised by DOMPurify into a DOM fragment (no `innerHTML`); `style`, forms and inputs are forbidden; external images are blocked by CSP. |
| Third-party MCP servers | They receive your keys, so each is fetched at a pinned commit that was read before use (`scripts/vendors.txt`), built with `npm ci --ignore-scripts`, and patched where needed (`patches/`). |
| Usage runaway | One model job at a time; new jobs are refused above 90% of your five-hour Claude window unless you confirm. |

## Not covered

- A compromised machine or browser extension (anything that can read your Keychain or localhost traffic).
- The behavior of third-party servers beyond the pinned commits we reviewed, and of Yahoo/yfinance.
- Anything that hosts EconAgent for other people. It is not designed to be multi-user and must not be exposed beyond localhost.
- Claude Code's own session logs (`~/.claude/projects/`): if you paste a key into a Claude chat, it is stored there. Rotate any key you have pasted anywhere.

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) rather than a public issue.
Include steps to reproduce. This is a hobby-scale project: expect a best-effort response.
