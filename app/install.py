#!/usr/bin/env python3
"""Put the mmbox console in this user's application menu, or take it out (`--uninstall`, or app/uninstall.py).

    python3 app/install.py              Linux: an entry in the applications menu (and its icon)
    py -3 app\\install.py               Windows: a Start menu shortcut (pythonw: no console window)
    python3 app/install.py --uninstall  the same, removed
    python3 app/install.py --check      says whether it is there, changes nothing

The entry runs the console with `--open`: its window comes to the front, and the computer's daemon (its butler)
starts with it unless Settings say otherwise; a second launch only brings the running window to the front.
Nothing is installed system-wide and no administrator right is needed. It is done again (updated) when the
console or Python moved since.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
CONSOLE = APP / "mmbox_console.py"
NAME = "mmbox console"
ID = "mmbox-console"  # the window's class (GLib prgname) and the entry's file name


def python_gui() -> str:
    """The interpreter the entry runs: pythonw on Windows (no console window), this one elsewhere."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").is_file():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


# ---------------------------------------------------------------- Linux (freedesktop: GNOME, KDE, XFCE…)
def linux_paths() -> dict:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return {"desktop": data / "applications" / f"{ID}.desktop",
            "svg": data / "icons" / "hicolor" / "scalable" / "apps" / f"{ID}.svg",
            "png": data / "icons" / "hicolor" / "256x256" / "apps" / f"{ID}.png"}


def linux_entry() -> str:
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name={NAME}
GenericName=Agents' mailbox console
Comment=The operator's console of mmbox: messages, boxes, questions, the butler
Exec="{python_gui()}" "{CONSOLE}" --open
Path={APP}
Icon={ID}
Terminal=false
Categories=Network;
Keywords=mmbox;agents;mailbox;butler;
StartupNotify=true
StartupWMClass={ID}
"""


def linux_install(check: bool) -> int:
    p, want = linux_paths(), linux_entry()
    have = p["desktop"].read_text(encoding="utf-8") if p["desktop"].is_file() else None
    if have == want and p["svg"].is_file():
        print(f"already installed: {p['desktop']}")
        return 0
    if check:
        print("not installed" if have is None else f"installed but out of date (the console or Python moved): {p['desktop']}")
        return 1
    for k in ("desktop", "svg", "png"):
        p[k].parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APP / "static" / "icon.svg", p["svg"])
    if (APP / "static" / "icon-256.png").is_file():
        shutil.copyfile(APP / "static" / "icon-256.png", p["png"])
    p["desktop"].write_text(want, encoding="utf-8")
    p["desktop"].chmod(0o755)
    refresh_linux_caches(p)
    print(("updated" if have else "installed") + f": {p['desktop']} (in the applications menu as \"{NAME}\")")
    return 0


def linux_uninstall(check: bool) -> int:
    p = linux_paths()
    there = [x for x in p.values() if x.exists()]
    if check:
        print("installed" if p["desktop"].is_file() else "not installed")
        return 0 if p["desktop"].is_file() else 1
    for x in there:
        x.unlink()
    refresh_linux_caches(p)
    print(f"removed: {', '.join(map(str, there))}" if there else "nothing to remove: not installed")
    return 0


def refresh_linux_caches(p: dict) -> None:
    for cmd in (["update-desktop-database", "-q", str(p["desktop"].parent)],
                ["gtk-update-icon-cache", "-q", "-t", str(p["svg"].parents[2])]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- Windows (Start menu)
def windows_link() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / f"{NAME}.lnk"


def powershell(script: str) -> tuple[int, str]:
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, creationflags=0x08000000)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def windows_install(check: bool) -> int:
    link, target, args = windows_link(), python_gui(), f'"{CONSOLE}" --open'
    icon = APP / "static" / "icon.ico"
    code, out = powershell(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(link)});"
                           f"Write-Output ($s.TargetPath + '|' + $s.Arguments)") if link.is_file() else (1, "")
    if code == 0 and out.strip() == f"{target}|{args}":
        print(f"already installed: {link}")
        return 0
    if check:
        print("not installed" if not link.is_file() else f"installed but out of date (the console or Python moved): {link}")
        return 1
    link.parent.mkdir(parents=True, exist_ok=True)
    code, out = powershell(
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(link)});"
        f"$s.TargetPath={ps_quote(target)}; $s.Arguments={ps_quote(args)}; $s.WorkingDirectory={ps_quote(APP)};"
        f"$s.Description={ps_quote('The operator console of mmbox')};"
        + (f"$s.IconLocation={ps_quote(str(icon) + ',0')};" if icon.is_file() else "") + "$s.Save()")
    if code != 0:
        print(f"failed: {out.strip()[:500]}", file=sys.stderr)
        return 1
    print(f"installed: {link} (in the Start menu as \"{NAME}\")")
    return 0


def windows_uninstall(check: bool) -> int:
    link = windows_link()
    if check:
        print("installed" if link.is_file() else "not installed")
        return 0 if link.is_file() else 1
    if link.is_file():
        link.unlink()
        print(f"removed: {link}")
    else:
        print("nothing to remove: not installed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--uninstall", action="store_true", help="take the entry out of the applications menu")
    ap.add_argument("--check", action="store_true", help="say whether it is there (exit 0) or not (exit 1); change nothing")
    a = ap.parse_args(argv)
    if not CONSOLE.is_file():
        print(f"{CONSOLE}: not found (run this script from the mmbox repository's app folder)", file=sys.stderr)
        return 2
    if os.name == "nt":
        return windows_uninstall(a.check) if a.uninstall else windows_install(a.check)
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        return linux_uninstall(a.check) if a.uninstall else linux_install(a.check)
    print(f"{sys.platform}: not supported yet (Linux and Windows are); run `python3 {CONSOLE} --open`", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
