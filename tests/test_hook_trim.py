"""What the hook decides to wrap, and what it must leave alone.

Every case here was a real judgment call while building the hook. The
false-positive tests matter more than the true positives: wrapping a watch
task or an interactive server would hang the tool call.
"""
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

from loader import load

hook = load("hook-trim.py")

BACKSLASH = chr(92)
POWERSHELL = "PowerShell"
CAPTURE = re.compile(r"\}\s*>\s*(\S+)\s*2>&1")


def wrapped(command, tool=POWERSHELL):
    return hook.decide(command, tool)


class NoisyCommandsAreWrapped(unittest.TestCase):

    def test_package_managers_and_compilers(self):
        for command in ["pnpm install", "pnpm test", "pnpm build", "npm ci",
                        "yarn build", "dotnet build", "dotnet test", "msbuild",
                        "mvn clean", "cargo build", "go test", "make",
                        "pip install requests", "gradlew build"]:
            with self.subTest(command=command):
                self.assertIsNotNone(wrapped(command))

    def test_test_runners_however_they_are_invoked(self):
        for command in [
                "jest",
                "tsc --noEmit",
                "eslint .",
                "vitest",
                "playwright test",
                "node_modules/.bin/jest",
                BACKSLASH.join([".", "node_modules", ".bin", "jest.cmd"])]:
            with self.subTest(command=command):
                self.assertIsNotNone(wrapped(command))

    def test_after_a_statement_separator(self):
        self.assertIsNotNone(wrapped("cd apps/web; pnpm build"))

    def test_after_a_conditional_separator(self):
        self.assertIsNotNone(wrapped("pnpm install && pnpm test"))

    def test_on_a_later_line_of_a_multi_line_command(self):
        # Claude Code routinely sends several statements on separate lines,
        # because PowerShell's working directory resets between tool calls.
        # A newline is a statement separator like any other.
        self.assertIsNotNone(
            wrapped("Set-Location C:/repo\n.\\node_modules\\.bin\\jest.cmd"))

    def test_a_quiet_first_line_does_not_shield_a_noisy_second(self):
        self.assertIsNotNone(wrapped("$env:CI = '1'\npnpm test"))


class QuietCommandsAreUntouched(unittest.TestCase):

    def test_ordinary_cmdlets_and_shell_builtins(self):
        for command in ["Get-ChildItem", "git status", "echo hi",
                        "Test-Path foo", "node --eval 1"]:
            with self.subTest(command=command):
                self.assertIsNone(wrapped(command))

    def test_an_empty_command(self):
        self.assertIsNone(wrapped(""))


class ExemptionsAreHonored(unittest.TestCase):
    """Matching NOISY is not enough - these would break or lose their point."""

    def test_informational_flags_where_the_output_is_the_answer(self):
        for command in ["dotnet build --version", "jest --help",
                        "tsc -v", "playwright test --list"]:
            with self.subTest(command=command):
                self.assertIsNone(wrapped(command))

    def test_watch_modes_and_servers_that_never_terminate(self):
        for command in ["jest --watch", "vitest --watchAll",
                        "pnpm dev", "pnpm start", "npm run serve"]:
            with self.subTest(command=command):
                self.assertIsNone(wrapped(command))

    def test_a_command_already_redirecting_to_a_file(self):
        self.assertIsNone(wrapped("pnpm build > build.log"))

    def test_a_missing_filter_leaves_the_command_untouched(self):
        # Wrapping a command around a filter that is not there would lose its
        # output. Running it unwrapped loses nothing but the trimming.
        real, hook.FILTER = hook.FILTER, "/nonexistent/trim-output.py"
        try:
            self.assertIsNone(wrapped("pnpm test", tool="Bash"))
        finally:
            hook.FILTER = real


class ShellDispatch(unittest.TestCase):
    """Windows sessions drive PowerShell; macOS and Linux drive Bash. The
    rewrites are not interchangeable, so the wrong one is worse than none."""

    def test_non_shell_tools_are_not_this_hooks_business(self):
        for tool in ["Write", "Read", "Glob", ""]:
            with self.subTest(tool=tool):
                self.assertIsNone(wrapped("pnpm test", tool=tool))

    def test_each_shell_gets_its_own_syntax(self):
        self.assertIn("Out-String", wrapped("pnpm test", tool="PowerShell"))
        self.assertRegex(wrapped("pnpm test", tool="Bash"), CAPTURE)

    def test_neither_shells_syntax_leaks_into_the_other(self):
        self.assertNotRegex(wrapped("pnpm test", tool="PowerShell"), CAPTURE)
        self.assertNotIn("LASTEXITCODE", wrapped("pnpm test", tool="Bash"))

    def test_both_capture_before_filtering_for_the_same_reason(self):
        # Neither shell can filter in a pipeline without losing something:
        # PowerShell loses the exit code, Bash loses the working directory.
        self.assertIn("$o = &", wrapped("pnpm test", tool="PowerShell"))
        self.assertRegex(wrapped("pnpm test", tool="Bash"), CAPTURE)


class TheBashRewriteShape(unittest.TestCase):

    def setUp(self):
        self.result = wrapped("dotnet build", tool="Bash")

    def test_merges_stderr_so_failures_are_not_lost(self):
        self.assertIn("2>&1", self.result)

    def test_uses_no_shell_expansion_claude_code_cannot_analyze(self):
        # When reads outside the working directories are blocked, Claude Code
        # asks the user about any command it cannot analyze statically, and
        # command substitution or variable expansion is exactly that. Every
        # rewritten build would ask, even one the user has allowed.
        self.assertNotIn("$", self.result)
        self.assertNotIn("`", self.result)

    def test_calls_the_filter_as_a_plain_command_an_allow_rule_can_match(self):
        interpreter = pathlib.Path(sys.executable).as_posix()
        self.assertIn(f"{interpreter} {hook.FILTER} ", self.result)

    def test_each_rewrite_captures_to_its_own_file(self):
        # Several sessions can build at once; a shared capture file would mix
        # their output.
        first = CAPTURE.search(wrapped("dotnet build", tool="Bash")).group(1)
        second = CAPTURE.search(wrapped("dotnet build", tool="Bash")).group(1)
        self.assertNotEqual(first, second)

    def test_the_command_is_never_placed_in_a_pipeline(self):
        # Bash runs every stage of a pipeline in a subshell, so piping the
        # command straight into the filter discards any cd it performed.
        # Claude Code's Bash tool carries the working directory between
        # calls, so that silently breaks the next one.
        self.assertNotIn("} 2>&1 |", self.result)

    def test_a_multi_line_command_is_captured_as_a_whole(self):
        # Without the group, only the final line would be redirected.
        result = wrapped("cd /repo\npnpm test", tool="Bash")
        self.assertIn("cd /repo\npnpm test\n}", result)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TheBashRewriteBehavesLikeTheOriginalCommand(unittest.TestCase):
    """Run the rewrite in a real shell: its shape only matters if it works."""

    def run_bash(self, script):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_a_succeeding_command_still_succeeds(self):
        self.assertEqual(0, self.run_bash(hook.rewrite_bash("echo fine")).returncode)

    def test_a_failing_command_still_fails_and_keeps_its_output(self):
        # Fails the way a build does, with a non-zero status. An explicit
        # `exit` would end the shell itself, wrapped or not, because the
        # group has to run in the current shell to keep any cd.
        run = self.run_bash(hook.rewrite_bash("echo broken; false"))
        self.assertNotEqual(0, run.returncode)
        self.assertIn("broken", run.stdout)

    def test_a_cd_inside_the_command_carries_to_what_follows(self):
        run = self.run_bash(hook.rewrite_bash("cd /") + "\npwd")
        self.assertEqual("/", run.stdout.strip().splitlines()[-1])

    def test_the_capture_file_is_removed(self):
        rewrite = hook.rewrite_bash("echo fine")
        self.run_bash(rewrite)
        self.assertFalse(pathlib.Path(CAPTURE.search(rewrite).group(1)).exists())


class WrappingIsIdempotent(unittest.TestCase):

    def test_an_already_wrapped_command_is_not_wrapped_again(self):
        once = wrapped("pnpm test")
        self.assertIsNone(wrapped(once))


class TheRewriteShape(unittest.TestCase):
    """Each clause exists for a reason that cost a debugging cycle."""

    def setUp(self):
        self.result = wrapped("dotnet build")

    def test_merges_stderr_so_failures_are_not_lost(self):
        self.assertIn("2>&1", self.result)

    def test_captures_the_exit_code_before_the_filter_overwrites_it(self):
        self.assertIn("$c = $LASTEXITCODE", self.result)

    def test_reraises_the_wrapped_commands_exit_code(self):
        self.assertIn("exit ($c ?? 0)", self.result)

    def test_degrades_to_raw_output_when_the_filter_is_missing(self):
        self.assertIn("Test-Path", self.result)
        self.assertIn("else { $o }", self.result)

    def test_invokes_the_interpreter_running_this_hook_not_bare_python(self):
        self.assertIn(pathlib.Path(sys.executable).as_posix(), self.result)

    def test_carries_no_backslash_paths(self):
        # A Windows backslash path survives the shell but not every layer
        # that parses the command on the way there. The hook then silently
        # never fires, which is the worst possible failure mode.
        self.assertNotIn(BACKSLASH, self.result)


class TheHookProtocol(unittest.TestCase):
    """Claude Code reads JSON on stdout; anything else is a broken hook."""

    def run_hook(self, payload):
        stdout = io.StringIO()
        real_stdin, real_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(payload)
        sys.stdout = stdout
        try:
            hook.main()
        finally:
            sys.stdin, sys.stdout = real_stdin, real_stdout
        return json.loads(stdout.getvalue())

    def test_an_untouched_command_emits_an_empty_object(self):
        result = self.run_hook(json.dumps(
            {"tool_name": POWERSHELL, "tool_input": {"command": "echo hi"}}))
        self.assertEqual({}, result)

    def test_a_wrapped_command_emits_updated_input(self):
        result = self.run_hook(json.dumps(
            {"tool_name": POWERSHELL, "tool_input": {"command": "pnpm test"}}))
        output = result["hookSpecificOutput"]
        self.assertEqual("PreToolUse", output["hookEventName"])
        self.assertIn("Out-String", output["updatedInput"]["command"])

    def test_other_tool_input_fields_survive_the_rewrite(self):
        result = self.run_hook(json.dumps({
            "tool_name": POWERSHELL,
            "tool_input": {"command": "pnpm test", "description": "run tests",
                           "timeout": 600000}}))
        updated = result["hookSpecificOutput"]["updatedInput"]
        self.assertEqual("run tests", updated["description"])
        self.assertEqual(600000, updated["timeout"])

    def test_malformed_stdin_does_not_take_the_tool_call_down(self):
        self.assertEqual({}, self.run_hook("not json at all"))


if __name__ == "__main__":
    unittest.main()
