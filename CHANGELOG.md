# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-18

### Added
- Planner: the warm-up time that gives the most 5-hour windows inside a workday, balanced between the first and last window, with days off and custom work days.
- `tick`, run every 5 minutes by launchd or a systemd user timer: one tiny Haiku warm-up whenever no window is open during the workday.
- Real reset times read from the `rate_limit_event` of the headless stream, with weekly usage, pace and reset drift in `status`.
- `cloud-spec`: the Claude Code scheduled task that opens the first window while the laptop sleeps.
- Multiple Claude profiles (`CLAUDE_CONFIG_DIR`), `doctor`, `install.sh`, Claude Code plugin and skill.
- `cloud-spec` asks for the `Read` tool only: a scheduled task created with an empty tool list gets the full default set.
