import json
import re
import subprocess
import unittest

from helpers import ROOT, SKILL, load

EM_DASH = chr(0x2014)
TEXT_SUFFIXES = {".md", ".py", ".sh", ".json", ".txt", ".yml", ".yaml", ""}


def tracked_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.splitlines() if (ROOT / p).suffix in TEXT_SUFFIXES and (ROOT / p).is_file()]


def commands():
    parser = load(SKILL / "window_shift.py", "window_shift_repo").build_parser()
    sub = next(action for action in parser._actions if action.dest == "command")
    return sorted(sub.choices)


class RepoConsistencyTests(unittest.TestCase):
    def test_no_em_dash(self):
        offenders = [str(p.relative_to(ROOT)) for p in tracked_files() if EM_DASH in p.read_text(errors="ignore")]
        self.assertEqual(offenders, [], "house style: no em dash characters")

    def test_versions_agree(self):
        version = (ROOT / "VERSION").read_text().strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual((SKILL / "VERSION").read_text().strip(), version, "run scripts/sync.sh")
        self.assertEqual(json.loads((ROOT / "claude/.claude-plugin/plugin.json").read_text())["version"], version)
        self.assertIn(f"## [{version}]", (ROOT / "CHANGELOG.md").read_text())

    def test_every_command_is_documented(self):
        docs, skill = (ROOT / "docs" / "how-it-works.md").read_text(), (SKILL / "SKILL.md").read_text()
        for name in commands():
            self.assertIn(f"`{name}`", docs, f"docs/how-it-works.md misses {name}")
            self.assertIn(f"$W {name}", skill, f"SKILL.md misses {name}")

    def test_one_message_setup_points_to_a_runbook_that_uses_real_commands(self):
        readme, runbook = (ROOT / "README.md").read_text(), (ROOT / "SETUP.md").read_text()
        self.assertIn("by following https://raw.githubusercontent.com/Spoki-App/claude-limits-window-shift/main/SETUP.md (install it", readme)
        used = set(re.findall(r'python3 -I "\$W" ([a-z-]+)', runbook))
        self.assertTrue(used, "SETUP.md runs no command")
        self.assertLessEqual(used, set(commands()))
        self.assertIn("releases/latest/download/install.sh", runbook)

    def test_readme_stays_for_people(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("docs/how-it-works.md", readme)
        self.assertLess(len(readme.splitlines()), 130, "keep the README short; details go to docs/how-it-works.md")
        for technical in ("--output-format", "CLAUDE_CONFIG_DIR", "launchd", "rate_limit_event"):
            self.assertNotIn(technical, readme)

    def test_readme_video_opens_in_the_browser(self):
        readme = (ROOT / "README.md").read_text()
        videos = re.findall(r'href="([^"]+\.mp4)"', readme)
        self.assertTrue(videos, "README links no video")
        prefix = "https://cdn.jsdelivr.net/gh/Spoki-App/claude-limits-window-shift@main/"
        for url in videos:
            self.assertTrue(url.startswith(prefix), f"{url}: GitHub serves mp4 as a download, use jsDelivr")
            self.assertTrue((ROOT / url[len(prefix):]).is_file(), url)
        self.assertNotIn("```mermaid", readme, "the poster already shows before and after")

    def test_skill_name_matches_folder_and_plugin(self):
        self.assertIn(f"name: {SKILL.name}\n", (SKILL / "SKILL.md").read_text())
        self.assertEqual(json.loads((ROOT / "claude/.claude-plugin/plugin.json").read_text())["name"], SKILL.name)
        self.assertEqual(json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["plugins"][0]["name"], SKILL.name)

    def test_json_files_parse(self):
        for p in tracked_files():
            if p.suffix == ".json":
                json.loads(p.read_text())

    def test_no_secrets_committed(self):
        pattern = re.compile(r"sk-ant-(api|oat)\d\d-[A-Za-z0-9_-]{20,}")
        hits = [str(p.relative_to(ROOT)) for p in tracked_files() if p.parent.name != "tests" and pattern.search(p.read_text(errors="ignore"))]
        self.assertEqual(hits, [])

    def test_never_uses_bare_mode(self):
        for name in ("warmup.py", "window_shift.py"):
            self.assertNotIn('"--bare"', (SKILL / name).read_text())


if __name__ == "__main__":
    unittest.main()
