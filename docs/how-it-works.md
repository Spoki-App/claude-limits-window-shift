# How it works

The technical side of [claude-limits-window-shift](../README.md): what runs, when, and why it is safe.

## The three parts

1. **Planner.** From your hours it computes the best warm-up time (06:00 for 9-18), the
   resets it creates (11:00, 16:00) and the latest warm-up that still helps (07:30). It picks
   the time that gives the most 5-hour windows inside your workday, balanced so the first and
   last are equally useful.
2. **The cloud warm-up.** A Claude Code scheduled task runs at 06:07 on work days and answers
   "OK". Scheduled tasks count against the same plan limits as everything else, so this opens
   the first window while your laptop is closed. Two more tiny tasks protect it from sessions
   that run during the night, see [Nights with other sessions](#nights-with-other-sessions).
3. **The local tick.** Every 5 minutes, inside your workday, `tick` checks whether a window is
   open. If none is (you were at lunch when the 11:00 one ended, or the cloud run failed), it
   sends one warm-up and records the new reset time. If you already started a window yourself,
   the warm-up joins it and the tick learns its end. At most one warm-up per window.

```mermaid
sequenceDiagram
    participant Cloud as Scheduled task (cloud)
    participant Tick as tick (your machine, every 5 min)
    participant CLI as claude -p (Haiku, no tools)
    participant Limits as Your plan limits
    Cloud->>Limits: 06:07 "OK" opens window 06:07-11:07
    Tick->>Tick: 09:00 laptop wakes, no known window
    Tick->>CLI: warm-up
    CLI->>Limits: joins the open window
    Limits-->>Tick: rate_limit_event: resets 11:07
    Tick->>Tick: 11:10 window over, you are idle
    Tick->>CLI: warm-up
    Limits-->>Tick: new window, resets 16:10
```

`plan` shows the math for any schedule:

```text
$ window_shift.py plan --start 09:00 --end 18:00
Without a warm-up: 2 windows touch your workday
  09:00-14:00  5h00 inside your workday
  14:00-19:00  4h00 inside your workday

With a warm-up at 06:00: 3 windows touch your workday
  06:00-11:00  2h00 inside your workday
  11:00-16:00  5h00 inside your workday
  16:00-21:00  2h00 inside your workday

Any warm-up between 04:30 and 07:30 keeps 3 windows.
```

## The warm-up

Exactly this, run from an empty folder with an allowlisted environment:

```sh
claude -p "OK" --model haiku --system-prompt "Reply with the single word OK." --tools "" \
  --strict-mcp-config --setting-sources "" --safe-mode --disable-slash-commands \
  --no-session-persistence --max-turns 1 --output-format stream-json --verbose
```

It costs about 600 tokens. The reset times come from the `rate_limit_event` in the stream, so
the tool never guesses where a window ends. Flags your Claude Code version doesn't know are left
out. `--bare` is never used: it ignores the subscription login, so it would not open the window.

## The cloud tasks

`cloud-spec` prints the tasks to create (`--json` gives every complete routine body in
`routines`, and the warm-up alone in `routine`):

```text
$ window_shift.py cloud-spec
  claude-limits-window-shift night guard
    Schedule: CRON_TZ=Europe/Rome 52 0 * * 1-5  (00:52)
    Why:      keeps night sessions inside a window that ends before the warm-up
  claude-limits-window-shift warm-up
    Schedule: CRON_TZ=Europe/Rome 7 6 * * 1-5  (06:07)
    Why:      opens the first window of the workday
  claude-limits-window-shift late warm-up
    Schedule: CRON_TZ=Europe/Rome 25 7 * * 1-5  (07:25)
    Why:      opens the window when a night session still had one open at the warm-up
  Each one:
    Prompt:     Reply with the single word OK. Do not use any tool.
    Model:      the smallest available (Haiku)
    Tools:      Read only (an empty list is replaced by the full default set)
    Repository, connectors: none
```

The one-message setup creates them for you with `/schedule`. By hand: the scheduled tasks page of
Claude Code on the web, from the same account. Then check their tools and connectors: a task
created with no tools gets the full default set, and one created without an explicit "no
connectors" gets all of yours.

## Nights with other sessions

The warm-up only opens a window when none is open. Anything else that uses your account during
the night (a scheduled PR check, a long agent run, you at 2 a.m.) opens its own 5-hour window,
and a 06:07 warm-up that lands inside it does nothing. A session at 02:16 keeps a window open
until 07:16: without help, your first message of the day starts the next one.

Two more cloud tasks, the same one-word message, cover this:

- **The night guard** runs five hours and a quarter before the warm-up (00:52). On a quiet
  account it opens a window that ends at 05:52, so every session during the night joins it
  instead of opening its own, and the 06:07 warm-up starts a fresh one. It does nothing when a
  window from the evening is still open.
- **The late warm-up** runs just before the last useful time (07:25). If a night window
  swallowed the 06:07 warm-up and has ended since, it opens the window then: you keep three
  windows, a little later than planned. If the warm-up worked, it joins that window.

`plan --night-run` replays a night, so you can see why a morning went the way it did:

```text
$ window_shift.py plan --start 09:00 --end 18:00 --night-run 20:40 --night-run 02:16
With other sessions at 20:40 (-1d), 02:16:
  20:40 (-1d)  session opens a window until 01:40
  00:52  night guard joins the window open until 01:40
  02:16  session opens a window until 07:16
  06:07  warm-up joins the window open until 07:16
  07:25  late warm-up opens a window until 12:25

3 windows touch your workday
```

What no cloud task can fix: a window from the evening is still open at the guard, and then a
session opens a new one between 02:30 and 04:30 (for 9-18). That window is still open at the
last useful warm-up, so the day gets two windows, as it would without the tool. The cloud tasks
cannot see whether a window is open; only the local tick can, and it runs while the laptop is
awake. `--not-before` also removes a guard that would run earlier than the time you set.

## Install by hand

```sh
curl -fsSL https://github.com/Spoki-App/claude-limits-window-shift/releases/latest/download/install.sh | sh -s -- --start 09:00 --end 18:00
```

It installs the Claude Code skill, saves your workday and runs the 5-minute scheduler (launchd on
macOS, a systemd user timer on Linux). Then create the cloud task above. The full runbook Claude
follows is [SETUP.md](../SETUP.md).

## Commands

`window_shift.py` lives in `claude/skills/limits-window-shift/`. Run it with `python3 -I`.

| Command | What it does |
|---|---|
| `plan` | Best warm-up time for `--start`/`--end` (and `--days`, `--margin`, `--not-before`); `--night-run 02:16` replays a night with other sessions |
| `setup` | Saves the workday, timezone, days off (`--skip-date`) and Claude profiles (`--profile`, `--config-dir`) |
| `install` | Runs `tick` every 5 minutes (launchd or systemd user timer) |
| `uninstall` | Removes the scheduler |
| `cloud-spec` | The cloud scheduled tasks to create (`--json` for the routine bodies) |
| `status` | Open window, next action, last warm-up, weekly usage and pace, per profile |
| `warm` | Sends one warm-up now |
| `tick` | What the scheduler runs (`--dry-run --now 2026-11-19T06:05` to simulate) |
| `doctor` | Checks Python, the `claude` CLI, its flags, each profile's login and the scheduler |
| `version` | Prints the version and checks for a newer release |

Several Claude accounts on one machine (different `CLAUDE_CONFIG_DIR`s) are supported: each
profile has its own state, and each account needs its own cloud tasks. Config lives in
`~/.config/claude-limits-window-shift/` (`chmod 600`, override with `CLWS_CONFIG`), next to the
per-profile state and a small `log.jsonl`.

## The weekly limit

Anthropic's help center says the weekly limit resets at a fixed time each week assigned to your
account, whatever you do, so the tool does not try to move it. `status` shows where you stand:

```text
weekly: 41% used, resets Thu 22:00, 2 workdays left, about 30% per workday
```

It also keeps the last weekly reset times it observed. If they ever stop being exactly a week
apart, `status` says so: that would mean the weekly window is rolling again.

## Security and terms

- **No credentials touched.** The tool runs the `claude` binary with your existing login. It
  never reads tokens, keychain entries or credential files, and never calls Anthropic endpoints.
- **Clean environment.** Warm-ups run with an allowlisted environment, from an empty folder,
  with tools, MCP servers, settings, hooks and plugins off.
- **Small footprint.** Haiku, a one-word answer, at most one per window from the laptop and
  three a day from the cloud, never retried after a limit is reached.
- **Your call.** Anthropic's terms allow automated use of Claude Code through its documented
  headless mode and scheduled tasks; limits "assume ordinary, individual usage". The tool doesn't
  add usage, it only chooses when your windows start. Read the terms for your plan.

Found a vulnerability? See [SECURITY.md](../SECURITY.md).

## Sources

Verified on 2026-11-18 with Claude Code 2.1.303.

| Fact | Source |
|---|---|
| The 5-hour window starts with your first message and is shared by claude.ai, Desktop and Claude Code | [Usage and length limits](https://support.claude.com/en/articles/11647753-usage-and-length-limits) |
| The weekly reset is fixed per account | same article |
| Scheduled tasks draw from the same plan limits | [Claude Code docs: scheduled tasks](https://code.claude.com/docs/en/routines.md) |
| `rate_limit_event` carries `resetsAt` and `utilization` in headless stream-json | [Agent SDK reference](https://code.claude.com/docs/en/agent-sdk/typescript.md) |
| `--bare` never reads the subscription login | [Headless mode](https://code.claude.com/docs/en/headless.md) |
| A window start is not rounded to the hour (community measurements) | the tool reads `resetsAt` instead of assuming |
| One warm-up costs about 600 tokens and returns both windows in `unifiedWindows` | measured, see `tests/test_warmup.py` |

Plans without a 5-hour limit don't need this tool: `status` will never see a 5-hour window.
