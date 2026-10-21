#!/usr/bin/env python3
"""Starts jira-dc-cli from a source checkout or from an installation folder.

The package lives in `src/` in a checkout and next to this file once installed.
"""
import os
import sys

if sys.version_info < (3, 10):
    sys.exit(f"jira-dc-cli needs Python 3.10 or newer; this is Python {sys.version.split()[0]}.")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "src")
sys.path.insert(0, SOURCE if os.path.isdir(os.path.join(SOURCE, "jira_dc_cli")) else HERE)

from jira_dc_cli.bootstrap import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
