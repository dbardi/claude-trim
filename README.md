# claude-trim

A Claude Code hook that stops noisy command output from filling your context.

Installs, builds and test runs print thousands of lines and say almost
nothing. Every one of those lines is read back on every subsequent turn of the
session, so a single verbose `pnpm test` is charged again and again.

`claude-trim` rewrites those commands *before they run* so only the lines that
matter come back: errors, failures, and the final summary.

Measured on a real monorepo test run:

| | Lines | Bytes | Approx. tokens |
| --- | ---: | ---: | ---: |
| Before | 10,716 | 852,427 | ~213,000 |
| After | 157 | 8,015 | ~2,000 |

The failure list, the root cause and the `Tests:` summary all survived.

## Requirements

- Claude Code
- PowerShell (this hook matches the **PowerShell** tool only — see Scope)
- Python 3.8+

## Install

```
git clone <this repo>
cd claude-trim
python install.py
```

That copies two scripts into `~/.claude` and merges a `PreToolUse` hook into
`~/.claude/settings.json`. Existing settings are preserved — the installer
merges, and copies the file to `settings.json.bak` first (`--no-backup` to
skip). Use `--dry-run` to see the change without writing it.

If the hook does not fire straight away, open `/hooks` once or start a new
session. Claude Code only watches directories that already had a settings
file when the session started.

## What it catches

`pnpm` / `npm` / `yarn` install, build, test, lint, typecheck · `dotnet
build`/`test`/`restore`/`publish` · `msbuild` · `jest` · `tsc` · `eslint` ·
`vitest` · `playwright` · `gradlew` · `mvn` · `pip install` · `cargo` ·
`go build`/`test` · `make`

Matched at the start of any line, or after a `;`, `&&` or `|`. Both
`cd apps/web; pnpm build` and

```powershell
Set-Location C:/repo
pnpm build
```

are caught — multi-statement commands are the normal case, not the exception.

## What it leaves alone

Everything else, and deliberately these:

- `--help`, `--version`, `-v`, `--list` — the output *is* the answer
- `--watch`, `--watchAll`, `dev`, `serve`, `start` — long-running or
  interactive; piping them would hang the call
- anything already redirecting to a file
- short output. Under 60 lines passes through whole, untouched

## What survives the filter

- error, failure, exception and warning lines
- TypeScript diagnostics (`TS2322`), jest failure bullets, cross marks
- `Tests:` / `Suites:` / `Snapshots:` summaries
- the last 25 lines, always
- stack frames in **your** source

Dropped: progress bars and spinners, download and resolver chatter, blank
lines, `PASS`/`✓` lines when trimming, and stack frames inside
`node_modules` — those are the frames that bury the one line naming the
actual error.

Every trim ends with a note saying how much was hidden, so nothing disappears
silently:

```
[trim-output: 113 of 138 lines hidden; re-run without the pipe for full output]
```

## Tuning

Both files are plain Python, installed at `~/.claude/`. Adding a build tool is
one line in the `NOISY` list in `hook-trim.py`. The thresholds live at the top
of `trim-output.py`:

| Constant | Default | Meaning |
| --- | ---: | --- |
| `KEEP_ALL_UNDER` | 60 | below this many lines, pass everything through |
| `TAIL_LINES` | 25 | trailing lines always kept |
| `MAX_SIGNAL` | 80 | cap on promoted error lines |

## How it works

The hook returns a rewritten command through
`hookSpecificOutput.updatedInput`:

```powershell
$o = & { <your command> } 2>&1 | Out-String -Stream
$c = $LASTEXITCODE
if (Test-Path "$HOME/.claude/trim-output.py") { $o | & "<python>" "..." } else { $o }
exit ($c ?? 0)
```

Three details that are load-bearing:

- **Output is captured before filtering.** PowerShell has no `set -o
  pipefail`; piping straight into the filter would make `$LASTEXITCODE` the
  filter's, and a failed build would report success.
- **The interpreter is an absolute path**, taken from the interpreter already
  running the hook. `python` is not on `PATH` everywhere.
- **`Test-Path` guards the filter.** If it is missing you get full output, not
  an error.

## Scope

PowerShell only. There is no Bash branch: the rewrite is PowerShell syntax,
and a hook that matched both shells would need two of them.

## Uninstall

```
python uninstall.py
```

Removes the hook from `settings.json` and deletes the scripts. Other settings
and other hooks are left exactly as they were; `--keep-scripts` unhooks
without deleting. There is a round-trip test asserting that install followed
by uninstall returns `settings.json` to its original contents.

## Tests

```
python -m unittest discover -s tests
```

56 tests, no dependencies beyond the standard library. They cover what gets
wrapped, what must *not* get wrapped, the shape of the rewrite, the hook's
JSON protocol, what the filter keeps and drops, and — most importantly — that
merging into `settings.json` never disturbs anything else in it.

Two of them exist because of bugs that were expensive to find, and both are
the same failure mode: **a hook that silently does nothing looks exactly like
a hook that is not installed.** One was a build tool on the second line of a
multi-statement command never matching; the other was `str.splitlines()`
breaking on `\r`, which multiplied the progress bars this tool exists to
suppress instead of collapsing them.
