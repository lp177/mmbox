"""The mmbox daemon's icon in the notification area, and its window (`mmbox.py daemon tray`, `mmbox.py daemon window`).

The daemon starts its icon when this computer has a desktop and no operator console with its own icon runs (the
console then shows the same actions in its menu); the icon quits by itself when such a console comes. A click (Linux:
the left button; Windows: the left button) opens the window; the right button shows the menu: the daemon's state,
Information..., Start, Stop, Restart, Check for updates (Update to ... when the client's clone is behind its origin),
Quit (stops the daemon and removes the icon until it starts again).

- Linux: a StatusNotifierItem of its own on the session bus, its menu exported with Dbusmenu (KDE Plasma, GNOME with
  the AppIndicator extension, most panels): PyGObject and gir1.2-dbusmenu-glib-0.4, as the console's icon.
- Windows: Shell_NotifyIcon through ctypes, a hidden window and its message loop: nothing to install.
- The window: tkinter (bundled with Python on Windows; Debian: python3-tk), a process of its own, one at a time (a
  second click brings it to the front).

Loaded by mmbox_daemon.main as `mmbox_tray.main(daemon_module, "tray" | "window")`. Standard library only (plus
PyGObject on Linux for the icon)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

B = None  # mmbox_daemon, given by main()
TICK_S = 3
APP = "mmbox daemon"


# ---------------------------------------------------------------- shared: state, actions, files
def home() -> Path:
    return B.home()


def light() -> dict:
    return B.control_state()


def code_files() -> list[Path]:
    d = Path(B.client_file()).parent
    return [d / "mmbox.py", d / "mmbox_daemon.py", d / "mmbox_tray.py"]


def code_sig() -> tuple:
    out = []
    for p in code_files():
        try:
            st = p.stat()
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def static_file(name: str) -> Path | None:
    """A file of the console's static folder (the logo), when the client is a full clone."""
    p = Path(B.client_file()).parent.parent / "app" / "static" / name
    return p if p.is_file() else None


class Single:
    """One process of a kind on this computer (`tray`, `window`): an OS lock on <daemon folder>/<kind>.lock, and
    <kind>.json saying which process holds it."""

    def __init__(self, kind: str):
        self.kind = kind
        self.f = open(home() / f"{kind}.lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.got = True
        except OSError:
            self.got = False
            self.f.close()
            return
        B.jsave(f"{kind}.json", {"pid": os.getpid(), "start": B.M.proc_start(os.getpid()), "since": time.time()})

    def end(self, reason: str) -> None:
        if not self.got:
            return
        B.jsave(f"{self.kind}.json", {**B.jload(f"{self.kind}.json", {}), "pid": None, "ended": reason, "at": time.time()})
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            self.f.close()
        except OSError:
            pass
        self.got = False


def window_pid() -> int | None:
    w = B.jload("window.json", {})
    pid = int(w.get("pid") or 0)
    return pid if pid and B.M.pid_alive(pid) and (not w.get("start") or B.M.proc_start(pid) == w["start"]) else None


def open_window() -> None:
    """The daemon's window: brought to the front if it is open, else started."""
    if window_pid():
        (home() / "window.raise").touch()
        return
    B.spawn("daemon", "window")


def do(key: str) -> dict:
    """One action of the menu or the window, run in a thread of its own (it may take seconds): its result."""
    if key == "up":
        return B.up()
    if key == "down":
        return B.down()
    if key == "restart":
        return B.restart()
    if key in ("check", "update"):
        r = B.update(apply=key == "update", who="mmbox tray")
        if r.get("error"):
            return {"ok": False, "err": r["error"]}
        if r.get("updated"):
            return {"ok": True, "out": f"updated to {(r.get('current') or {}).get('commit')}"
                                       + ("; the daemon restarted" if (r.get("restart") or {}).get("ok") else "")}
        n = len(r.get("behind") or [])
        return {"ok": True, "out": f"{n} update(s) available: {r['behind'][0]}" if n else "the client is up to date"}
    if key == "quit":
        r = B.down("quit")
        (home() / "tray.quit").touch()
        return r
    return {"ok": False, "err": f"unknown action {key}"}


def menu_model(st: dict, busy: str | None) -> list[tuple[str, str, bool]]:
    """(key, label, enabled) of the icon's menu; key "sep": a separator; "title": the state, never clickable."""
    run = st["running"]
    title = (f"mmbox daemon: {busy}..." if busy else
             f"mmbox daemon: running (pid {st['pid']})" if run else "mmbox daemon: stopped")
    upd = f"Update to {st['update'].get('latest') or 'the latest'} and restart" if st["behind"] else "Check for updates"
    free = busy is None
    return [("title", title, False), ("sep", "", False), ("info", "Information...", True), ("sep", "", False),
            ("up", "Start", free and not run), ("down", "Stop", free and run), ("restart", "Restart", free and run),
            ("update" if st["behind"] else "check", upd, free), ("sep", "", False),
            ("quit", "Quit (stop the daemon)", free)]


BUSY = {"up": "starting", "down": "stopping", "restart": "restarting", "check": "checking for updates",
        "update": "updating", "quit": "stopping"}


# ---------------------------------------------------------------- the icon
class Tray:
    """What the icon does on every system: its tick (state, a console that came, a new client on disk, Quit), its
    actions in threads; the system's class draws it (LinuxTray, WinTray)."""

    def __init__(self, single: Single):
        self.single, self.busy, self.sig, self.sig_at, self.st = single, None, code_sig(), None, light()
        self.reexec = False

    # the system's class: build(), redraw(), notify(title, body), run(), stop(), call_soon(fn)
    def tick(self) -> None:
        q = home() / "tray.quit"
        if q.exists():
            q.unlink(missing_ok=True)
            return self.stop("quit")
        if B.console_has_tray():
            B.log("daemon", "a console with its own icon runs: the daemon's icon quits")
            return self.stop("console")
        sig = code_sig()
        if sig != self.sig and self.busy is None:  # a new client on disk (an update): this icon starts again on it
            if self.sig_at is None or sig != self.sig_at[0]:
                self.sig_at = (sig, time.monotonic())
            elif time.monotonic() - self.sig_at[1] > 3:
                self.reexec = True
                return self.stop("new client")
        self.st = light()
        self.redraw()

    def act(self, key: str) -> None:
        if key == "info":
            open_window()
            return
        if key == "title" or self.busy:
            return
        self.busy = BUSY.get(key, key)
        self.redraw()

        def work():
            try:
                r = do(key)
            except Exception as e:  # noqa: BLE001 - shown, never fatal
                r = {"ok": False, "err": f"{type(e).__name__}: {e}"}

            def done():
                self.busy = None
                self.notify(APP, r.get("out") if r.get("ok") else "Failed: " + str(r.get("err") or "?"))
                self.tick()
            self.call_soon(done)
        threading.Thread(target=work, daemon=True, name=f"tray-{key}").start()

    def tooltip(self) -> str:
        st = self.st
        s = f"mmbox daemon: {self.busy}..." if self.busy else \
            f"mmbox daemon: running (pid {st['pid']})" if st["running"] else "mmbox daemon: stopped"
        return s + (f"\n{st['behind']} update(s) available" if st["behind"] else "")

    def finish(self, reason: str) -> None:
        self.single.end(reason)
        if self.reexec:
            args = [sys.executable, B.client_file(), "daemon", "tray"]
            if os.name == "nt":
                B.spawn("daemon", "tray")
            else:
                os.execv(sys.executable, args)


SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
 <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
 <property name="WindowId" type="i" access="read"/><property name="IconThemePath" type="s" access="read"/>
 <property name="IconName" type="s" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
 <property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
 <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
 <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
 <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

FALLBACK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
                'fill="#123a32"/><path d="M14 22h36v22H14z" fill="none" stroke="#e2c27a" stroke-width="5" '
                'stroke-linejoin="round"/><path d="M14 22l18 13 18-13" fill="none" stroke="#e2c27a" stroke-width="5" '
                'stroke-linejoin="round"/></svg>')


def tray_svg(colour: str) -> str:
    """The logo (static/icon-small.svg, the console's own), with a dot saying the daemon's state."""
    p = static_file("icon-small.svg")
    svg = p.read_text(encoding="utf-8").rstrip() if p else FALLBACK_SVG
    return svg[:-len("</svg>")] + f'<circle cx="51" cy="51" r="11" fill="{colour}" stroke="#121212" stroke-width="3"/></svg>'


class LinuxTray(Tray):
    PATH, IFACE = "/StatusNotifierItem", "org.kde.StatusNotifierItem"

    def build(self) -> None:
        import gi
        gi.require_version("Dbusmenu", "0.4")
        from gi.repository import Dbusmenu, Gio, GLib
        self.D, self.Gio, self.GLib = Dbusmenu, Gio, GLib
        self.icons = home() / "icons"
        self.icons.mkdir(parents=True, exist_ok=True)
        for name, colour in (("mmbox-daemon", "#5bc489"), ("mmbox-daemon-stopped", "#8a8f98"),
                             ("mmbox-daemon-busy", "#e2c27a")):
            (self.icons / f"{name}.svg").write_text(tray_svg(colour), encoding="utf-8")
        self.icon, self.tip = "mmbox-daemon-stopped", ""
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.server = Dbusmenu.Server.new("/MenuBar")
        root = Dbusmenu.Menuitem.new()
        self.items, self.keys = {}, [k for k, _, _ in menu_model(self.st, None)]
        for i, key in enumerate(self.keys):  # the separators keep their places: only labels and keys change
            it = Dbusmenu.Menuitem.new()
            if key == "sep":
                it.property_set(Dbusmenu.MENUITEM_PROP_TYPE, Dbusmenu.CLIENT_TYPES_SEPARATOR)
            else:
                it.connect("item-activated", lambda *_, i=i: self.act(self.keys[i]))
            root.child_append(it)
            self.items[i] = it
        self.server.set_root(root)
        iface = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self.reg = self.bus.register_object(self.PATH, iface, self._call, self._get, None)
        Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, self._owned, None)
        self.loop = GLib.MainLoop()
        GLib.timeout_add_seconds(TICK_S, lambda: (self.tick(), True)[1])
        self.redraw()

    def _owned(self, *_):
        """Registered with the panel's watcher, and again each time it starts (a restarted plasmashell)."""
        self.Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher",
                                              self.Gio.BusNameWatcherFlags.NONE, self._register, None)

    def _register(self, *_):
        def done(bus, res):
            try:
                bus.call_finish(res)
            except Exception as e:  # noqa: BLE001
                B.log("error", f"tray: {e}")
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                      "RegisterStatusNotifierItem", self.GLib.Variant("(s)", (self.name,)), None,
                      self.Gio.DBusCallFlags.NONE, -1, None, done)

    def _call(self, bus, sender, path, iface, method, params, inv):
        if method == "Activate":  # the left button: the window
            open_window()
        inv.return_value(None)

    def _get(self, bus, sender, path, iface, prop):
        V = self.GLib.Variant
        return {"Category": V("s", "SystemServices"), "Id": V("s", "mmbox-daemon"), "Title": V("s", APP),
                "Status": V("s", "Active"), "WindowId": V("i", 0), "IconThemePath": V("s", str(self.icons)),
                "IconName": V("s", self.icon), "ToolTip": V("(sa(iiay)ss)", (self.icon, [], APP, self.tip)),
                "ItemIsMenu": V("b", False), "Menu": V("o", "/MenuBar")}.get(prop)

    def redraw(self) -> None:
        D = self.D
        for i, (key, label, enabled) in enumerate(menu_model(self.st, self.busy)):
            it = self.items[i]
            self.keys[i] = key
            if key != "sep":
                it.property_set(D.MENUITEM_PROP_LABEL, label)
                it.property_set_bool(D.MENUITEM_PROP_ENABLED, enabled)
        icon = "mmbox-daemon-busy" if self.busy else "mmbox-daemon" if self.st["running"] else "mmbox-daemon-stopped"
        tip = self.tooltip()
        if (icon, tip) != (self.icon, self.tip):
            self.icon, self.tip = icon, tip
            for sig in ("NewIcon", "NewToolTip"):
                self.bus.emit_signal(None, self.PATH, self.IFACE, sig, None)

    def notify(self, title: str, body: str) -> None:
        """A system notification (org.freedesktop.Notifications): no library needed."""
        V = self.GLib.Variant
        self.bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                      "Notify", V("(susssasa{sv}i)", ("mmbox", 0, str(self.icons / "mmbox-daemon.svg"), title,
                                                      body[:300], [], {}, 6000)),
                      None, self.Gio.DBusCallFlags.NONE, -1, None, None)

    def call_soon(self, fn) -> None:
        self.GLib.idle_add(lambda: (fn(), False)[1])

    def run(self) -> None:
        self.loop.run()

    def stop(self, reason: str) -> None:
        self.loop.quit()
        self.finish(reason)


class WinTray(Tray):
    """Shell_NotifyIcon through ctypes: a hidden window receives the icon's mouse messages; its message loop is this
    process's main thread."""

    def build(self) -> None:
        import ctypes
        from ctypes import wintypes as W
        self.C, self.W = ctypes, W
        u32, s32, k32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
        self.u32, self.s32 = u32, s32
        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", W.UINT), ("lpfnWndProc", self.WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                        ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                        ("lpszClassName", W.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                        ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                        ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                        ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                        ("guidItem", GUID), ("hBalloonIcon", W.HICON)]
        self.NID = NOTIFYICONDATAW
        u32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        u32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
        u32.CreateWindowExW.restype = W.HWND
        u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u32.RegisterClassW.restype = W.ATOM
        u32.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
        u32.LoadImageW.restype = W.HANDLE
        u32.LoadIconW.argtypes = [W.HINSTANCE, W.LPVOID]
        u32.LoadIconW.restype = W.HICON
        u32.CreatePopupMenu.restype = W.HMENU
        u32.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        u32.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.LPVOID]
        u32.TrackPopupMenu.restype = ctypes.c_int
        u32.DestroyMenu.argtypes = [W.HMENU]
        u32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
        u32.SetForegroundWindow.argtypes = [W.HWND]
        u32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u32.SetTimer.argtypes = [W.HWND, ctypes.c_size_t, W.UINT, W.LPVOID]
        u32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
        u32.GetMessageW.restype = ctypes.c_int
        u32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
        u32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
        u32.DestroyWindow.argtypes = [W.HWND]
        u32.GetSystemMetrics.argtypes = [ctypes.c_int]
        u32.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        u32.RegisterWindowMessageW.restype = W.UINT
        s32.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        s32.Shell_NotifyIconW.restype = W.BOOL
        k32.GetModuleHandleW.argtypes = [W.LPCWSTR]
        k32.GetModuleHandleW.restype = W.HMODULE
        self.WM_TRAY, self.WM_SOON, self.WM_TIMER, self.WM_DESTROY = 0x0400 + 20, 0x8000 + 1, 0x0113, 0x0002
        self.TASKBAR = u32.RegisterWindowMessageW("TaskbarCreated")
        self.soon: list = []
        self.soon_lock = threading.Lock()
        hinst = k32.GetModuleHandleW(None)
        self._proc = self.WNDPROC(self._wndproc)  # kept: the window calls it for as long as it lives
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._proc, hinst, "mmboxDaemonTray"
        u32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u32.CreateWindowExW(0, "mmboxDaemonTray", APP, 0, 0, 0, 0, 0, None, None, hinst, None)
        size = u32.GetSystemMetrics(49) or 16  # SM_CXSMICON: the notification area's size, at this DPI
        ico = static_file("icon.ico")
        self.hicon = (u32.LoadImageW(None, str(ico), 1, size, size, 0x10) if ico else None) or u32.LoadIconW(None, 32512)
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize, self.nid.hWnd, self.nid.uID = ctypes.sizeof(NOTIFYICONDATAW), self.hwnd, 1
        self.nid.uFlags, self.nid.uCallbackMessage, self.nid.hIcon = 0x1 | 0x2 | 0x4, self.WM_TRAY, self.hicon
        self.nid.szTip = self.tooltip()[:127]
        s32.Shell_NotifyIconW(0, ctypes.byref(self.nid))  # NIM_ADD
        u32.SetTimer(self.hwnd, 1, TICK_S * 1000, None)
        self.tip = None
        self.redraw()

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == self.WM_TRAY:
                if lp == 0x0202:  # WM_LBUTTONUP: the window
                    open_window()
                elif lp in (0x0205, 0x007B):  # WM_RBUTTONUP, WM_CONTEXTMENU: the menu
                    self._menu()
                return 0
            if msg == self.WM_TIMER:
                self.tick()
                return 0
            if msg == self.WM_SOON:
                with self.soon_lock:
                    fns, self.soon = self.soon, []
                for fn in fns:
                    fn()
                return 0
            if msg == self.TASKBAR:  # Explorer restarted: the icon is added again
                self.s32.Shell_NotifyIconW(0, self.C.byref(self.nid))
                return 0
            if msg == self.WM_DESTROY:
                self.s32.Shell_NotifyIconW(2, self.C.byref(self.nid))  # NIM_DELETE
                self.u32.PostQuitMessage(0)
                return 0
        except Exception as e:  # noqa: BLE001 - never through the window procedure
            B.log("error", f"tray: {type(e).__name__}: {e}")
        return self.u32.DefWindowProcW(hwnd, msg, wp, lp)

    def _menu(self) -> None:
        u32, W = self.u32, self.W
        menu = u32.CreatePopupMenu()
        model = menu_model(self.st, self.busy)
        for i, (key, label, enabled) in enumerate(model):
            if key == "sep":
                u32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
            else:
                u32.AppendMenuW(menu, 0 if enabled else 0x1, i + 1, label)  # MF_STRING, MF_GRAYED
        pt = W.POINT()
        u32.GetCursorPos(self.C.byref(pt))
        u32.SetForegroundWindow(self.hwnd)  # or the menu never closes when clicked elsewhere
        cmd = u32.TrackPopupMenu(menu, 0x0100 | 0x0080 | 0x0002, pt.x, pt.y, 0, self.hwnd, None)  # RETURNCMD
        u32.PostMessageW(self.hwnd, 0, 0, 0)
        u32.DestroyMenu(menu)
        if cmd:
            self.act(model[cmd - 1][0])

    def redraw(self) -> None:
        tip = self.tooltip()[:127]
        if tip != self.tip:
            self.tip = tip
            self.nid.szTip = tip
            self.nid.uFlags = 0x1 | 0x2 | 0x4
            self.s32.Shell_NotifyIconW(1, self.C.byref(self.nid))  # NIM_MODIFY

    def notify(self, title: str, body: str) -> None:
        self.nid.uFlags = 0x10  # NIF_INFO: a balloon (a toast on Windows 10 and 11)
        self.nid.szInfoTitle, self.nid.szInfo, self.nid.dwInfoFlags = title[:63], body[:255], 0x1
        self.s32.Shell_NotifyIconW(1, self.C.byref(self.nid))
        self.nid.uFlags = 0x1 | 0x2 | 0x4

    def call_soon(self, fn) -> None:
        with self.soon_lock:
            self.soon.append(fn)
        self.u32.PostMessageW(self.hwnd, self.WM_SOON, 0, 0)

    def run(self) -> None:
        msg = self.W.MSG()
        while self.u32.GetMessageW(self.C.byref(msg), None, 0, 0) > 0:
            self.u32.TranslateMessage(self.C.byref(msg))
            self.u32.DispatchMessageW(self.C.byref(msg))

    def stop(self, reason: str) -> None:
        self.u32.DestroyWindow(self.hwnd)  # WM_DESTROY: the icon removed, the loop ends
        self.finish(reason)


def run_tray() -> int:
    os.environ.update({k: v for k, v in B.desktop_env().items() if not os.environ.get(k)})
    single = Single("tray")
    if not single.got:
        return 0  # one runs already
    kind = WinTray if os.name == "nt" else LinuxTray if sys.platform.startswith("linux") else None
    if kind is None:
        single.end("failed")
        return 0
    t = kind(single)
    try:
        t.build()
    except Exception as e:  # noqa: BLE001 - no desktop, no Dbusmenu: said once, the daemon tries again later
        B.log("error", f"the daemon's icon cannot show: {type(e).__name__}: {e} (Linux: python3-gi, "
                       f"gir1.2-dbusmenu-glib-0.4)")
        single.end("failed")
        return 1
    B.log("daemon", f"its icon in the notification area shows (pid {os.getpid()})")
    t.run()
    return 0


# ---------------------------------------------------------------- the window
BG, SURFACE, ON, MUTED, LINE = "#121212", "#1e1f22", "#e8eaed", "#a6abb3", "#33363b"
PRIMARY, ON_PRIMARY, DANGER, OK_C, WARN = "#e2c27a", "#1c1606", "#f28b82", "#81c995", "#fdd663"


def reduced_motion() -> bool:
    """The system asks for less motion (Windows: client area animations off; GNOME: animations off; KDE: animation
    speed at its fastest)."""
    try:
        if os.name == "nt":
            import ctypes
            v = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)
            return not v.value
        kde = Path.home() / ".config" / "kdeglobals"
        if kde.is_file() and "AnimationDurationFactor=0\n" in kde.read_text(encoding="utf-8", errors="ignore"):
            return True
        import shutil
        import subprocess
        if shutil.which("gsettings"):
            r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                               capture_output=True, text=True, timeout=3)
            return r.stdout.strip() == "false"
    except Exception:  # noqa: BLE001
        pass
    return False


def ago(t: float | None) -> str:
    if not t:
        return "-"
    s = max(0, int(time.time() - float(t)))
    return f"{s} s" if s < 90 else f"{s // 60} min" if s < 5400 else f"{s // 3600} h {s % 3600 // 60:02d}"


def run_window() -> int:
    os.environ.update({k: v for k, v in B.desktop_env().items() if not os.environ.get(k)})
    single = Single("window")
    if not single.got:
        (home() / "window.raise").touch()
        return 0
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on a scaled screen
        except Exception:  # noqa: BLE001
            pass
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except ImportError:
        B.log("error", "the daemon's window needs tkinter (Debian: apt install python3-tk)")
        single.end("failed")
        return 1
    try:
        Window(tk, tkfont, single).run()
    finally:
        single.end("closed")
    return 0


class RippleButton:
    """A button drawn on a canvas, after Material's: hover, focus ring, pressed, disabled; a ripple from the pointer (or
    from the centre for the keyboard), none when the system asks for less motion. Enter and Space press it."""

    def __init__(self, win, parent, text: str, command, kind: str = "outlined"):
        tk = win.tk
        self.win, self.text, self.command, self.kind, self.enabled, self.hover = win, text, command, kind, True, False
        f = win.f_button
        w, h = f.measure(text) + 32, 36
        self.c = tk.Canvas(parent, width=w, height=h, bg=SURFACE, highlightthickness=0, bd=0, takefocus=1,
                           cursor="hand2")
        self.w, self.h, self.focus, self.ripple = w, h, False, None
        self.c.bind("<Enter>", lambda e: self._set(hover=True))
        self.c.bind("<Leave>", lambda e: self._set(hover=False))
        self.c.bind("<FocusIn>", lambda e: self._set(focus=True))
        self.c.bind("<FocusOut>", lambda e: self._set(focus=False))
        self.c.bind("<ButtonPress-1>", self._press)
        self.c.bind("<ButtonRelease-1>", self._release)
        for k in ("<Return>", "<KP_Enter>", "<space>"):
            self.c.bind(k, self._key)
        self.draw()

    def colours(self) -> tuple[str, str, str]:
        """(fill, text, ripple)"""
        if not self.enabled:
            return (SURFACE if self.kind != "filled" else "#3a3b3e"), "#6b6f76", SURFACE
        if self.kind == "filled":
            return ("#ecd197" if self.hover else PRIMARY), ON_PRIMARY, "#fff3d6"
        return ("#2a2c30" if self.hover else SURFACE), (DANGER if self.kind == "danger" else PRIMARY), "#45484e"

    def draw(self) -> None:
        c = self.c
        c.delete("all")
        fill, fg, _ = self.colours()
        c.create_rectangle(0, 0, self.w, self.h, fill=fill, outline="")
        if self.ripple:
            x, y, r, col = self.ripple
            c.create_oval(x - r, y - r, x + r, y + r, fill=col, outline="")
        border = "#5f6368" if self.kind != "filled" else fill
        c.create_rectangle(0.5, 0.5, self.w - 1, self.h - 1, outline=border if self.enabled else "#44474c")
        if self.focus and self.enabled:
            c.create_rectangle(2, 2, self.w - 3, self.h - 3, outline=ON, width=2)
        c.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=self.win.f_button)

    def _set(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def set(self, enabled: bool, text: str | None = None) -> None:
        if text and text != self.text:
            self.text = text
            self.w = self.win.f_button.measure(text) + 32
            self.c.configure(width=self.w)
        self.enabled = enabled
        self.c.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        self.draw()

    def _ripple(self, x: float, y: float) -> None:
        if self.win.still:
            return
        _, _, col = self.colours()
        far = max(((x - a) ** 2 + (y - b) ** 2) ** 0.5 for a in (0, self.w) for b in (0, self.h))
        steps = 9

        def frame(i):
            if i > steps:
                self.ripple = None
                self.draw()
                return
            self.ripple = (x, y, far * i / steps, col if i < steps - 2 else self.colours()[0])
            self.draw()
            self.c.after(28, frame, i + 1)
        frame(1)

    def _press(self, e) -> None:
        if self.enabled:
            self.c.focus_set()
            self._ripple(e.x, e.y)

    def _release(self, e) -> None:
        if self.enabled and 0 <= e.x <= self.w and 0 <= e.y <= self.h:
            self.command()

    def _key(self, e) -> str:
        if self.enabled:
            self._ripple(self.w / 2, self.h / 2)
            self.command()
        return "break"


class Window:
    def __init__(self, tk, tkfont, single):
        self.tk, self.single = tk, single
        self.root = root = tk.Tk(className="mmbox-daemon")
        root.title("mmbox daemon")
        root.configure(bg=BG)
        root.resizable(False, False)
        self.still = reduced_motion()
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        self.f_title = tkfont.Font(family=base, size=18, weight="bold")
        self.f_sub = tkfont.Font(family=base, size=10)
        self.f_body = tkfont.Font(family=base, size=10)
        self.f_bold = tkfont.Font(family=base, size=10, weight="bold")
        self.f_button = tkfont.Font(family=base, size=10, weight="bold")
        self.logo = None
        png, ico = static_file("icon-256.png"), static_file("icon.ico")
        try:
            if png:
                self.full = tk.PhotoImage(file=str(png))
                self.logo = self.full.subsample(4)  # 64 px
                root.iconphoto(True, self.full)
            if ico and os.name == "nt":
                root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
        self.busy, self.msg, self.data, self.pending, self.result = None, ("", MUTED), None, None, None
        self.fetching, self.next_fetch, self.closed = False, 0.0, False
        self._ui()
        root.bind("<Escape>", lambda e: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self._poll)

    def _ui(self) -> None:
        tk, root = self.tk, self.root
        pad = {"padx": 20}
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", pady=(18, 10), **pad)
        if self.logo:
            tk.Label(head, image=self.logo, bg=BG).pack(side="left", padx=(0, 14))
        t = tk.Frame(head, bg=BG)
        t.pack(side="left", fill="x")
        tk.Label(t, text="mmbox", font=self.f_title, fg=ON, bg=BG, anchor="w").pack(anchor="w")
        self.l_sub = tk.Label(t, text="The daemon and butler of this computer", font=self.f_sub, fg=MUTED, bg=BG,
                              anchor="w")
        self.l_sub.pack(anchor="w")

        card = tk.Frame(root, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="x", pady=(0, 12), **pad)
        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=(14, 4))
        self.dot = tk.Canvas(row, width=14, height=14, bg=SURFACE, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.l_state = tk.Label(row, text="Reading...", font=self.f_bold, fg=ON, bg=SURFACE, anchor="w")
        self.l_state.pack(side="left")
        self.l_state2 = tk.Label(card, text="", font=self.f_body, fg=MUTED, bg=SURFACE, anchor="w", justify="left",
                                 wraplength=440)
        self.l_state2.pack(fill="x", padx=16, pady=(0, 10))
        grid = tk.Frame(card, bg=SURFACE)
        grid.pack(fill="x", padx=16, pady=(0, 14))
        self.cells = {}
        for i, (key, label) in enumerate((("machine", "Machine box"), ("services", "Services"),
                                          ("boxes", "Boxes"), ("started", "Sessions it started"),
                                          ("agents", "Agents found"), ("service", "Started by"),
                                          ("version", "Client"), ("update", "Updates"))):
            tk.Label(grid, text=label, font=self.f_body, fg=MUTED, bg=SURFACE, anchor="nw").grid(
                row=i, column=0, sticky="nw", padx=(0, 16), pady=2)
            v = tk.Label(grid, text="-", font=self.f_body, fg=ON, bg=SURFACE, anchor="w", justify="left",
                         wraplength=320)
            v.grid(row=i, column=1, sticky="w", pady=2)
            self.cells[key] = v

        self.l_msg = tk.Label(root, text="", font=self.f_body, fg=MUTED, bg=BG, anchor="w", justify="left",
                              wraplength=480)
        self.l_msg.pack(fill="x", pady=(0, 8), **pad)
        bar = tk.Frame(root, bg=SURFACE, highlightthickness=1, highlightbackground=LINE)
        bar.pack(fill="x", pady=(0, 18), **pad)
        inner = tk.Frame(bar, bg=SURFACE)
        inner.pack(padx=10, pady=10)
        self.btn = {}
        for key, text, kind in (("up", "Start", "outlined"), ("down", "Stop", "outlined"),
                                ("restart", "Restart", "outlined"), ("check", "Check for updates", "filled"),
                                ("quit", "Quit", "danger")):
            b = RippleButton(self, inner, text, lambda k=key: self.act(k), kind)
            b.c.pack(side="left", padx=4)
            self.btn[key] = b

    def close(self) -> None:
        self.closed = True
        self.root.destroy()

    # ---- one loop on Tk's thread (Tk is never called from another): the state every 3 s (read in a thread: about()
    # reads files and runs git), an action's result, a second click on the icon
    def _poll(self) -> None:
        if self.closed:
            return
        if self.pending is not None:
            self.data, self.pending, self.fetching = self.pending, None, False
            self._show()
        if self.result is not None:
            key, r = self.result
            self.result = None
            self._done(key, r)
            if self.closed:
                return
        if not self.fetching and self.busy is None and time.monotonic() >= self.next_fetch:
            self.fetching, self.next_fetch = True, time.monotonic() + 3

            def work():
                try:
                    data = B.about()
                except Exception as e:  # noqa: BLE001
                    data = {"error": f"{type(e).__name__}: {e}"}
                self.pending = data
            threading.Thread(target=work, daemon=True).start()
        p = home() / "window.raise"
        if p.exists():
            p.unlink(missing_ok=True)
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        self.root.after(200, self._poll)

    def _show(self) -> None:
        d = self.data or {}
        if d.get("error"):
            self.l_state.configure(text="Cannot read the daemon's state")
            self.l_state2.configure(text=d["error"])
            return
        run = d.get("running")
        self.l_sub.configure(text=f"The daemon and butler of {d.get('host') or 'this computer'}")
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 13, 13, fill=WARN if self.busy else OK_C if run else "#8a8f98", outline="")
        if self.busy:
            self.l_state.configure(text=self.busy[0].upper() + self.busy[1:] + "...")
        else:
            self.l_state.configure(text=f"Running, pid {d['pid']}" if run else
                                   "Stopped" + (" by the operator" if d.get("stopped") else ""))
        ver = d.get("version") or {}
        lines = []
        if run:
            lines.append(f"Running for {ago(d.get('since'))}; last round {ago(d.get('beat'))} ago.")
            if d.get("loaded") and ver.get("commit") and d["loaded"] != ver["commit"]:
                lines.append(f"It runs {d['loaded']}; the client on disk is {ver['commit']}: restart it to load it.")
        else:
            lines.append("No message is taken for this computer's projects while it is stopped." if d.get("box") else
                         "Its butler has no box yet: start it once.")
        if d.get("console"):
            lines.append("The operator console runs on this computer.")
        self.l_state2.configure(text=" ".join(lines))
        b = d.get("boxes") or {}
        svc = d.get("services") or []
        self.cells["machine"].configure(text=d.get("machine") or "none yet")
        self.cells["services"].configure(text="\n".join(svc) if svc else "none")
        self.cells["boxes"].configure(text=f"{b.get('projects', 0)} project(s), {b.get('open', 0)} open session(s), "
                                           f"{b.get('closed', 0)} closed")
        self.cells["started"].configure(text=str(d.get("started") or 0))
        self.cells["agents"].configure(text=", ".join(d.get("agents") or []) or "none found")
        self.cells["service"].configure(text={"systemd": "systemd (user service)", "launchd": "launchd"}.get(
            d.get("service"), "Windows Startup folder" if os.name == "nt" else "a process of its own"))
        self.cells["version"].configure(
            text=(f"{ver['commit']} of {ver.get('date')}: {ver.get('subject', '')}" if ver.get("commit")
                  else "not a git clone") + f"\nprotocol v{ver.get('protocol', '?')}")
        up = d.get("update") or {}
        n = len(up.get("behind") or [])
        if up.get("error"):
            ut, colour = f"Check failed {ago(up.get('at'))} ago: {up['error']}", DANGER
        elif n:
            ut, colour = f"{n} available (checked {ago(up.get('at'))} ago):\n" + "\n".join(up["behind"][:4]), WARN
        elif up.get("at"):
            ut, colour = f"Up to date (checked {ago(up.get('at'))} ago)", OK_C
        else:
            ut, colour = "Not checked yet", MUTED
        self.cells["update"].configure(text=ut, fg=colour)
        free = self.busy is None
        self.btn["up"].set(free and not run)
        self.btn["down"].set(free and bool(run))
        self.btn["restart"].set(free and bool(run))
        self.btn["check"].set(free, f"Update to {up.get('latest')}" if n else "Check for updates")
        self.btn["quit"].set(free)
        self.l_msg.configure(text=self.msg[0], fg=self.msg[1])
        focused = self.root.focus_get()
        if free and (focused is None or not any(focused is b.c and b.enabled for b in self.btn.values())):
            next((b.c for b in self.btn.values() if b.enabled), self.root).focus_set()  # the keyboard starts on one

    # ---- actions
    def act(self, key: str) -> None:
        if self.busy:
            return
        if key == "check" and len((self.data or {}).get("update", {}).get("behind") or []):
            key = "update"
        if key == "quit":
            from tkinter import messagebox
            if not messagebox.askokcancel("Quit the mmbox daemon",
                                          "The daemon stops, and its icon leaves the notification area. Nothing "
                                          "is taken for this computer's projects until it starts again (at the "
                                          "next login, or from the console).", parent=self.root):
                return
        self.busy = BUSY.get(key, key)
        self.msg = ("", MUTED)
        self._show()

        def work():
            try:
                r = do(key)
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
            self.result = (key, r)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, key: str, r: dict) -> None:
        self.busy = None
        self.msg = (r.get("out") or "Done", OK_C) if r.get("ok") else ("Failed: " + str(r.get("err") or "?"), DANGER)
        if key == "quit":
            self.close()
            return
        self.next_fetch = 0.0  # the new state at once
        self._show()

    def run(self) -> None:
        self.root.mainloop()


def main(daemon_module, what: str) -> int:
    global B
    B = daemon_module
    return run_tray() if what == "tray" else run_window()
