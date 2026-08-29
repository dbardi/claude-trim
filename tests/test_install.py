"""Merging into settings.json is the only genuinely dangerous thing here.

That file holds the user's own configuration. Clobbering an unrelated hook,
or leaving a duplicate that wraps a command twice, is a worse outcome than
the hook simply not installing.
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import install  # noqa: E402

SCRIPT = "/home/u/.claude/hook-trim.py"
MATCHER = "PowerShell"


def hooked(config):
    """Every command string configured under PreToolUse."""
    return [command.get("command", "")
            for entry in config.get("hooks", {}).get("PreToolUse", [])
            for command in entry.get("hooks", [])]


def ours(config):
    return [c for c in hooked(config) if install.MARKER in c]


class MergingIntoAnEmptyConfig(unittest.TestCase):

    def setUp(self):
        self.result = install.merge_hook({}, SCRIPT, MATCHER)

    def test_creates_the_hook(self):
        self.assertEqual(1, len(ours(self.result)))

    def test_scopes_it_to_powershell(self):
        entry = self.result["hooks"]["PreToolUse"][0]
        self.assertEqual("PowerShell", entry["matcher"])


class MergingIntoAPopulatedConfig(unittest.TestCase):

    def setUp(self):
        self.before = {
            "alwaysThinkingEnabled": True,
            "deniedMcpServers": [{"serverName": "claude.ai Resend"}],
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write", "hooks": [
                        {"type": "command", "command": "prettier --write"}]}],
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "log-it.sh"}]}],
            },
        }
        self.result = install.merge_hook(self.before, SCRIPT, MATCHER)

    def test_unrelated_settings_are_untouched(self):
        self.assertEqual(True, self.result["alwaysThinkingEnabled"])
        self.assertEqual([{"serverName": "claude.ai Resend"}],
                         self.result["deniedMcpServers"])

    def test_hooks_on_other_events_survive(self):
        self.assertEqual(
            "prettier --write",
            self.result["hooks"]["PostToolUse"][0]["hooks"][0]["command"])

    def test_other_pretooluse_hooks_survive(self):
        self.assertIn("log-it.sh", hooked(self.result))

    def test_the_caller_s_config_is_not_mutated(self):
        self.assertEqual([], ours(self.before))


class InstallingTwice(unittest.TestCase):

    def test_leaves_exactly_one_hook(self):
        once = install.merge_hook({}, SCRIPT, MATCHER)
        twice = install.merge_hook(once, SCRIPT, MATCHER)
        self.assertEqual(1, len(ours(twice)))

    def test_upgrades_an_older_install_in_place(self):
        old = {"hooks": {"PreToolUse": [
            {"matcher": "Bash|PowerShell", "hooks": [
                {"type": "command", "command": "python ~/.claude/hook-trim.py"}]}]}}
        upgraded = install.merge_hook(old, SCRIPT, MATCHER)
        self.assertEqual(1, len(ours(upgraded)))
        self.assertEqual("PowerShell",
                         upgraded["hooks"]["PreToolUse"][0]["matcher"])
        self.assertIn(SCRIPT, ours(upgraded)[0])


class Removing(unittest.TestCase):

    def test_takes_the_hook_out(self):
        installed = install.merge_hook({}, SCRIPT, MATCHER)
        self.assertEqual([], ours(install.remove_hook(installed)))

    def test_leaves_other_hooks_alone(self):
        config = {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "log-it.sh"}]}]}}
        result = install.remove_hook(install.merge_hook(config, SCRIPT, MATCHER))
        self.assertEqual(["log-it.sh"], hooked(result))

    def test_is_a_no_op_when_the_hook_was_never_installed(self):
        config = {"alwaysThinkingEnabled": True}
        self.assertEqual(config, install.remove_hook(config))

    def test_drops_containers_that_only_existed_for_this_hook(self):
        result = install.remove_hook(install.merge_hook({}, SCRIPT, MATCHER))
        self.assertNotIn("hooks", result)


class ShellSelection(unittest.TestCase):
    """The matcher has to name the shell tool Claude Code actually drives on
    this platform. Get it wrong and the hook installs cleanly, reports
    success, and then never fires."""

    def platform_is(self, name):
        return mock.patch.object(install.sys, "platform", name)

    def test_windows_sessions_hook_powershell(self):
        with self.platform_is("win32"):
            self.assertEqual("powershell", install.default_shell())

    def test_macos_and_linux_sessions_hook_bash(self):
        for name in ["darwin", "linux"]:
            with self.subTest(platform=name), self.platform_is(name):
                self.assertEqual("bash", install.default_shell())

    def test_the_chosen_matcher_reaches_the_written_entry(self):
        result = install.merge_hook({}, SCRIPT, install.MATCHERS["bash"])
        self.assertEqual("Bash", result["hooks"]["PreToolUse"][0]["matcher"])

    def test_both_is_available_for_a_machine_running_either(self):
        self.assertEqual("Bash|PowerShell", install.MATCHERS["both"])


class InstallThenUninstall(unittest.TestCase):

    def test_round_trips_back_to_the_original_settings(self):
        before = {
            "alwaysThinkingEnabled": True,
            "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "log-it.sh"}]}]},
        }
        after = install.remove_hook(install.merge_hook(before, SCRIPT, MATCHER))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
