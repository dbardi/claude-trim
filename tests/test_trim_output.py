"""What the filter keeps and what it throws away.

The contract is narrow: errors, failures and the final summary must survive
whatever else goes. A filter that hides the one line explaining a build
failure is worse than no filter, so most of these tests are about what
must NOT be lost.
"""
import io
import pathlib
import tempfile
import types
import unittest

from loader import load

trim = load("trim-output.py")

BACKSLASH = chr(92)
TRIM_NOTE = "[trim-output:"


def run_filter(text):
    """Drive main() the way the shell does - bytes in, bytes out."""
    captured = io.BytesIO()
    fake = types.SimpleNamespace(
        stdin=types.SimpleNamespace(buffer=io.BytesIO(text.encode("utf-8"))),
        stdout=types.SimpleNamespace(buffer=captured))
    real, trim.sys = trim.sys, fake
    try:
        trim.main()
    finally:
        trim.sys = real
    return captured.getvalue().decode("utf-8")


def many_lines(filler="step", count=100):
    return [f"{filler} {i}" for i in range(count)]


class ShortOutputIsLeftAlone(unittest.TestCase):

    def test_every_line_survives(self):
        result = run_filter("one\ntwo\nthree")
        self.assertEqual(["one", "two", "three"], result.strip().split("\n"))

    def test_no_trim_note_is_added(self):
        self.assertNotIn(TRIM_NOTE, run_filter("one\ntwo\nthree"))

    def test_successful_steps_are_kept_when_there_is_room(self):
        result = run_filter("PASS suite a\nPASS suite b")
        self.assertIn("PASS suite a", result)


class LongOutputIsTrimmed(unittest.TestCase):

    def setUp(self):
        lines = many_lines()
        lines[4] = "ERROR: the root cause"
        self.result = run_filter("\n".join(lines))

    def test_a_note_reports_what_was_hidden(self):
        self.assertIn(TRIM_NOTE, self.result)

    def test_the_error_is_promoted_out_of_the_middle(self):
        self.assertIn("ERROR: the root cause", self.result)

    def test_the_final_summary_survives(self):
        self.assertIn("step 99", self.result)

    def test_a_gap_marker_shows_where_lines_were_cut(self):
        self.assertIn("lines omitted", self.result)

    def test_the_bulk_of_the_middle_is_gone(self):
        self.assertNotIn("step 40", self.result)


class SuccessfulStepsAreDroppedOnlyWhenTrimming(unittest.TestCase):

    def test_a_wall_of_passes_collapses_to_the_failure(self):
        lines = ["PASS  suite %d" % i for i in range(100)]
        lines[4] = "FAIL  suite 4"
        result = run_filter("\n".join(lines))
        self.assertIn("FAIL  suite 4", result)
        self.assertNotIn("PASS  suite 50", result)


class NoiseIsDropped(unittest.TestCase):

    def dropped(self, line):
        return line not in run_filter("keep me\n%s\nkeep me too" % line)

    def test_library_stack_frames(self):
        self.assertTrue(self.dropped(
            "      at Parser.parse (node_modules/@babel/parser/lib/index.js:1:1)"))

    def test_library_stack_frames_on_windows_paths(self):
        frame = ("      at Parser.parse (C:%snode_modules%sbabel%sindex.js:1:1)"
                 % (BACKSLASH, BACKSLASH, BACKSLASH))
        self.assertTrue(self.dropped(frame))

    def test_download_and_progress_chatter(self):
        for line in ["Downloading react-native", "Progress: resolved 900",
                     "Packages: +122", "added 40 packages"]:
            with self.subTest(line=line):
                self.assertTrue(self.dropped(line))

    def test_blank_lines(self):
        self.assertEqual(["a", "b"], run_filter("a\n\n\n\nb").strip().split("\n"))


class SignalIsKept(unittest.TestCase):

    def kept(self, line):
        return line in run_filter("noise\n%s\nnoise" % line)

    def test_stack_frames_in_your_own_source(self):
        self.assertTrue(self.kept(
            "      at handler (apps/web/src/route.ts:12:3)"))

    def test_a_directory_that_merely_looks_like_node_modules(self):
        self.assertTrue(self.kept("      at x (src/mynode_modules/a.ts:1:1)"))

    def test_typescript_diagnostics(self):
        self.assertTrue(self.kept("src/a.ts(3,9): error TS2322: not assignable"))

    def test_the_test_summary(self):
        self.assertTrue(self.kept("Tests:       3 failed, 8 passed"))


def run_filter_on(capture, status):
    """Drive main() the way the Bash rewrite does - a capture file and the
    command's outcome as arguments. Returns the output and the exit code."""
    captured = io.BytesIO()
    fake = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=captured))
    real, trim.sys = trim.sys, fake
    code = 0
    try:
        trim.main([str(capture), status])
    except SystemExit as exit_:
        code = exit_.code
    finally:
        trim.sys = real
    return captured.getvalue().decode("utf-8"), code


class ReadingACaptureFile(unittest.TestCase):
    """The Bash rewrite hands over a file rather than a pipe, so it needs no
    shell expansion Claude Code would have to ask about."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.capture = pathlib.Path(self.directory.name) / "capture.out"
        self.capture.write_text("one\ntwo", encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_the_output_is_read_from_the_file(self):
        output, _ = run_filter_on(self.capture, "ok")
        self.assertEqual(["one", "two"], output.strip().split("\n"))

    def test_the_file_is_removed_once_read(self):
        run_filter_on(self.capture, "ok")
        self.assertFalse(self.capture.exists())

    def test_a_command_that_succeeded_exits_cleanly(self):
        _, code = run_filter_on(self.capture, "ok")
        self.assertIn(code, (0, None))

    def test_a_command_that_failed_is_reported_as_a_failure(self):
        _, code = run_filter_on(self.capture, "failed")
        self.assertEqual(1, code)


class TerminalControlCharacters(unittest.TestCase):

    def test_ansi_color_codes_are_stripped(self):
        result = run_filter("\x1b[31mERROR: red\x1b[0m")
        self.assertIn("ERROR: red", result)
        self.assertNotIn("\x1b", result)

    def test_carriage_return_overwrites_keep_only_the_final_state(self):
        result = run_filter("Building 10%\rBuilding 90%\rBuilding done")
        self.assertEqual("Building done", result.strip())

    def test_characters_the_windows_console_cannot_encode_survive(self):
        # cp1252 cannot encode U+25CF; writing via the byte stream can.
        self.assertIn("●", run_filter("  ● Test suite failed to run"))


if __name__ == "__main__":
    unittest.main()
