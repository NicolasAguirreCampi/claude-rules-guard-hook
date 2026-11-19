import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "rules_guard.py"


class RulesGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        (self.home / ".claude").mkdir(parents=True)
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_hook(self, relative_path, session="s1"):
        payload = {"tool_input": {"file_path": str(self.project / relative_path)}, "session_id": session}
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.home)},
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else None

    def context(self, relative_path, session="s1"):
        return self.run_hook(relative_path, session)["additionalContext"]

    def test_stays_silent_for_a_file_no_topic_or_rule_file_covers(self):
        self.write(self.home / ".claude" / "CLAUDE.md", "## Comments\nNone.\n")

        self.assertIsNone(self.run_hook("README.md"))

    def test_injects_global_sections_whose_heading_matches_the_file_topic(self):
        self.write(self.home / ".claude" / "CLAUDE.md",
                   "## Controllers\nOnly REST.\n\n## Deploys\nFridays off.\n")

        ctx = self.context("app/controllers/users_controller.rb")

        self.assertIn("Only REST.", ctx)
        self.assertNotIn("Fridays off.", ctx)

    def test_follows_project_imports_recursively_and_relative_to_the_importing_file(self):
        self.write(self.project / "CLAUDE.md", "@docs/AGENTS.md\n")
        self.write(self.project / "docs" / "AGENTS.md", "## Services\nUse fail!.\n\n@more/extra.md\n")
        self.write(self.project / "docs" / "more" / "extra.md", "## Service naming\nVerb first.\n")

        ctx = self.context("app/services/charge.rb")

        self.assertIn("Use fail!.", ctx)
        self.assertIn("Verb first.", ctx)
        self.assertIn(f"## From {self.project / 'docs' / 'AGENTS.md'}", ctx)

    def test_an_import_cycle_reads_each_file_once(self):
        self.write(self.project / "CLAUDE.md", "@a.md\n")
        self.write(self.project / "a.md", "## Service a\nA rule.\n@CLAUDE.md\n")

        ctx = self.context("app/services/charge.rb")

        self.assertEqual(ctx.count("A rule."), 1)

    def test_ignores_at_signs_inside_code(self):
        self.write(self.project / "CLAUDE.md",
                   "```ruby\n@secret.md\n```\nUse `@secret.md` here.\n")
        self.write(self.project / "secret.md", "## Services\nShould not load.\n")

        self.assertIsNone(self.run_hook("app/services/charge.rb"))

    def test_a_hash_comment_inside_a_code_fence_does_not_end_the_section(self):
        self.write(self.home / ".claude" / "CLAUDE.md",
                   "## Services\nBefore.\n```ruby\n# not a heading\n```\nAfter the fence.\n")

        self.assertIn("After the fence.", self.context("app/services/charge.rb"))

    def test_a_matching_subsection_of_a_matching_section_is_injected_once(self):
        self.write(self.home / ".claude" / "CLAUDE.md",
                   "## Icons\nIntro.\n\n### Reusing an icon\nMatch the glyph.\n")

        ctx = self.context("app/views/users/index.html.erb")

        self.assertEqual(ctx.count("Match the glyph."), 1)

    def test_tells_the_model_that_project_rules_win_and_never_asks(self):
        self.write(self.home / ".claude" / "CLAUDE.md", "## Controllers\nOnly REST.\n")
        self.write(self.project / "CLAUDE.md", "## Controllers\nCustom actions are fine.\n")

        output = self.run_hook("app/controllers/users_controller.rb")

        self.assertIn("follow the project rule", output["additionalContext"])
        self.assertNotIn("permissionDecision", output)

    def test_injects_a_rule_file_in_full_once_per_session_and_only_its_path_afterwards(self):
        self.write(self.project / "CLAUDE.md", "")
        rule = self.project / ".claude" / "rules" / "rspec.md"
        self.write(rule, '---\npaths:\n  - "**/*_spec.rb"\n---\nTest every role.\n')

        first = self.context("spec/a_spec.rb")
        second = self.context("spec/b_spec.rb")
        other_session = self.context("spec/c_spec.rb", session="s2")

        self.assertIn("Test every role.", first)
        self.assertNotIn("paths:", first)
        self.assertNotIn("Test every role.", second)
        self.assertIn(f"- {rule.resolve()}", second)
        self.assertIn("Test every role.", other_session)

    def test_rule_files_reached_through_a_symlinked_folder_count_once(self):
        self.write(self.project / "CLAUDE.md", "")
        self.write(self.project / ".agents" / "rules" / "general.md",
                   '---\npaths:\n  - "**/*.rb"\n---\nKeep methods small.\n')
        (self.project / ".claude" / "rules").mkdir(parents=True)
        (self.project / ".claude" / "rules" / "shared").symlink_to(self.project / ".agents" / "rules")
        (self.project / ".claude" / "rules" / "general.md").symlink_to(
            self.project / ".agents" / "rules" / "general.md")

        self.assertEqual(self.context("lib/tasks.rb").count("Keep methods small."), 1)

    def test_a_rule_file_whose_paths_do_not_match_is_left_out(self):
        self.write(self.project / "CLAUDE.md", "")
        self.write(self.project / ".claude" / "rules" / "migrations.md",
                   '---\npaths:\n  - "db/migrate/*.rb"\n---\nUse concurrent indexes.\n')

        self.assertIsNone(self.run_hook("config/routes.rb"))

    def test_a_rule_file_covers_a_path_outside_the_built_in_topics(self):
        self.write(self.project / "CLAUDE.md", "")
        self.write(self.project / ".claude" / "rules" / "general.md",
                   '---\npaths:\n  - "**/*.rb"\n---\nKeep methods small.\n')

        self.assertIn("Keep methods small.", self.context("config/routes.rb"))


if __name__ == "__main__":
    unittest.main()
