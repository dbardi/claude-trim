"""What the hook decides to wrap, and what it must leave alone.

Every case here was a real judgement call while building the hook. The
false-positive tests matter more than the true positives: wrapping a watch
task or an interactive server would hang the tool call.
"""
import io
import json
import pathlib
import sys
import unittest

from loader import load

hook = load("hook-trim.py")

BACKSLASH = chr(92)
POWERSHELL = "PowerShell"


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


class ExemptionsAreHonoured(unittest.TestCase):
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


class ShellDispatch(unittest.TestCase):
    """Windows sessions drive PowerShell; macOS and Linux drive Bash. The
    rewrites are not interchangeable, so the wrong one is worse than none."""

    def test_non_shell_tools_are_not_this_hooks_business(self):
        for tool in ["Write", "Read", "Glob", ""]:
            with self.subTest(tool=tool):
                self.assertIsNone(wrapped("pnpm test", tool=tool))

    def test_each_shell_gets_its_own_syntax(self):
        self.assertIn("Out-String", wrapped("pnpm test", tool="PowerShell"))
        self.assertIn("mktemp", wrapped("pnpm test", tool="Bash"))

    def test_neither_shells_syntax_leaks_into_the_other(self):
        self.assertNotIn("mktemp", wrapped("pnpm test", tool="PowerShell"))
        self.assertNotIn("LASTEXITCODE", wrapped("pnpm test", tool="Bash"))

    def test_both_capture_before_filtering_for_the_same_reason(self):
        # Neither shell can filter in a pipeline without losing something:
        # PowerShell loses the exit code, Bash loses the working directory.
        self.assertIn("$o = &", wrapped("pnpm test", tool="PowerShell"))
        self.assertIn("__ct=$(mktemp)", wrapped("pnpm test", tool="Bash"))


class TheBashRewriteShape(unittest.TestCase):

    def setUp(self):
        self.result = wrapped("dotnet build", tool="Bash")

    def test_merges_stderr_so_failures_are_not_lost(self):
        self.assertIn("2>&1", self.result)

    def test_captures_the_exit_status_before_the_filter_overwrites_it(self):
        self.assertIn("__cs=$?", self.result)

    def test_reraises_the_status_without_terminating_the_shell(self):
        self.assertIn("(exit $__cs)", self.result)

    def test_degrades_to_cat_when_the_filter_is_missing(self):
        self.assertIn("|| cat", self.result)

    def test_the_command_is_never_placed_in_a_pipeline(self):
        # Bash runs every stage of a pipeline in a subshell, so piping the
        # command straight into the filter discards any cd it performed.
        # Claude Code's Bash tool carries the working directory between
        # calls, so that silently breaks the next one.
        self.assertNotIn("} 2>&1 |", self.result)

    def test_redirects_to_a_file_so_the_group_stays_in_this_shell(self):
        self.assertIn("mktemp", self.result)
        self.assertRegex(self.result, r'\}\s*>\s*"\$__ct"\s*2>&1')

    def test_cleans_up_after_itself(self):
        self.assertIn('rm -f "$__ct"', self.result)

    def test_a_multi_line_command_is_captured_as_a_whole(self):
        # Without the group, only the final line would be redirected.
        result = wrapped("cd /repo\npnpm test", tool="Bash")
        self.assertIn("cd /repo\npnpm test\n}", result)


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
