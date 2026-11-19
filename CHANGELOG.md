# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-11-19

### Added
- Night guard and late warm-up: two more cloud tasks in `cloud-spec` (`routines` in `--json`). A session that runs on the account during the night opens its own window and used to swallow the 06:07 warm-up; the guard keeps night sessions inside a window that ends before the warm-up, and the late warm-up opens the window once a night one has ended.
- `plan --night-run HH:MM` replays a night with other sessions and shows which message opened each window.
- `status` lists the cloud helpers next to the cloud warm-up.

### Changed
- After an update, create the two new cloud tasks (`cloud-spec`). When the warm-up time changes, move all three and turn off any that `cloud-spec` no longer lists: a night guard left at its old time swallows the warm-up.

## [0.2.1] - 2026-11-18

### Changed
- `main` is protected (pull requests only, green CI, resolved review threads), so `scripts/release.sh` now ships each release through a release PR before tagging.
- SKILL.md explains how to remove everything, including the cloud task.

## [0.2.0] - 2026-11-18

### Added
- One-message setup: paste a sentence with your hours into Claude Code and it follows `SETUP.md` to install, check the login, send a first warm-up and create the cloud task.
- `cloud-spec --json` includes `routine`, the complete scheduled task body (cron with `CRON_TZ`, Haiku, `Read` only, no connectors, fresh event id).

### Changed
- Times accept `9`, `9.30` and `17h30` as well as `09:00`.
- `setup` run from another Claude profile (`CLAUDE_CONFIG_DIR`) adds it as a new profile instead of ignoring it.
- Every hint (`doctor` fixes, login, next steps) is a full command you can paste, with the real paths.
- A missing optional warm-up flag is a `WARN` in `doctor`, not a failure.
- Warm-ups never inherit `CLAUDE_CODE_OAUTH_TOKEN`, so they test the same login the scheduler uses.

## [0.1.0] - 2026-11-18

### Added
- Planner: the warm-up time that gives the most 5-hour windows inside a workday, balanced between the first and last window, with days off and custom work days.
- `tick`, run every 5 minutes by launchd or a systemd user timer: one tiny Haiku warm-up whenever no window is open during the workday.
- Real reset times read from the `rate_limit_event` of the headless stream, with weekly usage, pace and reset drift in `status`.
- `cloud-spec`: the Claude Code scheduled task that opens the first window while the laptop sleeps.
- Multiple Claude profiles (`CLAUDE_CONFIG_DIR`), `doctor`, `install.sh`, Claude Code plugin and skill.
- `cloud-spec` asks for the `Read` tool only: a scheduled task created with an empty tool list gets the full default set.
