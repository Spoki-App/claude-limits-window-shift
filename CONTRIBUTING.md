# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Rules

- **Python 3.9+ standard library and POSIX `sh` only.** No dependencies.
- **Official parts only.** The tool runs the `claude` CLI and documents Claude Code scheduled
  tasks. It never calls Anthropic endpoints, never reads credentials, never uses `--bare`.
- **Self-explanatory code.** No comments unless something would really be misread without one.
- **English** in code, docs and commit messages. **No em dash characters** (a test checks).
- **No secrets** in code, tests, fixtures or logs.

## Layout

| Path | What |
|---|---|
| `claude/skills/limits-window-shift/window_shift.py` | CLI entry point |
| `planner.py` | Pure math: best warm-up time, tick decisions, workdays left |
| `warmup.py` | The `claude -p` call and the stream-json parser |
| `store.py` | Config, per-profile state, log, lock |
| `service.py` | launchd, systemd and cloud cron rendering |
| `SKILL.md` | What Claude reads to use the tool |
| `tests/` | `unittest` suites, including repo consistency checks |

## Develop

```sh
python3 -m unittest discover -s tests -v
python3 -I claude/skills/limits-window-shift/window_shift.py plan --start 09:00 --end 18:00
```

## Pull requests

One topic per PR, with a test that fails without the change. Add a line under
`## [Unreleased]` in `CHANGELOG.md`. Use Conventional Commits. CI must be green.

## Releases (maintainers)

`scripts/release.sh X.Y.Z` on a clean `main`: bumps `VERSION`, the plugin manifest and the
changelog, tags, pushes and publishes the GitHub release with `install.sh` attached.
