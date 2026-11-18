# Security policy

## Reporting

Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/Spoki-App/claude-limits-window-shift/security/advisories/new),
never in a public issue. You will get an answer within a few working days.

## What matters most here

- **Credentials.** The tool must never read, copy, log or send Claude credentials, OAuth
  tokens or keychain entries. It only runs the `claude` binary with the user's own login.
- **The warm-up command.** It must stay minimal: no tools, no MCP servers, no settings or hooks,
  from an empty working folder, with an allowlisted environment.
- **Local files.** Config and state are `chmod 600` in a `0700` folder. A world-readable config
  is refused.
- **The scheduler.** The launchd agent or systemd unit runs only `window_shift.py tick` with
  the user's own permissions.

## Supported versions

Only the latest release.
