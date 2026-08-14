# Security Policy

Asteri takes runtime and supply-chain security seriously.

## Supported versions

| Version | Supported |
|---|---|
| 3.x | ✅ |
| 2.x | ❌ (upgrade to 3.x) |

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities. Instead,
report them privately via GitHub's Security Advisories:

- <https://github.com/IshikawaUta/asteri/security/advisories>

You can also email the maintainer directly at the address listed on the GitHub
profile. Include:

- The affected version(s).
- A minimal reproduction (config, worker class, and request details).
- The impact, and your suggested fix if you have one.

We aim to acknowledge reports within **72 hours** and to ship a patched release
as soon as practical. Please coordinate disclosure with us — we will credit you
in the release notes unless you prefer to stay anonymous.

## Security posture

- **Supply chain**: all GitHub Actions workflows are audited with `zizmor` on
  every push/PR and weekly. Actions are pinned to stable symbolic refs and kept
  current via Dependabot.
- **PyPI releases** use OIDC Trusted Publishing — no long-lived credentials.
- **Docker images** run as an unprivileged user.
- **Runtime hardening**: HAProxy PROXY protocol (`--proxy-protocol`), request
  body-size limits (`--max-body-size`), request parsing limits, and privilege
  dropping (`-u`/`-g`).

## Configuration file warning

`-c/--config` executes arbitrary Python code. Never run Asteri with config files
from untrusted sources.
