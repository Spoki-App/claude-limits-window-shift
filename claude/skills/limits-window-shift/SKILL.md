---
name: limits-window-shift
description: >-
  Fit Claude's usage windows to the user's workday. The 5-hour window starts at the first
  message, so a tiny automatic warm-up before work (06:00 for a 9-18 day) gives three fresh
  windows during the day instead of two. Use it when the user asks to set up, check or change
  their warm-up schedule, asks when their 5-hour or weekly limit resets, how much weekly usage
  is left, or says things like "shift my Claude windows", "optimize my Claude limits",
  "sposta le finestre di Claude", "ottimizza i limiti", "quando si resetta il limite",
  "finestra delle 5 ore", "limite settimanale". Also use it when a warm-up failed or the
  user got logged out of the CLI.
---

# limits-window-shift

`window_shift.py` sits next to this SKILL.md: `W="<this skill's base directory>/window_shift.py"`,
run as `python3 -I "$W" ...`.

## How it works, in one paragraph

The planner picks the warm-up time that makes the most 5-hour windows overlap the workday,
balanced so the first and last windows are equally useful. A scheduled cloud task sends the
early warm-up while the laptop sleeps; a night guard and a late warm-up, two more cloud tasks,
keep it working when other sessions use the account during the night. On the laptop, `tick` runs every 5 minutes: inside the
workday, when no window is open, it sends one tiny message (Haiku, no tools, no MCP, no
settings, no session saved) and reads the real reset time from Claude's rate-limit event.
The weekly reset is fixed per account by Anthropic: the tool shows it and the pace, it cannot move it.

## Commands

| Goal | Command |
|---|---|
| Show the best warm-up time for some hours | `$W plan --start 09:00 --end 18:00` |
| Explain a morning the warm-up did not open the window | `$W plan --night-run 02:16` (one per session that ran before work, evening ones included) |
| Save the workday (and the current profile) | `$W setup --start 09:00 --end 18:00 --days mon-fri` |
| Add a day off | `$W setup --skip-date 2026-12-24` |
| Add another Claude profile | `$W setup --profile work --config-dir ~/.claude-work` |
| Run `tick` every 5 minutes on this machine | `$W install` (remove: `$W uninstall`) |
| Remove everything | `$W uninstall`, then the user deletes the routines at https://claude.ai/code/routines (they cannot be deleted by tool), then remove `~/claude-limits-window-shift`, `~/.config/claude-limits-window-shift` and this skill's link, after confirming with the user |
| What the cloud tasks must look like | `$W cloud-spec` (`--json` for exact fields) |
| Current windows, next action, weekly pace | `$W status` |
| Send one warm-up now | `$W warm` |
| What the scheduler runs | `$W tick` (`--dry-run --now 2026-11-19T06:05` to simulate) |
| Check claude, login, flags, scheduler | `$W doctor` |
| Version and updates | `$W version` |

## Setting it up or changing it

Full setup is the runbook
https://raw.githubusercontent.com/Spoki-App/claude-limits-window-shift/main/SETUP.md. In short:

1. Hours and days from the user (ask once if missing). `$W plan` shows the result.
2. `$W setup ...`, `$W install`, `$W doctor` until every line is `OK`.
3. `$W warm` once.
4. Cloud tasks: each entry of `routines` in `$W cloud-spec --json` has a `routine`, the
   complete body for `RemoteTrigger` (`/schedule`). Fill `ENVIRONMENT_ID`, keep the rest as
   given, update a routine that already has that name instead of creating a second one, then
   read it back. Never create them for a different account than this session's.
5. When the warm-up time changes, repeat step 4 so every routine follows, and turn off
   (`enabled: false`) any `claude-limits-window-shift` routine that `cloud-spec` no longer lists.
   A night guard left at its old time swallows the new warm-up.

## When the window did not open in the morning

A warm-up only opens a window when none is open. List the account's routines and sessions for
what ran during the night (`last_fired_at`, run logs), then replay it with
`$W plan --night-run HH:MM ...`. A session between the warm-up minus five hours and the warm-up
is the usual cause; the night guard and the late warm-up exist for it, so check that both
routines exist. Say plainly when the replay ends with fewer windows: no cloud task can fix a
window opened in the hours the replay names.

## Rules

- Logins are the user's: when `doctor` or a warm-up reports `auth`, show the exact
  `claude auth login` command it prints and let the user run it. Never read, copy or
  print credentials, tokens or keychain entries.
- Never call Anthropic endpoints directly and never add `--bare` (it ignores the
  subscription login, so it would not open the window).
- Don't send warm-ups in a loop. One `warm` is enough; `tick` already guarantees at most one
  per window.
- English in config and commit messages, no em dash characters.
