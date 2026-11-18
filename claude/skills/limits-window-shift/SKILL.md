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
early warm-up while the laptop sleeps. On the laptop, `tick` runs every 5 minutes: inside the
workday, when no window is open, it sends one tiny message (Haiku, no tools, no MCP, no
settings, no session saved) and reads the real reset time from Claude's rate-limit event.
The weekly reset is fixed per account by Anthropic: the tool shows it and the pace, it cannot move it.

## Commands

| Goal | Command |
|---|---|
| Show the best warm-up time for some hours | `$W plan --start 09:00 --end 18:00` |
| Save the workday (and the current profile) | `$W setup --start 09:00 --end 18:00 --days mon-fri` |
| Add a day off | `$W setup --skip-date 2026-12-24` |
| Add another Claude profile | `$W setup --profile work --config-dir ~/.claude-work` |
| Run `tick` every 5 minutes on this machine | `$W install` (remove: `$W uninstall`) |
| Remove everything | `$W uninstall`, then the user deletes the routine at https://claude.ai/code/routines (it cannot be deleted by tool), then remove `~/claude-limits-window-shift`, `~/.config/claude-limits-window-shift` and this skill's link, after confirming with the user |
| What the cloud task must look like | `$W cloud-spec` (`--json` for exact fields) |
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
4. Cloud warm-up: the `routine` field of `$W cloud-spec --json` is the complete body for
   `RemoteTrigger` (`/schedule`). Fill `ENVIRONMENT_ID`, keep the rest as given, update the
   existing `claude-limits-window-shift warm-up` routine instead of creating a second one, then
   read it back. Never create it for a different account than this session's.
5. When the warm-up time changes, repeat step 4 so the routine follows.

## Rules

- Logins are the user's: when `doctor` or a warm-up reports `auth`, show the exact
  `claude auth login` command it prints and let the user run it. Never read, copy or
  print credentials, tokens or keychain entries.
- Never call Anthropic endpoints directly and never add `--bare` (it ignores the
  subscription login, so it would not open the window).
- Don't send warm-ups in a loop. One `warm` is enough; `tick` already guarantees at most one
  per window.
- English in config and commit messages, no em dash characters.
