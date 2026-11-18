# Setup runbook (for Claude)

The user sent you a message like *"Set up claude-limits-window-shift by following this runbook
(install it, start its scheduler, create its daily cloud task). I work 9-18, Monday to Friday."*

That message authorizes exactly these steps: running the release's `install.sh` (27 lines of
POSIX sh, read it first if you like), a background job that runs every 5 minutes on this machine,
a few one-word Haiku messages a day, and one scheduled cloud task on their Claude account.
Nothing else. Talk to the user in their language and keep it short. Every step is safe to repeat.

## 0. Before you start

- **Hours and days.** Take them from the message; ask once only if missing. Days default to
  `mon-fri`. Hours may be `9`, `09:00`, `9.30` or `17h30`. Days are English: `mon-fri`,
  `mon,wed-fri`.
- **Timezone.** The system one, unless the user names another (IANA, e.g. `Europe/Rome`).
- **The `claude` CLI.** Run `command -v claude`. The desktop app alone does not install it. If it
  is missing, tell the user and, with their OK, install it with the official installer
  `curl -fsSL https://claude.ai/install.sh | bash`.

## 1. Install

```sh
curl -fsSL https://github.com/Spoki-App/claude-limits-window-shift/releases/latest/download/install.sh | sh -s -- --start 09:00 --end 18:00 --days mon-fri
```

Add `--timezone <IANA name>` only if the user gave one. Expect `OK version`, `SAVED` and
`INSTALLED`, then a line `CLI <path>`. Use that path from now on:

```sh
W="<the path printed after CLI>"
```

Run it from the Claude profile the user wants to optimize (normally this session's). Running it
again from another profile (a different `CLAUDE_CONFIG_DIR`) adds that account as a second
profile; `SAVED ... profiles=` lists them.

If the output says `INSTALL_FAILED` (Linux without a systemd user session: WSL, containers,
SSH), show it to the user: the cloud task in step 4 still opens the first window; on a server
`loginctl enable-linger` usually fixes it. If the user had installed the plugin version, keep
only this one.

## 2. Check

```sh
python3 -I "$W" doctor
```

Every line must be `OK` (a `WARN` about missing flags is fine).

If `profile ... logged in` is `FAIL`, the CLI has no login of its own here (normal with the desktop
app, which signs in its sessions by itself). The sign-in is interactive, so the user runs it:
give them the exact command after `Fix:`. In the Claude Code CLI they can type `!` followed by the
command; in the desktop app, open it in a Terminal panel tab for them if you can, otherwise ask
them to paste it in Terminal. A browser opens and they sign in to the same Claude account this
session uses. Never type credentials, never read or print tokens. Then run `doctor` again.

## 3. First warm-up

```sh
python3 -I "$W" warm
```

Expect `WARMED profile=... tokens=..., window until ...` (about 600 tokens). If it fails with
`kind=auth`, go back to step 2.

## 4. Cloud task (opens the first window while the laptop sleeps)

```sh
python3 -I "$W" cloud-spec --json
```

Its `routine` field is the complete body for the `RemoteTrigger` tool (load it with ToolSearch,
or run the `/schedule` skill, which also lists the user's cloud environments).

- `list` the routines first. If one is named `claude-limits-window-shift warm-up`, `update` it
  instead of creating a second one.
- Replace `ENVIRONMENT_ID` with the user's environment id. One environment: use it. Several: ask.
- Keep everything else as given: the `CRON_TZ=` prefix, `allowed_tools: ["Read"]` (an empty list
  would get the full default tool set), `clear_mcp_connections: true`, no repository.
- If the Haiku model id is refused, use the smallest Haiku id you know.
- `get` it back: `allowed_tools` must be `["Read"]`, `mcp_connections` empty, `next_run_at` the
  next work day at the planned time.

If `RemoteTrigger` is not available, there is no environment, or the body is refused, show the
user the plain `python3 -I "$W" cloud-spec` output and the page
https://claude.ai/code/routines to create it by hand, from the same account.

## 5. Tell the user

Run `python3 -I "$W" status` and summarize in a few lines:

- the warm-up time and the resets it creates (for 9-18: 06:00, then 11:00 and 16:00);
- when their weekly limit resets (fixed by Anthropic per account, it cannot be moved) and how much is used;
- the routine link `https://claude.ai/code/routines/<id>`;
- that tomorrow, after the warm-up time, they can ask "how are my Claude limits?" to see it worked.

## Later

The skill `limits-window-shift` is installed now, so later requests ("start at 8", "I'm off
Friday", "remove it") go through it. When the warm-up time changes, repeat step 4.

## Rules

- Only these steps. No direct Anthropic API calls, no `--bare`, no credential access.
- One routine per Claude account, and only for this session's account.
- If a step fails twice, stop and show the user the exact output.
