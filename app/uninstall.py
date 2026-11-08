#!/usr/bin/env python3
"""Take the mmbox console out of this user's application menu (Linux) or Start menu (Windows): app/install.py
--uninstall. The console's data (its operator key, its archive) and the computer's boxes are not touched; the
daemon's service, if one was installed, is removed by `mmbox.py daemon uninstall`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install  # noqa: E402

if __name__ == "__main__":
    sys.exit(install.main(["--uninstall", *sys.argv[1:]]))
