"""Remove the output-trimming hook from Claude Code.

    python uninstall.py [--target DIR] [--dry-run] [--keep-scripts]

Takes the hook out of settings.json and deletes the two scripts. Every other
setting is left exactly as it was.
"""
import argparse
import json
import pathlib

from install import (MARKER, SOURCES, default_target, read_settings,
                     remove_hook, write_settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=pathlib.Path, default=default_target())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-scripts", action="store_true",
                        help="unhook it but leave the scripts in place")
    args = parser.parse_args()

    settings = args.target / "settings.json"
    before = read_settings(settings)
    after = remove_hook(before)

    if before == after:
        print(f"no {MARKER} hook found in {settings}")
    else:
        print(f"  unhook    {settings.name}")
    if not args.keep_scripts:
        for name in SOURCES:
            if (args.target / name).exists():
                print(f"  delete    {name}")

    if args.dry_run:
        print("\n-- dry run, nothing written --")
        return

    write_settings(settings, after, backup=False)
    if not args.keep_scripts:
        for name in SOURCES:
            (args.target / name).unlink(missing_ok=True)
    print("\nremoved.")


if __name__ == "__main__":
    main()
