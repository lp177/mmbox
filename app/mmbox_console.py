#!/usr/bin/env python3
"""mmbox console: the operator's graphical view of the mmbox services, boxes and messages (optional).

A local web application (standard library only, besides the client's own `cryptography`): run it, open the URL it
prints. It listens on 127.0.0.1 only and every API call carries the random token of that URL. With pywebview (Debian:
python3-webview) the same page opens in a window of its own, from the icon in the notification area (--browser: no
window, the browser as before).

What it shows, merged in one tree machine > project > box (like a file manager):
  - the boxes of this computer and their messages: the local logs (decrypted, as each box handled them) and what
    still waits unread in each box (decrypted here, never taken: the box's own listener still gets it);
  - the table of active boxes of each service (`/v1/boxes`): every box for an operator, else this computer's boxes
    and their peers;
  - the collector of each service where this console is an operator (MMBOX_OPERATORS on the gateway): who sent what to
    whom, what was taken and consumed, and the annotations the clients sealed for the operator's key, opened here.
What it does: send a message to a box (from a box of this computer that is paired with it), post a broadcast,
add / edit / remove / use a service, choose what this computer annotates for each collector. Every change goes
through `mmbox.py` itself, so the rules of the client apply.

The operator identity is this console's own key pair, in ~/.config/mmbox-console/ (MMBOX_CONSOLE_HOME): never an
mmbox box, never paired. Its id goes to MMBOX_OPERATORS on the gateways it may read.

  python3 app/mmbox_console.py [--port 8765] [--open] [--browser] [--no-tray] [--client PATH] [--days 7] [--refresh 8]
"""
from __future__ import annotations

import argparse, base64, collections, hashlib, importlib.util, json, mimetypes, os, re, secrets, socket, subprocess
import sys, threading, time, urllib.error, urllib.parse, urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
VERBOSE = bool(os.environ.get("MMBOX_CONSOLE_DEBUG"))


def log(*a):
    print("console:", *a, file=sys.stderr, flush=True)


WAITING = ("queued", "sent", "unread", "taken")  # a message's states while it is still in a box (or on its way)
PURGE_KEEP_DEFAULT_S = 86400  # the purge keeps this much of the open boxes' finished messages (Settings)
PURGE_KEEP_MAX_S = 366 * 86400
HELD_AFTER_S = 300  # a message its sender keeps queued (the service refuses it) for longer than this is "held": shown loud
HELD_RETRY_S = 1800  # no refusal seen for this long: its sender stopped retrying (console.js: HELD_RETRY_S)
# final states besides read: "delivered" (its service says it was consumed: what the logs of this computer and the
# collectors could not tell, as for a message sent before a collector ran), "stranded" (it waits in a box that closed:
# the relay forwards real messages, not the tool's own), "dropped" (its sender gave up: no service ever accepted it)
DELIVERY_RECHECK_S = 120  # a sent message the service still holds is asked about again after this long
DELIVERY_GROUPS = 24  # (service, sender, recipient) asked per refresh at most: the first refreshes catch up in turn


def fmt_bytes(n: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def fmt_delay(s: float) -> str:
    """A delay in the largest unit that divides it: 1 day, 36 hours, 90 minutes, 0 minutes."""
    for div, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s and s % div == 0:
            n = int(s // div)
            return f"{n} {unit}" + ("s" if n > 1 else "")
    return f"{s / 60:g} minutes"


def dir_size(p: Path) -> int:
    """The bytes of a file, or of everything under a folder (links not followed)."""
    try:
        if p.is_symlink():
            return 0
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def webkit_dirs() -> list[Path]:
    """Where WebKitGTK keeps the window's page data (local storage, cache), named after the program (mmbox-console)."""
    if os.name == "nt" or sys.platform == "darwin":
        return []
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return [data / "mmbox-console", cache / "mmbox-console"]


# ---------------------------------------------------------------- the client, imported as a module
def find_client(explicit: str | None) -> Path:
    for c in (explicit, os.environ.get("MMBOX_PY"), HERE.parent / "client" / "mmbox.py", installed_client()):
        if c and Path(c).is_file():
            return Path(c).resolve()
    sys.exit("console: mmbox.py not found: pass --client <path to mmbox.py> (or MMBOX_PY)")


def installed_client() -> Path:
    """The client the skill mmbox-install put on this computer (the same rule as its mmbox_client.py)."""
    if os.environ.get("MMBOX_CLIENT_DIR"):
        d = Path(os.environ["MMBOX_CLIENT_DIR"]).expanduser()
    elif os.name == "nt":
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "mmbox" / "src"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "mmbox" / "src"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mmbox" / "src"
    return d / "client" / "mmbox.py"


def load_client(path: Path):
    spec = importlib.util.spec_from_file_location("mmbox_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cache_identities(mod)
    return mod


def cache_identities(mod) -> None:
    """The client's load_me, each profile read once while its files do not change. Its peers_load lists every other
    profile of the computer (local_siblings), so a refresh loaded each identity (files, keys) once per profile and per
    lookup: tens of thousands of times with a hundred boxes, seconds of CPU at each refresh. Each caller gets its
    own copy of the profile's settings (callers change them before saving)."""
    if not hasattr(mod, "load_me") or not hasattr(mod, "Local"):
        return
    import copy
    orig, cache, lock = mod.load_me, {}, threading.Lock()

    def stamp(local, name):
        try:
            st = local.path(name).stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def load_me(profile):
        try:
            local = mod.Local(profile)
            key = (stamp(local, "identity.json"), stamp(local, "config.json"))
        except Exception:
            return orig(profile)
        with lock:
            hit = cache.get(profile)
        if hit is None or hit[0] != key:
            hit = (key, orig(profile))
            with lock:
                cache[profile] = hit
        me = hit[1]
        if me is None:
            return None
        c = copy.copy(me)
        c.conf = copy.deepcopy(me.conf)
        return c
    mod.load_me = load_me

    if not all(hasattr(mod, f) for f in ("local_siblings", "local_profiles", "local_rights_conf", "other_project_conf")):
        return
    orig_sib, sib, gen = mod.local_siblings, {}, {"at": 0.0, "key": None}

    def generation():
        """The files of every profile (identity, settings), looked at most once a second."""
        now = time.monotonic()
        with lock:
            if now - gen["at"] < 1.0:
                return gen["key"]
        key = tuple((n, stamp(mod.Local(n), "identity.json"), stamp(mod.Local(n), "config.json")) for n in mod.local_profiles())
        with lock:
            gen.update(at=now, key=key)
        return key

    def local_siblings(local):
        """The same list for each profile while no profile changed (it read every profile for each one of them)."""
        try:
            key = (local.profile, generation(),
                   json.dumps([mod.local_rights_conf(), mod.other_project_conf()], sort_keys=True, default=str))
        except Exception:
            return orig_sib(local)
        with lock:
            hit = sib.get(local.profile)
        if hit is None or hit[0] != key:
            hit = (key, orig_sib(local))
            with lock:
                sib[local.profile] = hit
        return {pid: dict(q) for pid, q in hit[1].items()}  # callers change the entries (alias)
    mod.local_siblings = local_siblings


NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: what the console runs never opens a window on Windows
M = None  # the client module, set in main()
B = None  # the daemon's module (client/mmbox_daemon.py beside the client), None with an older client
CLIENT: Path | None = None


def load_butler():
    """The daemon's module (its butler), beside the client (a full clone); None when the client has none yet."""
    p = Path(CLIENT).with_name("mmbox_daemon.py") if CLIENT else None
    if not p or not p.is_file():
        return None
    spec = importlib.util.spec_from_file_location("mmbox_daemon", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.M = M
    return mod


# ---------------------------------------------------------------- the operator identity
def console_home() -> Path:
    if os.environ.get("MMBOX_CONSOLE_HOME"):
        return Path(os.environ["MMBOX_CONSOLE_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "mmbox-console"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mmbox-console"


class KeyStore:
    """Where the operator's private key lives, as safely as this system allows: the Secret Service (GNOME Keyring,
    KWallet: Linux desktops), DPAPI (Windows: encrypted for this user, file identity.dpapi), else a file readable by
    this user only (identity.json, 0600: a server without a keyring), said in the log. `identity.where` names the
    store; when it names a store that does not answer (a locked keyring), the console stops: it never makes a new key
    over the one that store holds (a new key is no longer the services' operator)."""

    def __init__(self, home: Path, name: str = "identity"):
        self.home, self.name = home, name  # "identity": this device's key pair; "root": the account's root key
        self.where_file = home / f"{name}.where"
        self.attrs = {"home": str(home), "key": name}

    # -- the stores
    def _secret(self):
        if os.name == "nt" or sys.platform == "darwin" or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return None
        try:
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            schema = Secret.Schema.new("org.mmbox.console", Secret.SchemaFlags.NONE, {"home": Secret.SchemaAttributeType.STRING,
                                                                                     "key": Secret.SchemaAttributeType.STRING})
            return Secret, schema
        except (ImportError, ValueError, AttributeError):
            return None

    @staticmethod
    def _dpapi(data: bytes, protect: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        buf = ctypes.create_string_buffer(data, len(data))
        inp, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
        c32 = ctypes.windll.crypt32
        ok = (c32.CryptProtectData(ctypes.byref(inp), "mmbox console operator key", None, None, None, 0x1, ctypes.byref(out))
              if protect else c32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)))
        if not ok:
            raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed ({ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def where(self) -> str | None:
        try:
            return self.where_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return "file" if (self.home / f"{self.name}.json").is_file() else None

    def load(self) -> dict | None:
        """The operator identity, or None when this console has none yet; raises when its store does not answer."""
        w = self.where()
        if w is None:
            return None
        if w == "dpapi":
            return json.loads(self._dpapi((self.home / f"{self.name}.dpapi").read_bytes(), False).decode("utf-8"))
        if w == "secret-service":
            sec = self._secret()
            v = sec[0].password_lookup_sync(sec[1], self.attrs, None) if sec else None
            if not v:
                raise RuntimeError("the operator key is kept in this session's keyring (Secret Service), which does not "
                                   "answer or is locked: unlock it (log in to the desktop), then start the console again")
            return json.loads(v)
        return json.loads((self.home / f"{self.name}.json").read_text(encoding="utf-8"))

    def save(self, ident: dict) -> str:
        """Kept in the safest store this system has; the plain file goes once the key is safely elsewhere."""
        data = json.dumps(ident, indent=1)
        where = "file"
        if os.name == "nt":
            try:
                blob = self._dpapi(data.encode("utf-8"), True)
                self._write(self.home / f"{self.name}.dpapi", blob)
                if self._dpapi(blob, False).decode("utf-8") == data:
                    where = "dpapi"
            except OSError as e:
                log(f"DPAPI unavailable ({e}): the operator key stays in a file of this user")
        elif (sec := self._secret()) is not None:
            try:
                ok = sec[0].password_store_sync(sec[1], self.attrs, sec[0].COLLECTION_DEFAULT,
                                                f"mmbox console {'account root key' if self.name == 'root' else 'operator key'}", data, None)
                if ok and sec[0].password_lookup_sync(sec[1], self.attrs, None) == data:
                    where = "secret-service"
            except Exception as e:  # noqa: BLE001 - no Secret Service on this session: a file of this user then
                log(f"no keyring answers ({type(e).__name__}: {e}): the operator key stays in a file of this user (0600)")
        if where == "file":
            self._write(self.home / f"{self.name}.json", data.encode("utf-8"))
        else:
            self._shred(self.home / f"{self.name}.json")
        self._write(self.where_file, where.encode("utf-8"))
        return where

    @staticmethod
    def _write(p: Path, data: bytes) -> None:
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)

    @staticmethod
    def _shred(p: Path) -> None:
        if p.is_file():
            try:
                p.write_bytes(os.urandom(max(64, p.stat().st_size)))
            except OSError:
                pass
            p.unlink(missing_ok=True)


def new_identity() -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    raw = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
               encryption_algorithm=serialization.NoEncryption())
    return {"id": "m" + M.rand_b32(12), "sign_key": M.b64e(Ed25519PrivateKey.generate().private_bytes(**raw)),
            "box_key": M.b64e(X25519PrivateKey.generate().private_bytes(**raw)), "created": M.iso()}


def seal_identity(ident: dict, passphrase: str) -> dict:
    """An export of the operator key for another computer of the same operator: scrypt and ChaCha20-Poly1305."""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    salt, nonce = os.urandom(16), os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2 ** 17, r=8, p=1).derive(passphrase.encode("utf-8"))
    ct = ChaCha20Poly1305(key).encrypt(nonce, json.dumps(ident).encode("utf-8"), b"mmbox-operator-key")
    return {"mmbox_operator_key": 1, "id": ident["id"], "kdf": {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1},
            "salt": M.b64e(salt), "nonce": M.b64e(nonce), "ct": M.b64e(ct)}


def open_identity(blob: dict, passphrase: str) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    k = blob.get("kdf") or {}
    key = Scrypt(salt=M.b64d(blob["salt"]), length=32, n=int(k.get("n", 2 ** 17)), r=int(k.get("r", 8)),
                 p=int(k.get("p", 1))).derive(passphrase.encode("utf-8"))
    try:
        return json.loads(ChaCha20Poly1305(key).decrypt(M.b64d(blob["nonce"]), M.b64d(blob["ct"]), b"mmbox-operator-key"))
    except InvalidTag:
        raise ValueError("wrong passphrase, or a damaged export") from None


class Operator:
    """This console's key pair: it signs the operator's requests and opens the annotations sealed for it."""

    def __init__(self, create: bool = True):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        home = console_home()
        home.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(home, 0o700)
        self.store = KeyStore(home)
        ident = self.store.load()
        if ident is None:
            if not create:
                raise LookupError("no operator key on this computer yet")
            ident = new_identity()
            log(f"operator identity created: {ident['id']} ({self.store.save(ident)})")
        elif self.store.where() == "file" and (os.name == "nt" or self.store._secret()):
            log(f"operator key moved to {self.store.save(ident)} (it was a plain file)")
        self.id = ident["id"]
        self.sign = Ed25519PrivateKey.from_private_bytes(M.b64d(ident["sign_key"]))
        self.box = X25519PrivateKey.from_private_bytes(M.b64d(ident["box_key"]))
        pub = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.sign_pub = self.sign.public_key().public_bytes(**pub)
        self.box_pub = self.box.public_key().public_bytes(**pub)
        self.fingerprint = M.fingerprint(self.sign_pub, self.box_pub)
        mconf = M.Local(M.MACHINE_PROFILE).load("config.json", {}) or {}
        self.user = mconf.get("user") or os.environ.get("USER") or "operator"
        self.label = f"mmbox console ({socket.gethostname()})"
        self.home = home

    def signed(self, obj: dict) -> dict:
        return {**obj, "sig": M.b64e(self.sign.sign(M.canon(obj)))}

    def card(self) -> dict:
        c = {"v": M.VERSION, "id": self.id, "user": self.user, "label": self.label, "agent": "console",
             "sign_pub": M.b64e(self.sign_pub), "box_pub": M.b64e(self.box_pub), "fingerprint": self.fingerprint}
        return {**c, "sig": M.b64e(self.sign.sign(M.canon(c)))}


# ---------------------------------------------------------------- signed requests (as the operator, or as a box)
_SKEW: dict[str, float] = {}


class ConnPool:
    """Kept-alive connections to the services, shared by every thread of the console. A distant service costs a
    round trip per request on a kept connection, against three or four for a new TLS one (about 1 s instead of 0.3 s
    over a 300 ms link); the refresh's jobs and the page's actions each run in a thread of their own, so the
    client's per-thread connections would never be used twice. Behind a proxy (its environment variables) or with
    MMBOX_NO_KEEPALIVE: urllib, a new connection each time, as before."""

    KEEP = 8  # idle connections kept per service: about as many as one refresh asks at once

    def __init__(self):
        self.idle: dict[tuple, list] = {}
        self.lock = threading.Lock()
        self._tls = None
        self.direct = not (os.environ.get("MMBOX_NO_KEEPALIVE") or any(os.environ.get(k) for k in (
            "https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY")))

    def tls(self):
        if self._tls is None:
            import ssl
            self._tls = ssl.create_default_context()
        return self._tls

    def request(self, url: str, method: str, body: bytes | None, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
        """(status, headers, body) of one request; OSError when the service could not be reached or answered
        nothing readable."""
        if not self.direct:
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers or {}), e.read() or b""
            except urllib.error.URLError as e:
                raise OSError(str(e.reason)) from None
        import http.client
        u = urllib.parse.urlsplit(url)
        key = (u.scheme, u.hostname, u.port)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        for attempt in range(2):
            with self.lock:
                idle = self.idle.get(key) or []
                conn = idle.pop() if idle else None
            kept = conn is not None
            if conn is None:
                conn = (http.client.HTTPSConnection(u.hostname, u.port, timeout=timeout, context=self.tls())
                        if u.scheme == "https" else http.client.HTTPConnection(u.hostname, u.port, timeout=timeout))
            try:
                conn.timeout = timeout
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
                conn.request(method, path, body=body, headers={**headers, "Connection": "keep-alive"})
                r = conn.getresponse()
                data = r.read()
            except (http.client.HTTPException, OSError) as exc:
                conn.close()
                if kept and not attempt and not isinstance(exc, TimeoutError):
                    continue  # a kept connection the service closed while it was idle: once more, on a new one
                if isinstance(exc, OSError):
                    raise
                raise OSError(f"{type(exc).__name__}: {exc}") from None
            if r.will_close:
                conn.close()
            else:
                with self.lock:
                    idle = self.idle.setdefault(key, [])
                    if len(idle) < self.KEEP:
                        idle.append(conn)
                        conn = None
                if conn is not None:
                    conn.close()
            return r.status, dict(r.getheaders()), data
        raise OSError("no answer")


POOL = ConnPool()


def call(url: str, ident, path: str, body: dict, timeout: float = 20) -> dict:
    """POST /v1<path> on service `url`, signed by `ident` (anything with .id and .sign): {"ok"...} or {"_error"...}."""
    raw = json.dumps(body, ensure_ascii=False).encode()
    for attempt in range(2):
        ts, nonce = str(int(time.time() + _SKEW.get(url, 0))), secrets.token_hex(16)
        msg = "\n".join(["mmbox/2", "POST", "/v1" + path, ts, nonce, hashlib.sha256(raw).hexdigest()]).encode()
        headers = {"Content-Type": "application/json", "User-Agent": M.UA + " console", "X-Mmbox-Id": ident.id,
                   "X-Mmbox-Ts": ts, "X-Mmbox-Nonce": nonce, "X-Mmbox-Sig": M.b64e(ident.sign.sign(msg))}
        try:
            code, rh, data = POOL.request(url + "/v1" + path, "POST", raw, headers, timeout)
        except OSError as e:  # TimeoutError included
            return {"_error": 0, "error": str(e) or type(e).__name__}
        _clock(url, rh.get("Date") or rh.get("date"))
        if 200 <= code < 300:
            try:
                return json.loads(data or b"{}")
            except ValueError:
                return {"_error": code, "error": "unreadable answer"}
        try:
            why = json.loads(data or b"{}").get("error", "")
        except (ValueError, AttributeError):
            why = ""
        if code == 401 and "skew" in why and attempt == 0:
            continue
        if code == 401 and "unknown identity" in why and attempt == 0 and isinstance(ident, Operator):
            call(url, ident, "/card", {"card": ident.card()})
            continue
        return {"_error": code, "error": why}
    return {"_error": 0, "error": "gave up"}


def _clock(url: str, date: str | None) -> None:
    if date:
        try:
            from email.utils import parsedate_to_datetime
            _SKEW[url] = parsedate_to_datetime(date).timestamp() - time.time()
        except (TypeError, ValueError):
            pass


def health(url: str) -> dict:
    t0 = time.monotonic()
    try:
        code, rh, data = POOL.request(url + "/v1/health", "GET", None, {"User-Agent": M.UA + " console"}, 6)
        _clock(url, rh.get("Date") or rh.get("date"))
        if not 200 <= code < 300:
            return {"_error": code, "error": f"HTTP {code}"}
        h = json.loads(data or b"{}")
        h["ms"] = round((time.monotonic() - t0) * 1000)
        return h
    except (OSError, ValueError) as e:
        return {"_error": 0, "error": str(e) or type(e).__name__}


def parallel(fns: dict) -> dict:
    out = {}
    ts = [threading.Thread(target=lambda k=k, f=f: out.__setitem__(k, f()), daemon=True) for k, f in fns.items()]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return out


# ---------------------------------------------------------------- what the console knows
TOOL_KINDS = {"ping", "pong", "link", "transport", "retire", "introduce", "notice", "forward", "ack", "revoke"}


class LogCache:
    """log.jsonl of each local profile, read again only when it grew (from where it was)."""

    def __init__(self):
        self.files: dict[str, dict] = {}

    def read(self, profile: str, path: Path) -> list[dict]:
        rec = self.files.setdefault(profile, {"pos": 0, "ino": None, "rows": []})
        try:
            st = path.stat()
        except OSError:
            return rec["rows"]
        if st.st_ino != rec["ino"] or st.st_size < rec["pos"]:
            rec.update(pos=0, ino=st.st_ino, rows=[])
        if st.st_size > rec["pos"]:
            with path.open("rb") as f:
                f.seek(rec["pos"])
                data = f.read()
            cut = data.rfind(b"\n") + 1
            for line in data[:cut].splitlines():
                try:
                    rec["rows"].append(json.loads(line))
                except ValueError:
                    pass
            rec["pos"] += cut
        return rec["rows"]


# ---------------------------------------------------------------- questions the boxes put to the operator
QUESTIONS_KEEP_S = 14 * 86400
SOUNDS = {  # generated for this project (app/static/sounds/README.md): free to use, no attribution
    "bell": "Soft bell", "ding-dong": "Ding-dong", "marimba": "Marimba", "drop": "Water drop",
    "chimes": "Chimes", "bubble": "Bubble", "arpeggio": "Arpeggio", "small-bell": "Small bell", "xylophone": "Xylophone",
    "chord": "Muted chord"}
# the French names the sounds had before 2026-11-07: a state.json saved then still names one of them
OLD_SOUND_NAMES = {"cloche": "bell", "goutte": "drop", "carillon": "chimes", "bulle": "bubble", "arpege": "arpeggio",
                   "clochette": "small-bell", "accord": "chord"}
NOTIFY_DEFAULT = {"popup": True, "badge": True, "audio": False, "sound": "bell"}
LOCAL_WINS_MS = 10 ** 15  # a time no message carries: a profile of this computer is the last word on its model
STATS_WINDOWS = (300, 3600, 86400, 604800)  # 5 min, 1 h, 1 d, 7 d: the service keeps 7 days, whatever is shown


class Questions:
    """The questions boxes put to this console (`mmbox.py question`), taken from the operator's box on each service and
    kept in questions.json: pending until answered or withdrawn by their box, then kept QUESTIONS_KEEP_S."""

    def __init__(self, home: Path):
        self.path = home / "questions.json"
        self.lock = threading.RLock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items: dict[str, dict] = data.get("items") or {}
        # the operator's decisions on messages a box asked its user about (`ask` rights), by message id
        self.decisions: dict[str, dict] = data.get("decisions") or {}

    def save(self) -> None:
        now = time.time()
        for k in [k for k, q in self.items.items() if q["state"] != "pending" and now - (q.get("closed_at") or now) > QUESTIONS_KEEP_S]:
            del self.items[k]
        for k in [k for k, d in self.decisions.items() if now - (d.get("at") or now) > QUESTIONS_KEEP_S]:
            del self.decisions[k]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": self.items, "decisions": self.decisions}, ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def pending(self) -> list[dict]:
        with self.lock:
            return sorted((q for q in self.items.values() if q["state"] == "pending"), key=lambda q: q["received_at"])

    def recent(self, n: int = 40) -> list[dict]:
        with self.lock:
            return sorted((q for q in self.items.values() if q["state"] != "pending"), key=lambda q: -(q.get("closed_at") or 0))[:n]

    def add(self, url: str, sender: str, p: dict, card: dict, known: bool) -> None:
        with self.lock:
            if p.get("withdraw"):  # the box's user answered elsewhere (in its chat)
                q = self.items.get(str(p["withdraw"]))
                if q and q["from"] == sender and q["state"] == "pending":
                    q.update(state="withdrawn", closed_at=time.time(),
                             **({"withdrawn_answer": str(p["withdraw_answer"])[:4000]} if p.get("withdraw_answer") else {}))
                    self.save()
                return
            if p.get("id") in self.items:
                return
            about = p.get("about") if isinstance(p.get("about"), dict) else None
            self.items[p["id"]] = {
                "id": p["id"], "server": url, "from": sender, "label": p.get("label") or card.get("label"),
                "title": p.get("title"), "activity": p.get("activity"), "work": p.get("work"), "project": p.get("project"),
                "hostname": (p.get("host") or {}).get("hostname"), "agent": p.get("agent"), "fingerprint": card.get("fingerprint"),
                "known": known, "ts": p.get("ts"), "received_at": time.time(), "subject": str(p.get("subject") or "")[:200],
                "body": str(p.get("body") or "")[:8000], "options": [str(o)[:600] for o in (p.get("options") or [])][:12],
                "multiple": p.get("multiple") is not False, "re": p.get("re"),
                "about": {k: (str(v)[:4000] if isinstance(v, str) else v) for k, v in about.items()} if about else None,
                "state": "pending"}
            self.save()


class Recaps:
    """What the sessions wrote to this console when their work was over (`mmbox.py done`), taken from the operator's
    box on each service and kept in recaps.json for good: only the operator's own deletion removes one."""
    FIELDS = ("kind", "subject", "body", "ts", "from", "from_user", "label", "agent", "title", "activity", "work",
              "project", "host", "provider", "model", "then", "open_questions")

    def __init__(self, home: Path):
        self.path = home / "recaps.json"
        self.lock = threading.RLock()
        try:
            self.items: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8")).get("items") or {}
        except (OSError, ValueError, AttributeError):
            self.items = {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": self.items}, ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, url: str, sender: str, p: dict, card: dict) -> dict | None:
        """The recap kept (None: already there, or not one)."""
        with self.lock:
            mid = str(p.get("id") or "")
            if not mid or mid in self.items or p.get("from") != sender:
                return None
            r = {k: p[k] for k in self.FIELDS if p.get(k) not in (None, "")}
            r["body"] = str(r.get("body") or "")[:16000]
            r.update(id=mid, server=url, fingerprint=card.get("fingerprint"), received_at=time.time(), to=p.get("to"))
            self.items[mid] = r
            self.save()
            return r

    def all(self) -> list[dict]:
        with self.lock:
            return list(self.items.values())

    def remove(self, ids) -> int:
        with self.lock:
            gone = [i for i in ids if self.items.pop(i, None) is not None]
            if gone:
                self.save()
            return len(gone)


def desktop_session() -> bool:
    """A Linux graphical session with a session bus: where the notification area and the notifications are."""
    return os.name != "nt" and sys.platform != "darwin" and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")) \
        and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class AppWindow:
    """The console in a window of its own (pywebview: WebKitGTK on Linux, WebView2 on Windows, WebKit on macOS): the
    same page as in the browser, with the same token. Its close button hides it in the notification area while the
    icon is there (`can_hide`, set by Desktop), otherwise it quits the console. Every call may come from any thread:
    pywebview's own calls wait for the window, so they run in a short thread of their own."""


    def __init__(self, webview, url: str, shown: bool, home: Path, own_titlebar: bool = True):
        self.wv, self.home = webview, home
        # the title bar drawn by the page (Settings, on by default) instead of the system's: GTK only, where the
        # window can lose and get back its decorations at once, and the window manager moves and resizes it
        self.titlebar_ok = desktop_session()
        self.own_titlebar = own_titlebar and self.titlebar_ok
        self.maximized = False
        self._press = None  # the webview's last press of the left button (a copy), for its release after a drag
        self.can_hide, self.quitting, self.visible = False, False, shown
        self.left_front = 0.0  # when the window last lost the focus (monotonic): a click on the panel may take it
        self.activation_token = None  # Wayland: the token the panel gives just before its click (xdg-activation)
        self._watched = False
        self.goto, self.goto_n = None, 0  # a notification's click: what the page opens (also at its next poll, 20 s)
        self.on_change = lambda: None  # the icon's menu follows the window
        webview.settings["ALLOW_DOWNLOADS"] = True  # the attached files: a save dialog
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        width, height = 1280, 860
        if desktop_session():
            try:
                from gi.repository import Gdk, GdkPixbuf, Gtk
                Gtk.init([])
                # its icon in the task bar: set before the window exists, which the default icon applies to only
                # the thick-stroke drawing for the small sizes (it stays readable at 16 px), the full one above
                Gtk.Window.set_default_icon_list([GdkPixbuf.Pixbuf.new_from_file_at_size(
                    str(STATIC / ("icon-small.svg" if n <= 32 else "icon.svg")), n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
                # most of the screen pywebview centres it on (its first), a 4K one included
                geo = Gdk.Display.get_default().get_monitor(0).get_geometry()
                width, height = max(1100, int(geo.width * 0.7)), max(760, int(geo.height * 0.8))
            except Exception as e:
                log(f"window: {type(e).__name__}: {e}")
        self.win = webview.create_window("mmbox", url + "&app=1", width=width, height=height, min_size=(760, 520),
                                         hidden=not shown, background_color="#121212", text_select=True,
                                         frameless=self.own_titlebar, easy_drag=False)
        self.win.events.closing += self._closing
        for ev in (self.win.events.maximized, self.win.events.restored, self.win.events.shown):
            ev += self._frame_changed

    # ---- the page's own title bar: its buttons, its drag area and the window's edges call these (POST "window")

    def frame(self) -> dict:
        """What the page draws: its own title bar or not, and the window maximized (the restore button then)."""
        return {"titlebar": self.own_titlebar, "titlebar_ok": self.titlebar_ok, "maximized": self.maximized}

    def _frame_changed(self, *_a) -> None:
        """Maximized or restored by the window manager (a double click, a shortcut, a drag to the screen's top): the
        state read in the GTK loop, then the page told at once."""
        if not self.titlebar_ok:
            return
        from gi.repository import Gdk, GLib

        def run():
            try:
                self._hook_press()
                gw = self._gtk_window().get_window()
                self.maximized = bool(gw and gw.get_state() & (Gdk.WindowState.MAXIMIZED | Gdk.WindowState.FULLSCREEN))
            except Exception:
                pass
            self._run_js(f"typeof setWindowFrame === 'function' && setWindowFrame({json.dumps(self.frame())})")
            return False
        GLib.idle_add(run)

    def _hook_press(self) -> None:
        """Once, in the GTK loop: the webview's presses of the left button are kept (a copy, before WebKit has them)."""
        if self._press is not None:
            return
        from webview.platforms.gtk import BrowserView
        self._press = False

        def keep(_wv, ev):
            if ev.button == 1:
                self._press = ev.copy()
            return False
        BrowserView.instances[self.win.uid].webview.connect("button-press-event", keep)

    def _release_page(self) -> None:
        """The window manager now has the pointer and its button's release: the page never gets it and would keep
        the button down (its next click lost). It gets a release made from the press that started the drag."""
        try:
            from gi.repository import Gdk
            from webview.platforms.gtk import BrowserView
            ev = self._press
            if ev:
                self._press = False
                ev.type = Gdk.EventType.BUTTON_RELEASE
                ev.state |= Gdk.ModifierType.BUTTON1_MASK
                BrowserView.instances[self.win.uid].webview.event(ev)
        except Exception as e:
            log(f"window: {type(e).__name__}: {e}")

    def set_titlebar(self, own: bool) -> None:
        """Settings: the page's title bar (the system's one removed at once) or the system's."""
        self.own_titlebar = bool(own) and self.titlebar_ok
        w = self._gtk_window()
        if w is not None:
            from gi.repository import GLib
            GLib.idle_add(lambda: w.set_decorated(not self.own_titlebar) or self._frame_changed() or False)

    EDGES = {"n": "NORTH", "s": "SOUTH", "e": "EAST", "w": "WEST",
             "ne": "NORTH_EAST", "nw": "NORTH_WEST", "se": "SOUTH_EAST", "sw": "SOUTH_WEST"}

    def window_op(self, op: str, edge: str = "") -> dict:
        """A button of the page's title bar (minimize, maximize: or restore, close), or the window moved or resized
        by the window manager from the pointer, while its button is still down (a click already over does nothing)."""
        if op not in ("minimize", "maximize", "close", "move", "resize") or (op == "resize" and edge not in self.EDGES):
            return {"ok": False, "err": "op: minimize, maximize, close, move or resize (edge n, s, e, w, ne, nw, se, sw)"}
        w = self._gtk_window()
        if w is None:
            if op == "close":
                self.hide() if self.can_hide else self.quit()
            return {"ok": op == "close"}
        from gi.repository import Gdk, GLib

        def run():
            try:
                if op == "minimize":
                    w.iconify()
                elif op == "maximize":
                    w.unmaximize() if w.is_maximized() else w.maximize()
                elif op == "close":
                    w.close()  # as the system's button: _closing hides it in the notification area, or quits
                else:
                    ptr = Gdk.Display.get_default().get_default_seat().get_pointer()
                    _s, x, y = ptr.get_position()
                    gw = w.get_window()
                    held = gw is not None and gw.get_device_position(ptr)[3] & Gdk.ModifierType.BUTTON1_MASK
                    if held and op == "move":
                        w.begin_move_drag(1, x, y, Gdk.CURRENT_TIME)
                    elif held:
                        w.begin_resize_drag(getattr(Gdk.WindowEdge, self.EDGES[edge]), 1, x, y, Gdk.CURRENT_TIME)
                    if held:
                        self._release_page()
            except Exception as e:
                log(f"window {op}: {type(e).__name__}: {e}")
            return False
        GLib.idle_add(run)
        return {"ok": True}

    @staticmethod
    def _bg(fn, *a) -> None:
        threading.Thread(target=fn, args=a, daemon=True, name="window").start()

    def _closing(self) -> bool:
        """The close button: hidden in the notification area (False cancels the close), or the console stops."""
        if self.quitting or not self.can_hide:
            self.quitting = True
            return True
        self.hide()
        return False

    def _gtk_window(self):
        try:
            from webview.platforms.gtk import BrowserView
            return BrowserView.instances[self.win.uid].window
        except Exception:
            return None

    def _watch(self, w) -> None:
        """When the window loses the focus: a click on the icon may take it from the window just before Activate."""
        if not self._watched:
            self._watched = True
            w.connect("notify::is-active", lambda w, _p: None if w.is_active() else setattr(self, "left_front", time.monotonic()))

    def _present(self) -> bool:
        """In the GTK loop: shown, out of the task bar and in front with the focus. A bare present() is refused by
        the window manager's focus stealing prevention (KWin: no user time, and an unmapped window keeps the time of
        its last use, so it maps behind the others): the X server's time now counts as the user's action; on Wayland,
        the panel's activation token. If it still is not active a moment later, it is raised by force."""
        w = self._gtk_window()
        if w is None:  # not built yet: pywebview shows it
            self.win.show()
            self.win.restore()
            return False
        from gi.repository import Gdk, GLib
        self._watch(w)
        w.realize()
        gw, t = w.get_window(), 0
        try:
            import gi
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            if isinstance(gw, GdkX11.X11Window):
                t = GdkX11.x11_get_server_time(gw)
                gw.set_user_time(t)  # mapped as if the user had just acted on it: in front, not behind
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
        if self.activation_token:
            w.set_startup_id(self.activation_token)
            self.activation_token = None
        w.show_all()
        w.deiconify()
        w.present_with_time(t or Gdk.CURRENT_TIME)

        def insist():
            if w.get_visible() and not w.is_active():
                w.set_keep_above(True)  # above every window for an instant: in front whatever the policy
                w.present_with_time(t or Gdk.CURRENT_TIME)
                if gw is not None:
                    gw.focus(t or Gdk.CURRENT_TIME)
                GLib.timeout_add(150, lambda: w.set_keep_above(False) and False)
            return False
        GLib.timeout_add(250, insist)
        return False

    def show(self, question: bool | str = False, held: bool = False, msg: str | None = None, late: bool = False) -> None:
        """Opened (created the first time) and put in front, whether it was hidden, minimized or behind others; then
        what a notification's click is about: one message (`msg`), the questions (`question`: True, or the id of the
        one to show first), the held messages or the late unread ones. The page opens it at once, or as soon as its
        next poll has it."""
        target = ({"msg": msg} if msg else {"question": question} if question else {"held": True} if held
                  else {"late": True} if late else None)
        if target:
            self.goto_n += 1
            self.goto = {**target, "n": self.goto_n, "until": time.time() + 20}

        def run():
            if desktop_session() and self._gtk_window() is not None:
                from gi.repository import GLib
                GLib.idle_add(self._present)
            else:
                self.win.show()
                self.win.restore()  # out of the task bar, in front
            if target:
                self._run_js(f"typeof openFromNotification === 'function' && openFromNotification({json.dumps(self.goto)})")
        self.visible = True
        self._bg(run)
        self.on_change()

    def take_goto(self) -> dict | None:
        """For the window's page at its poll: the last click's target, while it is fresh (the page skips one it ran)."""
        g = self.goto
        return g if g and g["until"] > time.time() else None

    def _run_js(self, code: str) -> None:
        """The page runs `code` now. Not pywebview's evaluate_js: it wraps the code in eval(), which the page's CSP
        (script-src 'self') refuses. On GTK, WebKit runs it as is; elsewhere the page takes the target at its next
        poll of /api/pending (take_goto), as it does when it was not loaded yet."""
        try:
            from webview.platforms.gtk import BrowserView
            wv = BrowserView.instances[self.win.uid].webview
            from gi.repository import GLib
        except Exception:
            return

        def run():
            try:
                wv.evaluate_javascript(code, -1, None, None, None, None, None)
            except Exception as e:
                log(f"window: {type(e).__name__}: {e}")
            return False
        GLib.idle_add(run)

    def on_screen(self) -> bool:
        """Shown and not minimized. Called in the GTK loop on Linux, where the window itself says it."""
        try:
            from gi.repository import Gdk
            w = self._gtk_window()
            gw = w.get_window()
            return w.get_visible() and not (gw and gw.get_state() & Gdk.WindowState.ICONIFIED)
        except Exception:
            return self.visible

    def in_front(self) -> bool:
        """On screen and the active window (or it was until the click on the icon, a moment ago)."""
        w = self._gtk_window()
        if w is None:
            return False
        self._watch(w)
        return self.on_screen() and (w.is_active() or time.monotonic() - self.left_front < 0.6)

    def hide(self) -> None:
        self.visible = False
        self._bg(self.win.hide)
        self.on_change()

    def quit(self) -> None:
        self.quitting = True
        self._bg(self.win.destroy)

    def run(self) -> None:
        """The window's loop, on the main thread, until the console quits. On Linux it is also the icon's GTK loop."""
        opts = {"private_mode": False, "storage_path": str(self.home / "webview")}  # the page's settings are kept
        if desktop_session():
            opts["gui"] = "gtk"
        self.wv.start(**opts)


SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
 <property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
 <property name="WindowId" type="i" access="read"/><property name="IconThemePath" type="s" access="read"/>
 <property name="IconName" type="s" access="read"/><property name="AttentionIconName" type="s" access="read"/>
 <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
 <property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
 <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
 <method name="ProvideXdgActivationToken"><arg name="token" type="s" direction="in"/></method>
 <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
 <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewAttentionIcon"/><signal name="NewToolTip"/>
 <signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""


class SniTray:
    """The icon as a StatusNotifierItem of its own (KDE Plasma, GNOME's AppIndicator extension, most panels): a
    click opens the console (Activate), the right button shows the menu (exported with Dbusmenu)."""

    PATH, IFACE = "/StatusNotifierItem", "org.kde.StatusNotifierItem"

    def __init__(self, d: "Desktop"):
        import gi
        gi.require_version("Dbusmenu", "0.4")
        from gi.repository import Dbusmenu
        Gio, GLib = d.Gio, d.GLib
        self.d, self.D, self.GLib, self.Gio = d, Dbusmenu, GLib, Gio
        self.icon, self.tip = "mmbox-console", "mmbox"
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.server = Dbusmenu.Server.new("/MenuBar")
        root = Dbusmenu.Menuitem.new()
        self.items = {}
        for key, label, act in d.menu():
            it = Dbusmenu.Menuitem.new()
            if key == "sep":
                it.property_set(Dbusmenu.MENUITEM_PROP_TYPE, Dbusmenu.CLIENT_TYPES_SEPARATOR)
            else:
                it.property_set(Dbusmenu.MENUITEM_PROP_LABEL, label)
                it.connect("item-activated", lambda *_, act=act: act())
            root.child_append(it)
            self.items[key] = it
        self.server.set_root(root)
        iface = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self.reg = self.bus.register_object(self.PATH, iface, self._call, self._get, None)
        self.own = Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, self._owned, None)
        self.refresh()

    def _owned(self, *_):
        """Registered with the panel's watcher, and again each time it starts (a restarted plasmashell)."""
        self.watch = self.Gio.bus_watch_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher", self.Gio.BusNameWatcherFlags.NONE, self._register, None)

    def _register(self, *_):
        def done(bus, res):
            try:
                bus.call_finish(res)
            except Exception as e:
                log(f"notification area: {e}")
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                      "RegisterStatusNotifierItem", self.GLib.Variant("(s)", (self.name,)), None,
                      self.Gio.DBusCallFlags.NONE, -1, None, done)

    def _call(self, bus, sender, path, iface, method, params, inv):
        if method == "Activate":  # a click: the window opens, or folds back into the notification area
            self.d.toggle()
        elif method == "SecondaryActivate":  # the middle button: the questions when some wait
            self.d.open(question=bool(self.d.pending_n))
        elif method == "ProvideXdgActivationToken" and self.d.app:  # Wayland: sent just before Activate
            self.d.app.activation_token = params.unpack()[0] or None
        inv.return_value(None)

    def _get(self, bus, sender, path, iface, prop):
        V = self.GLib.Variant
        return {"Category": V("s", "Communications"), "Id": V("s", "mmbox-console"), "Title": V("s", "mmbox"),
                "Status": V("s", "Active"), "WindowId": V("i", 0), "IconThemePath": V("s", str(self.d.icons)),
                "IconName": V("s", self.icon), "AttentionIconName": V("s", "mmbox-console-alert"),
                "ToolTip": V("(sa(iiay)ss)", (self.icon, [], "mmbox", self.tip)),
                "ItemIsMenu": V("b", False), "Menu": V("o", "/MenuBar")}.get(prop)

    def _signal(self, name: str) -> None:
        self.bus.emit_signal(None, self.PATH, self.IFACE, name, None)

    def set_icon(self, icon: str, tip: str) -> None:
        if (icon, tip) != (self.icon, self.tip):
            self.icon, self.tip = icon, tip
            self._signal("NewIcon")
            self._signal("NewToolTip")

    def refresh(self) -> None:
        D = self.D
        for key, (label, enabled, visible) in self.d.menu_state().items():
            it = self.items[key]
            if label:
                it.property_set(D.MENUITEM_PROP_LABEL, label)
            it.property_set_bool(D.MENUITEM_PROP_ENABLED, enabled)
            it.property_set_bool(D.MENUITEM_PROP_VISIBLE, visible)


class AyatanaTray:
    """The icon through AyatanaAppIndicator3, where Dbusmenu is missing or no StatusNotifierWatcher runs (it falls
    back to the old system tray itself): a click shows the menu, the middle button opens the console."""

    def __init__(self, d: "Desktop"):
        import gi
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AI
        Gtk, GLib = d.Gtk, d.GLib
        GLib.log_set_handler("libayatana-appindicator", GLib.LogLevelFlags.LEVEL_WARNING, lambda *a: None)  # "deprecated"
        self.d = d
        ind = AI.Indicator.new_with_path("mmbox-console", "mmbox-console", AI.IndicatorCategory.COMMUNICATIONS, str(d.icons))
        ind.set_status(AI.IndicatorStatus.ACTIVE)
        ind.set_title("mmbox")
        menu, self.items = Gtk.Menu(), {}
        for key, label, act in d.menu():
            it = Gtk.SeparatorMenuItem() if key == "sep" else Gtk.MenuItem(label=label)
            if act:
                it.connect("activate", lambda *_, act=act: act())
            menu.append(it)
            self.items[key] = it
        menu.show_all()
        ind.set_menu(menu)
        ind.set_secondary_activate_target(self.items["open"])
        self.ind = ind
        self.refresh()

    def set_icon(self, icon: str, tip: str) -> None:
        self.ind.set_icon_full(icon, tip)

    def refresh(self) -> None:
        for key, (label, enabled, visible) in self.d.menu_state().items():
            it = self.items[key]
            if label:
                it.set_label(label)
            it.set_sensitive(enabled)
            it.set_visible(visible)


class Desktop:
    """This computer's desktop, when it has one (Linux: a session bus, PyGObject, libnotify, Dbusmenu or
    AyatanaAppIndicator3): an icon in the notification area, with a red dot while questions wait (the badge setting),
    whose click opens the console (its window, or the browser with --browser) and whose menu opens it, opens the
    local site in the browser, hides the window in the notification area or quits; one persistent notification while
    questions wait and no browser tab shows its own (never one per question); the sound, played here when no tab can
    play it. With the window, the GTK loop is the window's, on the main thread; without it, its own, in a thread."""

    def __init__(self, home: Path, url_of, on_quit, app: AppWindow | None = None):
        self.ok, self.url_of, self.on_quit, self.app = False, url_of, on_quit, app
        self.note, self.dismissed, self.shown, self.tray, self.pending_n = None, set(), None, None, 0
        self.note_q, self.held_one, self.recap_notes = None, None, {}  # what each notification's click opens
        self.held_note, self.held_dismissed, self.held_shown, self.held_n, self.badge = None, set(), None, 0, True
        if not desktop_session():
            return
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Notify", "0.7")
            from gi.repository import Gio, GLib, Gtk, Notify
        except (ImportError, ValueError) as e:
            log(f"no icon in the notification area ({e}): install python3-gi, gir1.2-dbusmenu-glib-0.4, gir1.2-notify-0.7")
            return
        self.Gio, self.GLib, self.Gtk, self.Notify = Gio, GLib, Gtk, Notify
        self.icons = home / "icons"
        self.icons.mkdir(parents=True, exist_ok=True)
        for name, dot in (("mmbox-console", False), ("mmbox-console-alert", True)):
            (self.icons / f"{name}.svg").write_text(tray_svg(dot), encoding="utf-8")
        if app:
            app.on_change = lambda: GLib.idle_add(self._refresh)
            self.ok = True  # built as soon as the window's loop runs
            GLib.idle_add(self._build)
            return
        ready = threading.Event()

        def run():
            try:
                Gtk.init([])
                self._build()
            finally:
                ready.set()
            if self.tray:
                Gtk.main()
        threading.Thread(target=run, daemon=True, name="desktop").start()
        ready.wait(10)
        self.ok = self.tray is not None

    def _build(self) -> bool:
        try:
            self.Notify.init("mmbox")
        except Exception as e:
            log(f"notifications unavailable: {e}")
        watcher = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None).call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
            self.GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)), None, self.Gio.DBusCallFlags.NONE, 2000, None)[0]
        errors = []
        for kind in ((SniTray, AyatanaTray) if watcher else (AyatanaTray, SniTray)):
            try:
                self.tray = kind(self)
                break
            except Exception as e:
                errors.append(f"{kind.__name__}: {type(e).__name__}: {e}")
        if self.tray is None:
            log("no icon in the notification area (" + "; ".join(errors) + "): install gir1.2-dbusmenu-glib-0.4 "
                "(or gir1.2-ayatanaappindicator3-0.1)")
            self.ok = False
        if self.app:
            self.app.can_hide = self.tray is not None
            if self.tray is None and not self.app.visible:
                self.app.show()  # without an icon, nothing else could open it
        return False

    # ---- the menu: (key, label, action); its state follows the window and the questions
    def menu(self) -> list[tuple]:
        return [("open", "Open the console", self.open),
                ("browser", "Open the local site in the browser", self.open_browser),
                ("questions", "No question waiting", lambda: self.open(question=True)),
                ("held", "No message held", lambda: self.open(held=True)),
                ("hide", "Hide in the notification area", self.hide),
                ("sep", None, None),
                ("quit", "Quit the console", self.on_quit)]

    def menu_state(self) -> dict:
        n, app = self.pending_n, self.app
        return {"open": ("Open the console" if app else "Open the console in the browser", True, True),
                "browser": (None, True, bool(app)),
                "questions": (f"Answer the questions ({n})" if n else "No question waiting", bool(n), True),
                "held": (f"See the held messages ({self.held_n})", True, bool(self.held_n)),
                "hide": (None, bool(app and app.visible), bool(app and app.can_hide))}

    def _refresh(self) -> bool:
        if self.tray:
            self.tray.refresh()
        return False

    def toggle(self) -> None:
        """The icon's click: no window yet, or hidden, minimized or behind other windows: it opens in front; already in
        front: it folds back into the notification area (the console keeps running)."""
        if self.app and self.app.can_hide and self.app.in_front():
            self.app.hide()
        else:
            self.open()

    def open(self, question: bool | str = False, held: bool = False, msg: str | None = None, late: bool = False) -> None:
        """The console in front, on what is asked: one message, the questions (or one of them), the held messages, the
        late unread ones (the window only: the browser opens on the list)."""
        if self.app:
            self.app.show(question=question, held=held, msg=msg, late=late)
        else:
            self._browser(self.url_of(question, held, msg))

    def open_browser(self) -> None:
        self._browser(self.url_of(False))

    def hide(self) -> None:
        if self.app and self.app.can_hide:
            self.app.hide()

    @staticmethod
    def _browser(url: str) -> None:
        import webbrowser
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    def update(self, pending: list[dict], badge: bool, popup: bool) -> None:
        if self.ok:
            self.GLib.idle_add(self._update, [dict(q) for q in pending], badge, popup)

    def _icon(self) -> None:
        """The red dot while questions wait or messages are held (the badge setting), and the tooltip says which."""
        if not self.tray:
            return
        n, k = self.pending_n, self.held_n
        tip = ", ".join(x for x in (f"{n} question(s) waiting" if n else "", f"{k} message(s) held" if k else "") if x)
        self.tray.set_icon("mmbox-console-alert" if self.badge and (n or k) else "mmbox-console", tip or "mmbox")
        self.tray.refresh()

    def _update(self, pending, badge, popup):
        n = self.pending_n = len(pending)
        self.badge = badge
        self._icon()
        ids = {q["id"] for q in pending}
        if not (popup and n) or ids <= self.dismissed:  # nothing waits, a tab shows its own, or the user closed it
            if self.note is not None and not (popup and n):
                try:
                    self.note.close()
                except Exception:
                    pass
                self.note, self.shown = None, None
            return False
        last = pending[-1]
        title = "mmbox: 1 question waiting" if n == 1 else f"mmbox: {n} questions waiting"
        body = f"{last.get('title') or last.get('label') or last['from']}: {last.get('subject') or ''}"
        if (title, body) == self.shown:
            return False
        icon = str(self.icons / "mmbox-console-alert.svg")
        if self.note is None:
            note = self.Notify.Notification.new(title, body, icon)
            note.set_timeout(0)  # never expires: it stays until answered or closed
            note.set_hint("resident", self.GLib.Variant.new_boolean(True))
            note.set_hint("desktop-entry", self.GLib.Variant.new_string("mmbox-console"))
            note.add_action("default", "Answer", lambda *_: self.open(question=self.note_q or True))
            note.add_action("answer", "Answer", lambda *_: self.open(question=self.note_q or True))
            note.connect("closed", lambda nn, ids=ids: self._closed(nn, ids))
            self.note = note
        else:
            self.note.update(title, body, icon)
        self.note.ids, self.note_q = ids, last["id"]  # its click: the question it names
        try:
            self.note.show()
            self.shown = (title, body)
        except Exception as e:
            log(f"notification: {e}")
        return False

    def update_held(self, held: list[dict], badge: bool, popup: bool) -> None:
        if self.ok:
            self.GLib.idle_add(self._update_held, held, badge, popup)

    def _update_held(self, held, badge, popup):
        """Messages their senders keep queued because the recipient does not accept them yet (its computer has not
        run since): the tray's dot, and one resident notification, shown again only when another message is held."""
        self.held_n, self.badge = len(held), badge
        self._icon()
        ids = {m["id"] for m in held}
        if not (popup and held) or ids <= self.held_dismissed:
            if self.held_note is not None and not (popup and held):
                try:
                    self.held_note.close()
                except Exception:
                    pass
                self.held_note, self.held_shown = None, None
            return False
        to = {}
        for m in held:
            to.setdefault(m.get("to_name") or m.get("to") or "?", []).append(m)
        oldest = max(m["held"].get("age_s") or 0 for m in held)
        title = f"mmbox: {len(held)} message(s) held"
        body = "; ".join(f"{len(ms)} for {who}" for who, ms in sorted(to.items(), key=lambda x: -len(x[1]))[:4]) \
            + f". The oldest for {fmt_age(oldest)}: the recipient's computer has not accepted them yet; they leave by themselves when it runs."
        if self.held_shown == ids:  # the same messages: never popped up again because they got older
            return False
        icon = str(self.icons / "mmbox-console-alert.svg")
        if self.held_note is None:
            note = self.Notify.Notification.new(title, body, icon)
            note.set_timeout(0)
            note.set_urgency(self.Notify.Urgency.CRITICAL)
            note.set_hint("resident", self.GLib.Variant.new_boolean(True))
            note.set_hint("desktop-entry", self.GLib.Variant.new_string("mmbox-console"))
            note.add_action("default", "Show", lambda *_: self._open_held())
            note.add_action("held", "Show them", lambda *_: self._open_held())
            note.connect("closed", lambda nn, ids=ids: self._held_closed(nn, ids))
            self.held_note = note
        else:
            self.held_note.update(title, body, icon)
        self.held_note.ids = ids
        self.held_one = held[0]["id"] if len(held) == 1 else None
        try:
            self.held_note.show()
            self.held_shown = ids
        except Exception as e:
            log(f"notification: {e}")
        return False

    def _open_held(self) -> None:
        """The held notification's click: the message itself when it is the only one, else the list of them all."""
        if self.held_one:
            self.open(msg=self.held_one)
        else:
            self.open(held=True)

    def recap(self, r: dict) -> None:
        if self.ok:
            self.GLib.idle_add(self._recap, r)

    def alert(self, title: str, body: str, msg: str | None = None, late: bool = False) -> None:
        if self.ok:
            self.GLib.idle_add(self._alert, title, body, msg, late)

    def _alert(self, title, body, msg, late):
        """The page's own alerts (a message unread too long, one forwarded to a machine box) when it runs in the
        console's window, whose WebKit refuses the browser's notifications: shown by the system, not resident."""
        key = msg or ("late" if late else title)
        try:
            note = self.Notify.Notification.new(title, body[:300], str(self.icons / "mmbox-console.svg"))
            note.set_hint("desktop-entry", self.GLib.Variant.new_string("mmbox-console"))
            note.add_action("default", "Show", lambda *_: self.open(msg=msg, late=late))
            note.connect("closed", lambda *_: self.recap_notes.pop(key, None))
            note.show()
            self.recap_notes[key] = note  # kept until closed, or its action is lost with the object
            for old in list(self.recap_notes)[:-20]:
                self.recap_notes.pop(old, None)
        except Exception as e:
            log(f"notification: {e}")
        return False

    def _recap(self, r):
        """A session's work is over: one notification, not resident (the recap stays in the list)."""
        who = r.get("title") or r.get("label") or r.get("from") or "a session"
        host = (r.get("host") or {}).get("hostname")
        try:
            note = self.Notify.Notification.new(f"mmbox: {who}" + (f" on {host}" if host else "") + " is done",
                                                str(r.get("subject") or "")[:200], str(self.icons / "mmbox-console.svg"))
            note.set_hint("desktop-entry", self.GLib.Variant.new_string("mmbox-console"))
            mid = r.get("id")
            note.add_action("default", "Show", lambda *_, mid=mid: self.open(msg=mid) if mid else self.open())
            note.connect("closed", lambda *_, mid=mid: self.recap_notes.pop(mid, None))
            note.show()
            self.recap_notes[mid] = note  # kept (each one, until closed), or its action is lost with the object
            for old in list(self.recap_notes)[:-20]:
                self.recap_notes.pop(old, None)
        except Exception as e:
            log(f"notification: {e}")
        return False

    def _held_closed(self, note, ids):
        if note is self.held_note:
            self.held_dismissed |= getattr(note, "ids", ids)
            self.held_note, self.held_shown = None, None

    def _closed(self, note, ids):
        if note is self.note:
            self.dismissed |= getattr(note, "ids", ids)  # shown again only for a new question
            self.note, self.shown = None, None

    @staticmethod
    def play(path: Path) -> None:
        import shutil
        for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["pw-play"], ["paplay"], ["mpg123", "-q"]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd + [str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=NOWIN)
                except OSError:
                    continue
                return


def tray_svg(dot: bool) -> str:
    """The console's tray icon (static/icon-small.svg, the logo's thick-stroke drawing for small sizes, also the tab's
    and the header's), with the red dot when something waits."""
    svg = (STATIC / "icon-small.svg").read_text(encoding="utf-8").rstrip()
    if dot:
        svg = svg[:-len("</svg>")] + '<circle cx="51" cy="13" r="12" fill="#e5383b" stroke="#121212" stroke-width="3"/></svg>'
    return svg


# ---------------------------------------------------------------- files attached to messages
# The model keeps what the attachments are (name, type, size, sha256) and where their file is on this computer (`path`:
# a box's <profile>/attachments/, written by the client when it logged the message, or the console's own attachments/
# for an unread message read in a waiting box). Never their content: /api/attachment serves the file itself.
RASTER = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp")


def sniff_image(head: bytes) -> str | None:
    """The raster image type a file's first bytes say, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and (head[8:12] in (b"avif", b"avis") or b"avif" in head[16:64]):
        return "image/avif"
    if head[:2] == b"BM" and len(head) >= 26:
        return "image/bmp"
    return None


def att_save(a: dict, home: Path) -> str | None:
    """An attachment that still carries its content (an unread message, decrypted here): checked and kept once under
    the console's attachments/<sha256>; its path, or None when it does not check."""
    sha = str(a.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    folder = home / "attachments"
    f = folder / sha
    if f.is_file() and f.stat().st_size == a.get("size"):
        return str(f)
    try:
        raw = base64.b64decode(str(a.get("data") or "").encode(), validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != a.get("size") or hashlib.sha256(raw).hexdigest() != sha or len(raw) > 4_000_000:
        return None
    folder.mkdir(exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    tmp = folder / f".{sha}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, f)
    return str(f)


def att_meta(atts, home: Path, local: bool = False) -> list[dict]:
    """The model's copy of a payload's attachments: metadata and local path, never the base64. A `path` is taken only
    from a box's own log (`local`): anywhere else it would be the sender's word."""
    out = []
    for a in (atts if isinstance(atts, list) else [])[:getattr(M, "ATTACH_MAX_FILES", 8)]:
        if not isinstance(a, dict):
            continue
        x = {"name": str(a.get("name") or "file")[:120], "type": str(a.get("type") or "application/octet-stream")[:100],
             "size": a.get("size") if isinstance(a.get("size"), int) else None,
             "sha256": str(a.get("sha256") or "")[:64] or None}
        if local and isinstance(a.get("path"), str):
            x["path"] = a["path"]
        elif a.get("data"):
            try:
                x["path"] = att_save(a, home)
            except OSError as e:
                log(f"attachment {x['name']}: {e}")
            if not x.get("path"):
                x["error"] = "its content does not check"
        if a.get("error"):
            x["error"] = str(a["error"])[:200]
        out.append({k: v for k, v in x.items() if v is not None})
    return out


def att_roots(home: Path) -> tuple[Path, Path]:
    return Path(os.path.realpath(M.base_dir())), Path(os.path.realpath(home / "attachments"))


def att_allowed(path: str, home: Path) -> Path | None:
    """The real file behind `path` when it lies in an attachment folder: <mmbox home>/<profile>/attachments/, or the
    console's own attachments/. None otherwise (symbolic links resolved first)."""
    f = Path(os.path.realpath(path))
    base, mine = att_roots(home)
    if mine in f.parents:
        return f
    try:
        parts = f.relative_to(base).parts
    except ValueError:
        return None
    return f if len(parts) >= 4 and parts[1] == "attachments" and ".." not in parts else None


# ---------------------------------------------------------------- conversations: what the console keeps of each box
class Archive:
    """The console's own copy of each box's conversation (archive.sqlite in its folder): every message a box sent or
    received, kept until its conversation's expiry (the default, or the conversation's own override), or for good while
    the conversation is pinned. Unpinned, every kept message starts its expiry again from that moment. A message whose
    expiry passed in a conversation is gone from it for good (a marker stays, so that the logs do not bring it back)."""

    def __init__(self, home: Path):
        import sqlite3
        self.db = sqlite3.connect(str(home / "archive.sqlite"), check_same_thread=False, isolation_level=None)
        if os.name != "nt":
            os.chmod(home / "archive.sqlite", 0o600)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS msgs (id TEXT PRIMARY KEY, data TEXT NOT NULL, size INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS conv (box TEXT NOT NULL, id TEXT NOT NULL, received REAL NOT NULL,
                                                 expires REAL, expired INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (box, id));
                CREATE TABLE IF NOT EXISTS convset (box TEXT PRIMARY KEY, ttl_s REAL, pinned INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS purged (id TEXT PRIMARY KEY, at REAL NOT NULL);
            """)

    def purged(self) -> set:
        """The messages the purge removed: hidden from the list even when a collector still holds an event of them."""
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT id FROM purged")}

    def mark_purged(self, ids, forget_after_s: float) -> None:
        """A marker for each of `ids`; the markers older than `forget_after_s` go (nothing can bring those back)."""
        now = time.time()
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("INSERT OR REPLACE INTO purged (id, at) VALUES (?, ?)", [(i, now) for i in ids])
            self.db.execute("DELETE FROM purged WHERE at < ?", (now - forget_after_s,))
            self.db.execute("COMMIT")

    def settings(self, box: str) -> tuple[float | None, bool]:
        r = self.db.execute("SELECT ttl_s, pinned FROM convset WHERE box=?", (box,)).fetchone()
        return (r[0], bool(r[1])) if r else (None, False)

    def pinned(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT box FROM convset WHERE pinned=1")]

    def overrides(self) -> dict:
        with self.lock:
            return {r[0]: r[1] for r in self.db.execute("SELECT box, ttl_s FROM convset WHERE ttl_s IS NOT NULL")}

    def ingest(self, msgs, default_ttl: float) -> None:
        """Every message of the merged model, into the conversation of its sender and of its recipient(s)."""
        now = time.time()
        with self.lock:
            sets = {r[0]: (r[1], bool(r[2])) for r in self.db.execute("SELECT box, ttl_s, pinned FROM convset")}
            rows_m, rows_c = [], []
            for m in msgs:
                if m.get("tier", 3) > 2:
                    continue
                data = json.dumps(m, ensure_ascii=False, default=str)
                rows_m.append((m["id"], data, len(data)))
                try:
                    received = M.parse_iso(m.get("ts")).timestamp()
                except (TypeError, ValueError, AttributeError):
                    received = now
                for b in {m.get("from"), m.get("to")} - {None, ""}:
                    ttl, pin = sets.get(b, (None, False))
                    rows_c.append((b, m["id"], received, None if pin else received + (ttl or default_ttl)))
            self.db.execute("BEGIN")
            self.db.executemany("INSERT INTO msgs (id, data, size) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                                "data=excluded.data, size=excluded.size WHERE excluded.size >= msgs.size", rows_m)
            self.db.executemany("INSERT OR IGNORE INTO conv (box, id, received, expires) VALUES (?, ?, ?, ?)", rows_c)
            self.db.execute("UPDATE conv SET expired=1 WHERE expired=0 AND expires IS NOT NULL AND expires < ?", (now,))
            self.db.execute("DELETE FROM msgs WHERE id NOT IN (SELECT id FROM conv WHERE expired=0)")
            self.db.execute("COMMIT")

    def drop(self, ids) -> int:
        """These messages gone from the console's copy (a box's own purge); a marker stays for each (mark_purged)."""
        ids = [(i,) for i in ids]
        if not ids:
            return 0
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("UPDATE conv SET expired=1 WHERE id=?", ids)
            n = sum(self.db.execute("DELETE FROM msgs WHERE id=?", i).rowcount for i in ids)
            self.db.execute("COMMIT")
        return n

    def purge(self, keep: set) -> int:
        """Every kept message but those of `keep` gone, pinned conversations included; a marker stays for each, so
        that the boxes' logs do not bring them back. The file is compacted after."""
        with self.lock:
            gone = [(r[0],) for r in self.db.execute("SELECT id FROM msgs") if r[0] not in keep]
            self.db.execute("BEGIN")
            self.db.executemany("UPDATE conv SET expired=1 WHERE id=?", gone)
            self.db.executemany("DELETE FROM msgs WHERE id=?", gone)
            self.db.execute("COMMIT")
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return len(gone)

    def get(self, mid: str) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT data FROM msgs WHERE id=?", (mid,)).fetchone()
        return json.loads(r[0]) if r else None

    def chat(self, box: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT m.data, c.received, c.expires FROM conv c JOIN msgs m ON m.id=c.id "
                                   "WHERE c.box=? AND c.expired=0 AND (c.expires IS NULL OR c.expires > ?)", (box, time.time())).fetchall()
        out = []
        for data, received, expires in rows:
            m = json.loads(data)
            m["kept_until"] = expires
            out.append(m)
        return sorted(out, key=lambda m: m.get("ts") or "")

    def set_ttl(self, box: str, ttl_s: float | None, default_ttl: float) -> None:
        """This conversation's own expiry (None: back to the default), counted from each message's reception."""
        with self.lock:
            self.db.execute("INSERT INTO convset (box, ttl_s) VALUES (?, ?) ON CONFLICT(box) DO UPDATE SET ttl_s=excluded.ttl_s",
                            (box, ttl_s))
            pinned = self.db.execute("SELECT pinned FROM convset WHERE box=?", (box,)).fetchone()[0]
            if not pinned:
                self.db.execute("UPDATE conv SET expires = received + ? WHERE box=? AND expired=0", (ttl_s or default_ttl, box))

    def set_pin(self, box: str, pinned: bool, default_ttl: float) -> None:
        """Pinned: nothing of this conversation expires. Unpinned: every kept message starts its expiry again now."""
        now = time.time()
        with self.lock:
            self.db.execute("INSERT INTO convset (box, pinned) VALUES (?, ?) ON CONFLICT(box) DO UPDATE SET pinned=excluded.pinned",
                            (box, int(pinned)))
            ttl = self.db.execute("SELECT ttl_s FROM convset WHERE box=?", (box,)).fetchone()[0] or default_ttl
            if pinned:
                self.db.execute("UPDATE conv SET expires=NULL WHERE box=? AND expired=0", (box,))
            else:
                self.db.execute("UPDATE conv SET received=?, expires=? WHERE box=? AND expired=0", (now, now + ttl, box))


class State:
    def __init__(self, op: Operator, days: float):
        self.op, self.days = op, days
        self.lock = threading.Lock()
        self.refreshing = threading.Lock()
        self.logs = LogCache()
        self.col: dict[str, dict] = {}  # url -> {"cursor", "events": deque}
        self.cards: dict[str, dict] = self._load_cards()
        self.snapshot: dict = {"version": 0}
        self.messages: dict[str, dict] = {}
        self._usage: tuple[float, dict] = (0.0, {})
        self.digest = ""
        self.errors: list[str] = []
        self.startup: dict | None = None  # what the operator is asked once, at the opening (collector retention)
        self.seen_path = op.home / "state.json"
        try:
            self.console_state = json.loads(self.seen_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.console_state = {}
        self.last_open = float(self.console_state.get("last_seen") or 0)
        self._seen_saved = 0.0
        self.questions = Questions(op.home)
        self.recaps = Recaps(op.home)
        self.archive = Archive(op.home)
        self.purged: set = self.archive.purged()
        self.clients: dict[str, float] = {}  # when a browser tab last said it shows notifications / plays the sound
        self.desktop: Desktop | None = None
        self.known_q: set | None = None  # the questions seen at the last tick (None: not yet: no sound at the opening)
        self.rights: dict[str, dict] = {}
        self.delivery: dict[str, dict] = {}  # message id -> {"st": consumed|taken|waiting|unknown, "at": when asked}

    def save_console_state(self) -> None:
        tmp = self.seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.console_state), encoding="utf-8")
        os.replace(tmp, self.seen_path)

    def conv_default_ttl(self) -> float:
        """How long a conversation keeps its messages by default: the longest retention of the collectors this
        console reads, else MMBOX_CONSOLE_KEEP_DAYS (7)."""
        days = [float((s.get("col_conf") or {}).get("keep_days") or 0) for s in self.snapshot.get("servers") or []
                if s.get("operator")]
        return (max(days) if days and max(days) > 0 else float(os.environ.get("MMBOX_CONSOLE_KEEP_DAYS", "7"))) * 86400

    def notify_settings(self) -> dict:
        st = {**NOTIFY_DEFAULT, **(self.console_state.get("notify") or {})}
        st["sound"] = OLD_SOUND_NAMES.get(st["sound"], st["sound"])
        return st

    def sound_path(self) -> Path | None:
        st = self.notify_settings()
        if st["sound"] == "custom":
            p = self.op.home / "sounds" / "custom.mp3"
            return p if p.is_file() else None
        p = STATIC / "sounds" / f"{st['sound']}.mp3"
        return p if st["sound"] in SOUNDS and p.is_file() else None

    def tick(self) -> None:
        """After each refresh and each answer: the icon, the persistent notification, the sound of a new question."""
        pend = self.questions.pending()
        ids = {q["id"] for q in pend}
        new = ids - self.known_q if self.known_q is not None else set()
        self.known_q = ids
        st = self.notify_settings()
        now = time.time()
        tab_notify = now - self.clients.get("notify", 0) < 15
        tab_audio = now - self.clients.get("audio", 0) < 15
        if self.desktop:
            self.desktop.update(pend, badge=st["badge"], popup=st["popup"] and not tab_notify)
            self.desktop.update_held(self.held(), badge=st["badge"], popup=st["popup"] and not tab_notify)
        if new and st["audio"] and not tab_audio and (p := self.sound_path()):
            Desktop.play(p)

    def held(self) -> list[dict]:
        """The messages held long enough to be shown loud: their senders keep them queued, the service refuses them."""
        now_ms = int(time.time() * 1000)
        with self.lock:
            boxes = {b["id"]: b for b in self.snapshot.get("boxes") or []}
            name = lambda i: (lambda b: " on ".join(x for x in (b.get("title") or b.get("label") or i, b.get("hostname")) if x))(boxes.get(i) or {})  # noqa: E731
            out = [{**{k: m.get(k) for k in ("id", "from", "to", "kind", "subject", "tier")}, "to_name": name(m.get("to")),
                    "held": {**m["held"], "age_s": (now_ms - m["held"]["since"]) / 1000}}
                   for m in self.messages.values() if m.get("held") and not m["held"].get("ack")
                   and now_ms - m["held"]["since"] >= HELD_AFTER_S * 1000]
        return sorted(out, key=lambda m: m["held"]["since"])

    def waiting_ids(self) -> set:
        """The messages still in a box: sent and not consumed yet, unread, taken, or queued by their sender."""
        with self.lock:
            return {mid for mid, m in self.messages.items() if m.get("state") in WAITING}

    def usage(self, fresh: bool = False) -> dict:
        """What the services' Redis (where this console is an operator) and this computer hold, for the explorer's
        frame and the memory view. Cached a minute, unless `fresh`."""
        at, cached = self._usage
        if cached and not fresh and time.time() - at < 60:
            return cached

        def one(srv):
            row = {"name": srv["name"], "url": srv["url"]}
            if not srv.get("operator"):
                row["why"] = "this console is no operator of it"
            elif not srv.get("ok"):
                row["why"] = "unreachable"
            elif "usage" not in (srv.get("features") or []):
                row["why"] = "its gateway predates the memory figures: update it"
            else:
                r = call(srv["url"], self.op, "/usage", {"fresh": fresh})
                if r.get("_error") is not None:
                    row["why"] = f"{r['_error']} {r.get('error', '')}".strip()
                else:
                    row["redis"] = {k: r.get(k) for k in ("used", "peak", "max", "policy", "dataset", "keys", "boxes",
                                                           "channels", "collector", "stats", "at_ms", "acl")}
            return row
        import concurrent.futures as cf
        servers = list(self.snapshot.get("servers") or [])
        with cf.ThreadPoolExecutor(max(1, min(8, len(servers)))) as ex:
            rows = list(ex.map(one, servers))
        out = {"servers": rows, "local": self.local_usage(), "at": time.time()}
        self._usage = (time.time(), out)
        return out

    def debug_all(self) -> bool:
        """Debug mode (Settings): the list keeps every message seen, consumed ones included. Off: only the useful ones."""
        return bool(self.console_state.get("debug_all"))

    def own_titlebar(self) -> bool:
        """The app's window draws its own title bar (Settings; on by default) instead of the system's."""
        return self.console_state.get("own_titlebar") is not False

    def purge_keep_s(self) -> float:
        """How much of the open boxes' finished messages the purge keeps (Settings; 24 h by default)."""
        try:
            v = float(self.console_state.get("purge_keep_s"))
        except (TypeError, ValueError):
            return PURGE_KEEP_DEFAULT_S
        return min(max(v, 0.0), PURGE_KEEP_MAX_S)

    def purge_plan(self) -> dict:
        """What the purge removes: every message of the list that no longer waits in a box (or was only "sent" by a
        box that closed since), but those younger than the kept delay that an open box sent, received or saw (its
        session may still answer them)."""
        keep_s = self.purge_keep_s()
        cut = time.time() - keep_s
        with self.lock:
            msgs = dict(self.messages)
            boxes = {b["id"]: b for b in self.snapshot.get("boxes") or []}
        open_ids = {i for i, b in boxes.items() if b.get("active") or (b.get("local") and not b.get("closed_at"))}

        def recent_open(m):
            t = _ms(m.get("ts"))
            if t is None or t / 1000 < cut:
                return False
            rel = {m.get("from"), m.get("to"), m.get("read_by"), (m.get("taken_by") or {}).get("id"),
                   *(m.get("to_boxes") or []), *(m.get("seen_by") or [])}
            return bool(rel & open_ids)
        def to_operator(m):
            return m.get("kind") in ("question", "recap") or m.get("to") == self.op.id or bool(m.get("to_operator"))

        def still_waiting(m):
            # "sent" by a box that is no longer open: its copy in the recipient's box is delivered without the
            # sender's log, and a deleted sender can no longer ask the service whether it was consumed
            st = m.get("state")
            return st in WAITING and not (st == "sent" and m.get("from") not in open_ids)
        waiting = {i for i, m in msgs.items() if still_waiting(m)}
        # what was written to the operator (questions, recaps) never perishes: only its own deletion removes it
        gone = {i for i, m in msgs.items() if i not in waiting and not recent_open(m) and not to_operator(m)}
        return {"keep_s": keep_s, "cut": cut, "msgs": msgs, "waiting": waiting, "gone": gone, "open": open_ids}

    def local_usage(self) -> dict:
        """This computer: the console's folder (its copy of the messages, their attached files, the window's data)
        and the boxes' folder (their keys, logs and attached files)."""
        home, boxes_dir = self.op.home, Path(M.base_dir())
        archive = sum(dir_size(home / f"archive.sqlite{x}") for x in ("", "-wal", "-shm"))
        atts, wv_home = dir_size(home / "attachments"), dir_size(home / "webview")
        webkit, total_home = sum(dir_size(d) for d in webkit_dirs()), dir_size(home)
        plan = self.purge_plan()
        with self.archive.lock:
            ids = [r[0] for r in self.archive.db.execute("SELECT id FROM msgs")]
        console = {"archive": archive, "attachments": atts, "window": wv_home + webkit,
                   "other": max(0, total_home - archive - atts - wv_home), "total": total_home + webkit, "path": str(home)}
        logs, closed = 0, 0
        for d in boxes_dir.iterdir() if boxes_dir.is_dir() else []:
            f = d / "log.jsonl"
            if f.is_file() and f.stat().st_size:
                logs += f.stat().st_size
                try:
                    closed += bool((json.loads((d / "config.json").read_text(encoding="utf-8")) or {}).get("closed_at"))
                except (OSError, ValueError):
                    pass
        boxes = {"total": dir_size(boxes_dir), "path": str(boxes_dir), "logs": logs, "closed_with_log": closed,
                 "profiles": sum(1 for d in boxes_dir.iterdir() if d.is_dir()) if boxes_dir.is_dir() else 0}
        return {"console": console, "boxes": boxes, "total": console["total"] + boxes["total"],
                "messages": len(ids), "listed": len(plan["msgs"]), "waiting": len(plan["waiting"]),
                "purgeable": len(plan["gone"]), "keep_s": plan["keep_s"],
                "kept_open": len(plan["msgs"]) - len(plan["gone"]) - len(plan["waiting"])}

    @staticmethod
    def _purge_log(y, drop) -> tuple[int, int]:
        """y's log.jsonl rewritten without the entries `drop(entry)` names, under the profile's own lock (a listener
        appending meanwhile waits); the attached files of the messages no entry names any more go too.
        Returns (entries removed, attachment folders removed)."""
        p = y.local.path("log.jsonl")
        removed, gone, kept_ids = 0, set(), set()
        with y.local.locked():
            if not p.is_file():
                return 0, 0
            kept = []
            for line in p.read_bytes().splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    kept.append(line + b"\n")
                    continue
                mid = e.get("id") or (e.get("payload") or {}).get("id") if isinstance(e, dict) else None
                if isinstance(e, dict) and drop(e, mid):
                    removed += 1
                    gone.add(mid)
                else:
                    kept.append(line + b"\n")
                    kept_ids.add(mid)
            if removed:
                tmp = p.with_name(p.name + ".purge")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.writelines(kept)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, p)
        folders = 0
        name_of = getattr(M, "_attach_dir_name", None)
        root = y.local.dir / "attachments"
        if name_of and root.is_dir():
            import shutil
            for mid in gone - kept_ids - {None}:
                d = root / name_of(mid)
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                    folders += 1
        return removed, folders

    def purge_everything(self) -> dict:
        """The memory view's first button, a real purge: every message that no longer waits in a box goes from this
        computer for good - the boxes' logs (a closed box's log is emptied of everything but what still waits), their
        attached files, the console's copy - but what an open box handled within the kept delay (Settings). A marker
        stays for each, so that a collector's events do not bring them back to the list."""
        plan = self.purge_plan()
        msgs, waiting, gone, cut = plan["msgs"], plan["waiting"], plan["gone"], plan["cut"]
        before = self.local_usage()["total"]
        entries = folders = logs = 0
        errors = []
        for n in M.local_profiles():
            y = M.load_me(n)
            if not y:
                continue
            closed = bool(y.closed_at) and y.id not in plan["open"]
            outbox = {i.get("msg_id") for i in (y.local.load("state.json", {}) or {}).get("outbox") or []}

            def drop(e, mid, closed=closed, outbox=outbox):
                if not mid or mid in waiting or mid in outbox:
                    return False
                if closed or mid in gone:
                    return True
                if mid in msgs:  # in the list and kept: an open box's recent message
                    return False
                try:  # older than the list's horizon: by its own date
                    return M.parse_iso((e.get("payload") or {}).get("ts") or e.get("at")).timestamp() < cut
                except (TypeError, ValueError, AttributeError):
                    return False
            try:
                r, f = self._purge_log(y, drop)
            except OSError as ex:
                errors.append(f"{n}: {ex}")
                continue
            entries, folders, logs = entries + r, folders + f, logs + bool(r)
        keep = set(msgs) - gone
        n_arch = self.archive.purge(keep)
        with self.lock:
            shas = {str(a.get("sha256") or "") for mid in keep for a in (self.messages.get(mid) or {}).get("attachments") or []}
        files = 0
        folder = self.op.home / "attachments"
        if folder.is_dir():
            for f in folder.iterdir():
                if f.is_file() and f.name not in shas:
                    f.unlink(missing_ok=True)
                    files += 1
        # the markers outlive what could bring a message back: the collectors' retention and the list's horizon
        self.archive.mark_purged(gone, max(self.days * 86400, self.conv_default_ttl(), plan["keep_s"]) + 86400)
        self.purged = self.archive.purged()
        self.logs = LogCache()  # the rewritten logs are read again from their start
        self._usage = (0.0, {})
        after = self.local_usage()["total"]
        log(f"purge: {len(gone)} message(s), {entries} log entries in {logs} box(es), {n_arch} console cop(ies)"
            + (f"; errors: {'; '.join(errors)}" if errors else ""))
        threading.Thread(target=self.refresh, daemon=True).start()
        out = (f"{len(gone)} message(s) purged: {entries} log entries in {logs} box(es), {folders + files} attached "
               f"file folder(s) and file(s), {n_arch} console cop(ies); {len(waiting)} still waiting kept, "
               f"{len(keep) - len(waiting & keep)} handled by an open box within {fmt_delay(plan['keep_s'])} kept; "
               f"{fmt_bytes(max(0, before - after))} freed.")
        return {"ok": not errors, "out": out + ("\nFailed: " + "; ".join(errors) if errors else ""), "err": "; ".join(errors) or None}

    def purge_box(self, bid: str) -> dict:
        """A box's menu, "Purge its history": what no longer waits among the messages this box of this computer sent,
        received or took, gone from its log, its attached files and the console's copy (as the memory view's purge,
        for this box alone: what still waits, and what an open box handled within the kept delay, stays)."""
        y = next((b for n in M.local_profiles() if (b := M.load_me(n)) and b.id == bid), None)
        if not y:
            return {"ok": False, "err": "not a box of this computer: its history is on its own computer"}
        plan = self.purge_plan()
        msgs, waiting, cut = plan["msgs"], plan["waiting"], plan["cut"]

        def mine(m):
            return bid in {m.get("from"), m.get("to"), m.get("read_by"), (m.get("taken_by") or {}).get("id"),
                           *(m.get("to_boxes") or []), *(m.get("seen_by") or [])}
        gone = {i for i in plan["gone"] if mine(msgs[i])}
        outbox = {i.get("msg_id") for i in (y.local.load("state.json", {}) or {}).get("outbox") or []}

        def drop(e, mid):
            if not mid or mid in waiting or mid in outbox:
                return False
            if mid in gone:
                return True
            if mid in msgs:
                return False
            try:  # older than the list's horizon: by its own date
                return M.parse_iso((e.get("payload") or {}).get("ts") or e.get("at")).timestamp() < cut
            except (TypeError, ValueError, AttributeError):
                return False
        try:
            entries, folders = self._purge_log(y, drop)
        except OSError as ex:
            return {"ok": False, "err": f"{y.local.profile}: {ex}"}
        n_arch = self.archive.drop(gone)
        self.archive.mark_purged(gone, max(self.days * 86400, self.conv_default_ttl(), plan["keep_s"]) + 86400)
        self.purged = self.archive.purged()
        self.logs = LogCache()
        self._usage = (0.0, {})
        log(f"purge of {y.local.profile}: {len(gone)} message(s), {entries} log entries, {n_arch} console cop(ies)")
        threading.Thread(target=self.refresh, daemon=True).start()
        return {"ok": True, "out": f"{y.label}: {len(gone)} message(s) purged ({entries} log entries, {folders} attached "
                                   f"file folder(s), {n_arch} console cop(ies)); what still waits, and what an open box "
                                   f"handled within {fmt_delay(plan['keep_s'])}, kept."}

    def _patch_snapshot(self, fn) -> None:
        """The published snapshot changed at once (fn: a light message -> it, changed, or None to drop it), under a
        new version, so that the page's next poll does not bring back the old state before the refresh rebuilds it."""
        with self.lock:
            snap = self.snapshot
            if not snap.get("messages"):
                return
            msgs = [x for m in snap["messages"] if (x := fn(m)) is not None]
            self.snapshot = {**snap, "messages": msgs, "version": snap.get("version", 0) + 1}
            self.digest = ""  # the next refresh publishes, even if it finds what it had before

    def delete_messages(self, ids) -> dict:
        """The open message's menu (one message) or the reader's bin (its whole thread): these messages gone from this
        computer for good - every local box's log, their attached files, the console's copy - with a marker for each
        so that a collector's events do not bring them back. What still waits in a box, or in an outbox, stays: its
        recipient has not taken it yet, and the list would show it again anyway."""
        ids = {str(i) for i in ids or [] if i}
        if not ids:
            return {"ok": False, "err": "no message named"}
        with self.lock:
            msgs = {i: self.messages.get(i) for i in ids}
        waiting = {i for i, m in msgs.items() if m and m.get("state") in WAITING}
        # what still waits unread in a box of this computer is taken out of that box first, unread
        unboxed, unbox_err = self._unbox(msgs, waiting)
        waiting -= unboxed
        outbox = set()
        profiles = [y for n in M.local_profiles() if (y := M.load_me(n))]
        for y in profiles:
            outbox |= {i.get("msg_id") for i in (y.local.load("state.json", {}) or {}).get("outbox") or []}
        gone = ids - waiting - outbox
        entries = folders = 0
        errors = list(unbox_err)
        if gone:
            for y in profiles:
                try:
                    r, f = self._purge_log(y, lambda e, mid: mid in gone)
                except OSError as ex:
                    errors.append(f"{y.local.profile}: {ex}")
                    continue
                entries, folders = entries + r, folders + f
            n_arch = self.archive.drop(gone)
            self.recaps.remove(gone)
            # the console's copies of their attached files, unless a message that stays has the same file
            with self.lock:
                kept = {str(a.get("sha256") or "") for mid, m in self.messages.items() if mid not in gone
                        for a in (m or {}).get("attachments") or []}
            shas = {str(a.get("sha256") or "") for m in msgs.values() for a in (m or {}).get("attachments") or []}
            folder = self.op.home / "attachments"
            for sha in shas - kept - {""}:
                f = folder / sha
                if f.is_file():
                    f.unlink(missing_ok=True)
                    folders += 1
            self.archive.mark_purged(gone, max(self.days * 86400, self.conv_default_ttl(), self.purge_keep_s()) + 86400)
            self.purged = self.archive.purged()
            self.logs = LogCache()
            self._usage = (0.0, {})
            with self.lock:  # gone from the list at once, not at the next refresh
                self.messages = {i: m for i, m in self.messages.items() if i not in gone}
            self._patch_snapshot(lambda m: None if m.get("id") in gone else m)
            log(f"delete: {len(gone)} message(s), {entries} log entries, {n_arch} console cop(ies)"
                + (f"; errors: {'; '.join(errors)}" if errors else ""))
            threading.Thread(target=self.refresh, daemon=True).start()
        kept_n = len(ids) - len(gone)
        out = (f"{len(gone)} message(s) deleted from this computer" if gone else "Nothing deleted") \
            + (f", {len(unboxed)} of them taken out of their box unread" if unboxed else "") \
            + (f"; {kept_n} kept: still waiting in another computer's box or in its sender's outbox" if kept_n else "") + "."
        return {"ok": not errors, "out": out, "deleted": sorted(gone), "kept": sorted(ids - gone),
                "err": "; ".join(errors) or None}

    def move_messages(self, ids, to: str) -> dict:
        """The list's batch action: these messages, waiting unread in boxes of this computer, handed to another open box
        of this computer (`mmbox.py move`: a forward keeping their first sender, its rights and its date). The others
        (taken already, in another computer's box, in an outbox) stay where they are."""
        ids = {str(i) for i in ids or [] if i}
        dest = next((y for n in M.local_profiles() if (y := M.load_me(n)) and y.id == to and not y.closed_at), None)
        if not dest:
            return {"ok": False, "err": "the destination must be an open box of this computer"}
        with self.lock:
            msgs = {i: self.messages.get(i) or {} for i in ids}
        groups: dict[str, list] = {}
        for i, m in msgs.items():
            prof = next((s.split(":", 1)[1] for s in m.get("sources") or [] if s.startswith("waiting:")), None)
            if m.get("state") == "unread" and prof and prof != dest.local.profile:
                groups.setdefault(prof, []).append(i)
        moved, errors = set(), []
        for prof, part in groups.items():
            r = run_client(["--profile", prof, "move", *part, "--to", dest.local.profile, "--json"], timeout=180)
            line = next((x for x in reversed((r.get("out") or "").splitlines()) if x.startswith("{")), "")
            try:
                moved |= {d.get("id") for d in json.loads(line).get("moved") or []}
            except ValueError:
                errors.append(f"{prof}: {(r.get('err') or r.get('out') or '?').strip().splitlines()[-1:]}")
        kept = ids - moved
        if moved:
            log(f"move: {len(moved)} message(s) to {dest.label}")
            threading.Thread(target=self.refresh, daemon=True).start()
        out = (f"{len(moved)} message(s) moved to {dest.label}" if moved else "Nothing moved") \
            + (f"; {len(kept)} kept where they are: only a message still waiting unread in a box of this computer moves"
               if kept else "") + "."
        return {"ok": bool(moved) or not errors, "out": out, "moved": sorted(moved), "kept": sorted(kept),
                "err": "; ".join(errors) or None}

    def _unbox(self, msgs: dict, ids: set) -> tuple[set, list]:
        """These messages, waiting unread (or taken, not consumed) in a box of this computer, taken out of it unread:
        `backlog purge` of that box (their senders told; the tool's own notices quietly); what the client does not
        handle (not from one of its peers, does not open) is removed by the box's own keys (/done). What waits in
        another computer's box, or in an outbox, is not this computer's to remove."""
        quiet_kinds = set(M.QUIET_KINDS) | {"introduce"}
        groups: dict[str, list] = {}
        for i in ids:
            m = msgs.get(i) or {}
            if m.get("state") not in ("unread", "taken"):
                continue
            prof = next((s.split(":", 1)[1] for s in m.get("sources") or [] if s.startswith("waiting:")), None)
            if prof:
                groups.setdefault(prof, []).append(m)
        done, errors = set(), []
        for prof, ms in groups.items():
            for quiet in (True, False):
                part = [m["id"] for m in ms if (m.get("kind") in quiet_kinds) == quiet]
                if not part:
                    continue
                r = run_client(["--profile", prof, "backlog", "purge", "--box", "all", "--json"]
                               + (["--quiet"] if quiet else []) + part, timeout=180)
                out, items = r.get("out") or "", []
                for k in [0] + [j + 1 for j, c in enumerate(out) if c == "\n"]:  # the JSON list, after any note
                    if out.startswith("[", k):
                        try:
                            items = json.loads(out[k:])
                            break
                        except ValueError:
                            continue
                done |= {it.get("id") for it in items if isinstance(it, dict) and it.get("status") == "purged"}
            y = M.load_me(prof)
            for m in ms:
                if m["id"] in done:
                    continue
                if not y or not m.get("entry"):
                    errors.append(f"{m['id']}: not taken out of {prof}")
                    continue
                for u in m.get("servers") or []:
                    d = call(u, y, "/done", {"box": y.id, "entries": [m["entry"]], "handled": {m["entry"]: "deleted"}})
                    if d.get("ok"):
                        done.add(m["id"])
                        break
                else:
                    errors.append(f"{m['id']}: not taken out of {prof}")
        if done:
            log(f"delete: {len(done)} waiting message(s) taken out of their box unread")
        return done, errors

    def held_ack(self, ids, on: bool) -> dict:
        """The held alert: these messages marked handled (out of the button, the panel, the explorer's badges, the
        tray and its notification) or put back in it. They stay queued by their senders; a mark goes with its message."""
        ids = {str(i) for i in ids or [] if i}
        if not ids:
            return {"ok": False, "err": "no message named"}
        now = int(time.time() * 1000)
        with self.lock:
            acks = dict(self.console_state.get("held_ack") or {})
            for i in ids:
                if on:
                    acks[i] = now
                else:
                    acks.pop(i, None)
            # a mark outlives its message by the list's horizon at most
            live = {i for i, m in self.messages.items() if m.get("held")}
            acks = {i: t for i, t in acks.items() if i in live or now - t < self.days * 86400e3}
            self.console_state["held_ack"] = acks
            for i in ids:  # at once, not at the next refresh
                m = self.messages.get(i)
                if m and m.get("held"):
                    m["held"] = {k: v for k, v in m["held"].items() if k != "ack"} | ({"ack": now} if on else {})

        def mark(m):
            if m.get("id") not in ids or not m.get("held"):
                return m
            return {**m, "held": {k: v for k, v in m["held"].items() if k != "ack"} | ({"ack": now} if on else {})}
        self._patch_snapshot(mark)
        self.save_console_state()
        threading.Thread(target=self.refresh, daemon=True).start()
        n = len(ids)
        return {"ok": True, "out": f"{n} held message(s) " + ("marked handled" if on else "back in the alert")}

    def box_command(self, bid: str, what: str) -> dict:
        """A box's menu: empty what waits in it (purged, senders told), close a session box, delete a project box (or
        a closed session box). The client does it with the box's own keys: for a box of another computer, its daemon
        does it there, asked with an op signed by this console's operator key (`op: box`)."""
        y = next((b for n in M.local_profiles() if (b := M.load_me(n)) and b.id == bid), None)
        if not y:
            return self.remote_box_command(bid, what)
        prof = y.local.profile
        if what == "empty":
            r = run_client(["--profile", prof, "backlog", "purge", "--box", "session", "--all"], timeout=180)
        elif what == "close":
            if y.role != "session" or y.closed_at:
                return {"ok": False, "err": "only an open session box is closed"}
            r = run_client(["--profile", M.MACHINE_PROFILE, "sessions", "--close", prof, "--reason", "closed from the console"])
        elif what == "delete":
            if y.role == "machine" or (y.role == "session" and not y.closed_at):
                return {"ok": False, "err": "only a project box, or a closed session box, is deleted"}
            r = run_client(["--profile", M.MACHINE_PROFILE, "sessions", "--remove", prof, "--reason", "deleted from the console"],
                           timeout=180)
        else:
            return {"ok": False, "err": f"unknown box command {what}"}
        self._usage = (0.0, {})
        threading.Thread(target=self.refresh, daemon=True).start()
        return r

    def daemon_of(self, b: dict) -> dict | None:
        """The daemon (butler box) of the computer a box of the snapshot runs on, if this console knows one."""
        mk = b.get("machine")
        return next((x for x in self.snapshot.get("boxes") or [] if mk and x.get("role") == "butler"
                     and x.get("machine") == mk), None)

    def remote_box_command(self, bid: str, what: str, wait_s: float = 45.0) -> dict:
        """A box's menu for a box of another computer: an op to that computer's daemon, then its answer awaited (the
        daemon reads its box every round, 15 s)."""
        b = self.snapshot_box(bid)
        if not b:
            return {"ok": False, "err": "unknown box"}
        if what not in ("empty", "close", "delete"):
            return {"ok": False, "err": "only its own computer purges a box's history"}
        d = self.daemon_of(b)
        if not d:
            return {"ok": False, "err": "its computer runs no mmbox daemon this console knows (its butler's box): only "
                                        "its own computer can do that"}
        r = self.send_op(d["id"], {"op": "box", "box": bid, "what": what})
        if not r.get("ok"):
            return r
        end = time.time() + wait_s
        while time.time() < end:
            res = (getattr(self, "op_results", {}) or {}).get(r["op_id"])
            if res:
                out = res.get("result") or {}
                threading.Thread(target=self.refresh, daemon=True).start()
                err = out.get("error")
                if err and "unknown op" in err:
                    err += " (its client predates remote box commands: update it, then restart its daemon)"
                return {"ok": not err, "out": out.get("out"), "err": err}
            time.sleep(0.5)
        return {"ok": True, "out": f"asked the daemon of {d.get('label') or d['id']}; no answer yet: its box shows the "
                                   f"change once it acted (never, if that computer has not pinned this console's "
                                   f"operator key: then its butler's chat says it got a message)"}

    # ---------------------------------------------------------------- the butler (client/BUTLER.md)
    def butler_on(self) -> bool:
        """Whether this console runs the computer's butler (Settings; on by default)."""
        return self.console_state.get("butler_run", True) is not False

    def ensure_butler(self) -> None:
        """The butler's daemon started when this console runs it and none runs on this computer (one per computer: a
        second one stops at once)."""
        if getattr(self, "_butler_proc", None) is not None and self._butler_proc.poll() is not None:
            self._butler_proc = None  # an ended butler this console started: reaped
        if not B or not self.butler_on() or not M.machine_me():
            return
        if B.status().get("running"):
            return
        if time.time() - getattr(self, "_butler_started", 0) < 120:
            return  # just started: its first beat comes
        self._butler_started = time.time()
        kw = {"creationflags": NOWIN | 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        self._butler_proc = subprocess.Popen([sys.executable, str(CLIENT), "daemon", "run"], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             env={**os.environ, "MMBOX_NO_SELF_UPDATE": "1"}, **kw)
        log("daemon started")

    def butler_state(self) -> dict:
        if not B:
            return {"available": False, "why": "the client has no butler yet (mmbox_daemon.py): update it (mmbox-update)"}
        c = B.config()
        return {"available": True, "run_with_console": self.butler_on(), **B.status(), "chat": B.chat_history(200),
                "op_results": getattr(self, "op_results", {}),
                "settings": {"butler_agent": c.get("agent"), "butler_model": c.get("model"),
                             "session_agent": self.console_state.get("session_agent"),
                             "session_model": self.console_state.get("session_model")}}

    def send_op(self, box: str, op: dict) -> dict:
        """An `op` to a computer's daemon (its butler's box), signed with this console's operator key: the only key
        its client accepts an op from. The answer comes back as an `op_result` in this console's box (op_results)."""
        b = self.snapshot_box(box) or {}
        urls = [u for u in b.get("servers") or [] if any(s.get("url") == u and s.get("operator") for s in self.snapshot.get("servers") or [])]
        if not urls:
            return {"ok": False, "err": "this console is no operator of a service that daemon is on"}
        url = urls[0]
        card = self.card(url, box)
        if not card or not card.get("box_pub"):
            return {"ok": False, "err": "its key is unknown on " + url}
        mid = M.now().strftime("%Y%m%dT%H%M%SZ") + "-" + M.rand_b32(6)
        payload = {"v": M.VERSION, "id": mid, "from": self.op.id, "from_user": self.op.user, "label": self.op.label,
                   "agent": "console", "to": box, "ts": M.iso(), "kind": "op", "subject": f"op {op.get('op')}",
                   "body": json.dumps(op, ensure_ascii=False), "reply_expected": True, "op": op,
                   **({"chain": ch} if (ch := account_chain()) else {})}  # a pinned account's daemon checks it
        env = M.envelope(self.op, {"id": box, "box_pub": card["box_pub"]}, payload, mid)
        r = call(url, self.op, "/send", {"to": box, "env": env})
        if r.get("_error") is not None:
            return {"ok": False, "err": f"{r.get('_error')} {r.get('error', '')}".strip()}
        return {"ok": True, "op_id": mid}

    @staticmethod
    def _remote(kind: str, target: str, subject: str, body: str) -> dict:
        """A message from this computer's machine box to another computer (its butler, or its machine box)."""
        r = run_client(["--profile", M.MACHINE_PROFILE, "send", target, "--kind", kind, "--subject", subject,
                        "--body", body] + (["--for", "machine"] if kind == "task" else []), timeout=60)
        return {"ok": r.get("ok"), "err": None if r.get("ok") else (r.get("err") or r.get("out") or "")[-600:]}

    @staticmethod
    def butler_log(since: int) -> dict:
        """The butler's log from byte `since` (from its start again when it was rotated): at most 400 kB a call."""
        if not B:
            return {"lines": [], "next": 0}
        p = B.home() / "butler.log"
        try:
            size = p.stat().st_size
        except OSError:
            return {"lines": [], "next": 0}
        since = since if 0 <= since <= size else 0
        with open(p, "rb") as f:
            f.seek(max(since, size - 400_000))
            data = f.read(400_000)
        lines = []
        for ln in data.decode("utf-8", "replace").splitlines():
            try:
                lines.append(json.loads(ln))
            except ValueError:
                pass
        return {"lines": lines, "next": size, "reset": since == 0}

    def butler_action(self, a: str, body: dict) -> dict:
        if not B:
            return {"ok": False, "err": "the client has no butler yet: update it (mmbox-update)"}
        if a == "butler_chat":
            text = str(body.get("text") or "").strip()[:20000]
            if not text:
                return {"ok": False, "err": "an empty message"}
            B.chat_add("operator", text)
            with open(B.home() / "chat.queue", "a", encoding="utf-8") as f:
                f.write(text + "\x1e")
            self.ensure_butler()
            return {"ok": True, "running": B.status().get("running")}
        if a == "butler_settings":  # Settings: what the butler thinks with, what a new session runs on by default
            c = B.jload("butler.json", {})
            for k, ck in (("butler_agent", "agent"), ("butler_model", "model")):
                if k in body:
                    v = str(body.get(k) or "").strip()[:80]
                    c.pop(ck, None) if not v else c.__setitem__(ck, v)
            B.jsave("butler.json", c)
            for k in ("session_agent", "session_model"):
                if k in body:
                    self.console_state[k] = str(body.get(k) or "").strip()[:80] or None
            self.save_console_state()
            return {"ok": True}
        if a == "butler_launch":  # a computer without a butler: this one runs it, another is asked through its machine box
            mk_local = M.machine_me()
            target = str(body.get("machine") or "")
            agent, model = str(body.get("agent") or "").strip(), str(body.get("model") or "").strip()
            if not target or (mk_local and target == mk_local.id):
                self.butler_action("butler_settings", {"butler_agent": agent, "butler_model": model})
                return self.butler_action("butler_run", {"on": True})
            daemon = next((b for b in self.snapshot.get("boxes") or [] if b.get("role") == "butler" and b.get("machine") == target), None)
            if daemon:  # its daemon runs already: configured with an op (operator key)
                r = self.send_op(daemon["id"], {"op": "config", "agent": agent, "model": model})
                return {**r, "out": "its daemon's agent and model are set" if r.get("ok") else None}
            text = ("lp177's console asks this computer to run its mmbox butler (client/BUTLER.md), one per computer:\n\n"
                    + (f"1. Set its agent and model in ~/.local/share/mmbox/butler/butler.json: {json.dumps({'agent': agent, 'model': model})}\n" if agent or model else "")
                    + "2. Run it for good: `python3 <mmbox client>/mmbox.py butler install --systemd`, then "
                      "`systemctl --user enable --now mmbox-butler.service` (Linux); elsewhere start `mmbox.py butler run` "
                      "as a lasting background process (its console runs it too).\n"
                    + "3. Answer with `mmbox.py butler status` (running, its box id).")
            r = self._remote("task", target, "Start the mmbox butler of this computer", text)
            return {**r, "out": "asked its machine box: a session there starts it, its box then shows in the explorer" if r["ok"] else None}
        if a == "butler_start" and body.get("butler") and not any((y := M.load_me(n)) and y.id == body["butler"] for n in M.local_profiles()):
            op = {"op": "start", **{k: body[k] for k in ("path", "project", "agent", "model", "title", "task") if body.get(k)}}
            r = self.send_op(str(body["butler"]), op)
            return {**r, "out": "asked its daemon: the new session's box shows in the explorer once it listens" if r.get("ok") else None}
        if a == "butler_start":
            args = ["butler", "start", "--path", str(body.get("path") or "")] + (["--project", str(body["project"])] if body.get("project") else [])
            for k in ("agent", "model", "title", "task"):
                if body.get(k):
                    args += [f"--{k}", str(body[k])]
            r = run_client(args, timeout=90)
            try:
                res = json.loads((r.get("out") or "").strip().splitlines()[-1])
            except (ValueError, IndexError):
                return {"ok": False, "err": (r.get("err") or r.get("out") or "no answer")[-1500:]}
            return {"ok": "error" not in res, **res, "err": res.get("error")}
        if a == "butler_refresh" and body.get("box") and not any((y := M.load_me(n)) and y.id == body["box"] for n in M.local_profiles()):
            # another computer's daemon: an op signed with this console's operator key; its new inventory comes back in
            # the services' table (and as its op_result)
            r = self.send_op(str(body["box"]), {"op": "inventory", "refresh": True})
            return {**r, "out": "asked; its inventory shows within a round or two" if r.get("ok") else None}
        if a == "daemon_op":  # any op to a computer's daemon (operator key only); its answer: op_results[op_id]
            op = body.get("op") if isinstance(body.get("op"), dict) else {}
            if op.get("op") not in ("status", "config", "inventory", "start", "stop", "input", "output", "list", "box"):
                return {"ok": False, "err": "unknown op"}
            return self.send_op(str(body.get("box") or ""), op)
        if a == "session_input":  # a line typed into a hidden session of this computer
            return {"ok": True, **B.session_input(str(body.get("profile") or ""), str(body.get("text") or "")[:20000])}
        if a == "session_stop":
            s = (B.jload("state.json", {}).get("started") or {}).get(str(body.get("profile") or ""))
            return {"ok": bool(s), **(B.stop_session(s) if s else {"err": "not a session the daemon started"})}
        if a == "butler_refresh":
            if B.status().get("running"):
                (B.home() / "refresh").touch()
                return {"ok": True, "out": "the butler probes the agents and models now"}
            r = run_client(["butler", "inventory", "--refresh"], timeout=120)
            return {"ok": r.get("ok"), "out": "agents and models probed", "err": r.get("err") if not r.get("ok") else None}
        if a == "butler_run":
            on = bool(body.get("on"))
            self.console_state["butler_run"] = on
            self.save_console_state()
            if on:
                self._butler_started = 0
                self.ensure_butler()
            elif (pid := B.status().get("pid")) and B.status().get("running"):
                try:
                    os.kill(int(pid), 15)
                except OSError as e:
                    return {"ok": False, "err": str(e)}
            return {"ok": True, "run_with_console": on}
        if a == "butler_stop_session":
            r = run_client(["butler", "stop", str(body.get("profile") or "")], timeout=30)
            return {"ok": r.get("ok"), "out": r.get("out"), "err": r.get("err")}
        return {"ok": False, "err": f"unknown action {a}"}

    # ---------------------------------------------------------------- the account: chain kept in step with the services
    def account_sync(self) -> None:
        """The account's chain against each service this console talks to: what the service lacks is sent (link by
        link: it orders them), what it has more is taken when it holds and extends ours (never a fork); then the join
        requests waiting for an approval. A new device waiting to join takes the chain once it is in it."""
        A = account_lib()
        chain, joining = account_chain(), self.console_state.get("joining")
        fp = None
        if chain:
            fp = A.verify(chain, max_age=None)["fingerprint"]
        elif joining:
            fp = joining.get("account")
        if not fp:
            return
        joins, errors = {}, []
        for srv in self.snapshot.get("servers") or []:
            if not srv.get("ok"):
                continue
            u = srv["url"]
            r = call(u, self.op, "/account/chain", {"account": fp})
            if r.get("_error") is not None:
                continue  # an older service, or none of this account there
            remote = r.get("chain") or []
            if not chain:  # joining: in once a device approved it
                try:
                    st = A.verify(remote, fp, max_age=None)
                except A.ChainError:
                    continue
                if self.op.id in st["devices"]:
                    account_save(remote)
                    chain = remote
                    self.console_state.pop("joining", None)
                    self.save_console_state()
                    run_client(["daemon", "operator", "--account", st["fingerprint"]], timeout=60)
                    log(f"account: this console is now a device of {st['account']}; this computer's daemon obeys it")
                continue
            try:
                if remote and A.extends(chain, remote) and len(remote) > len(chain):
                    A.verify(remote, fp, max_age=None)
                    account_save(remote)
                    chain = remote
                    log(f"account: chain taken from {u} ({len(remote)} links)")
                elif A.extends(remote, chain):
                    for link in chain[len(remote):]:
                        rr = call(u, self.op, "/account/link", {"account": fp, "link": link})
                        if rr.get("_error") is not None:
                            errors.append(f"{u}: link {link['seq']}: {rr.get('_error')} {rr.get('error', '')}")
                            break
                else:
                    errors.append(f"{u}: its chain forks from this console's")
            except A.ChainError as e:
                errors.append(f"{u}: {e}")
            jr = call(u, self.op, "/account/joins", {"account": fp})
            for j in jr.get("joins") or []:
                if j.get("id") and j["id"] not in A.verify(chain, max_age=None)["devices"]:
                    joins[j["id"]] = {**j, "code": A.join_code(j), "server": u}
        self.account_joins = joins
        if errors and errors != getattr(self, "_acct_errors", None):
            log("account: " + "; ".join(errors))
        self._acct_errors = errors

    def account_operator(self, health: dict) -> bool:
        """Whether this console is an operator of a service through its account (MMBOX_OPERATOR_ACCOUNTS there)."""
        accts = health.get("operator_accounts") or []
        chain = account_chain()
        if not accts or not chain:
            return False
        try:
            st = account_lib().verify(chain, max_age=None)
        except Exception:  # noqa: BLE001 - no valid chain: no operator through it
            return False
        return st["fingerprint"] in accts and self.op.id in st["devices"]

    def account_state(self) -> dict:
        try:
            A = account_lib()
        except Exception:  # noqa: BLE001 - an older client: no accounts
            return {"available": False}
        chain = account_chain()
        out = {"available": True, "this": self.op.id, "joining": self.console_state.get("joining"),
               "default_recovery": str(default_recovery_path()),
               "joins": list(getattr(self, "account_joins", {}).values()), "errors": getattr(self, "_acct_errors", [])}
        if self.console_state.get("joining"):
            out["my_code"] = A.join_code(device_record(self.op))
        if chain:
            st = A.verify(chain, max_age=None)
            out.update(account=st["account"], fingerprint=st["fingerprint"], length=st["length"], head_ts=st["head_ts"],
                       devices=list(st["devices"].values()), revoked=sorted(st["revoked"]),
                       root_here=KeyStore(console_home(), "root").where() is not None)
        return out

    def account_action(self, a: str, body: dict) -> dict:
        A = account_lib()
        chain = account_chain()
        if a == "account_create":  # the onboarding window: the passphrase came over this console's own loopback
            path = Path(os.path.expanduser(str(body.get("recovery") or "").strip() or str(default_recovery_path())))
            try:
                res = account_create(self.op, str(body.get("passphrase") or ""), path)
            except ValueError as e:
                return {"ok": False, "err": str(e)}
            log(f"account {res['account']} made (root {res['fingerprint']}); recovery export {res['recovery']}")
            threading.Thread(target=self.account_sync, daemon=True).start()
            return {"ok": True, **res}
        if a == "account_join":
            fp = str(body.get("account") or "").strip().upper()
            if chain:
                return {"ok": False, "err": "this console is already a device of an account"}
            if not re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", fp):
                return {"ok": False, "err": "the account's root fingerprint: XXXX-XXXX-XXXX-XXXX"}
            sent = [s["url"] for s in self.snapshot.get("servers") or [] if s.get("ok")
                    and call(s["url"], self.op, "/account/join", {"account": fp, "device": device_record(self.op)}).get("ok")]
            if not sent:
                return {"ok": False, "err": "no service of this computer knows that account"}
            self.console_state["joining"] = {"account": fp, "at": time.time(), "servers": sent}
            self.save_console_state()
            return {"ok": True, "code": A.join_code(device_record(self.op)), "servers": sent}
        if a == "account_join_cancel":
            j = self.console_state.pop("joining", None) or {}
            self.save_console_state()
            for u in j.get("servers") or []:
                call(u, self.op, "/account/join/clear", {"account": j.get("account"), "device": self.op.id})
            return {"ok": True}
        if not chain:
            return {"ok": False, "err": "this console is no device of an account"}
        st = A.verify(chain, max_age=None)
        dev_id = str(body.get("device") or "")
        if a in ("account_approve", "account_decline"):
            j = getattr(self, "account_joins", {}).get(dev_id)
            if not j:
                return {"ok": False, "err": "no such request waiting"}
            if a == "account_approve":
                if str(body.get("code") or "") != j["code"]:
                    return {"ok": False, "err": "the code differs from the one this request's keys give: not approved"}
                chain.append(A.make_link(chain, "add", {k: j[k] for k in ("id", "sign_pub", "box_pub", "label", "host", "user")
                                                        if k in j}, self.op.id, self.op.sign))
                A.verify(chain, max_age=None)
                account_save(chain)
            call(j["server"], self.op, "/account/join/clear", {"account": st["fingerprint"], "device": dev_id})
            self.account_joins.pop(dev_id, None)
            threading.Thread(target=self.account_sync, daemon=True).start()
            return {"ok": True}
        if a == "account_revoke":
            dev = st["devices"].get(dev_id)
            if not dev:
                return {"ok": False, "err": "no active device of that id"}
            if dev_id == self.op.id:
                return {"ok": False, "err": "revoke this console from another device of the account"}
            chain.append(A.make_link(chain, "revoke", dev, self.op.id, self.op.sign))
            account_save(chain)
            threading.Thread(target=self.account_sync, daemon=True).start()
            return {"ok": True}
        return {"ok": False, "err": f"unknown action {a}"}

    def empty_boxes(self) -> dict:
        """The memory view's second button: every box of every service where this console is an operator deleted
        with what waits in it, for every computer (the operators' own box excepted); live sessions register again."""
        lines, ok = [], True
        for srv in self.snapshot.get("servers") or []:
            if not (srv.get("operator") and srv.get("ok") and "usage" in (srv.get("features") or [])):
                continue
            r = call(srv["url"], self.op, "/usage", {"empty": True}, timeout=120)
            if r.get("_error") is not None:
                ok = False
                lines.append(f"{srv['name']}: failed ({r['_error']} {r.get('error', '')})")
            else:
                e = r.get("emptied") or {}
                lines.append(f"{srv['name']}: {e.get('boxes', 0)} box(es) deleted with {e.get('entries', 0)} waiting message(s); "
                             f"Redis now {fmt_bytes(r.get('used') or 0)}")
        if not lines:
            return {"ok": False, "err": "no service where this console is an operator has the memory figures"}
        log("boxes emptied: " + "; ".join(lines))
        threading.Thread(target=self.refresh, daemon=True).start()
        self._usage = (0.0, {})
        return {"ok": ok, "out": "\n".join(lines)}

    def fetch_questions(self, servers: list[dict], peers: dict) -> list[str]:
        """What boxes wrote to this console on each service where it is the operator: questions (and their
        withdrawals), each taken, verified against its sender's key, kept, then deleted from the service."""
        def one(srv):
            u = srv["url"]
            r = call(u, self.op, "/list", {"box": self.op.id, "since": "0"})
            if r.get("_error") is not None:
                return f"{srv['name']}: questions unreadable ({r.get('_error')} {r.get('error', '')})"
            for e in r.get("entries") or []:
                if e.get("taken") and (e["taken"] or {}).get("holder") != "console":
                    continue
                t = {"won": True, "env": e.get("env"), "from": e.get("from")} if e.get("taken") else \
                    call(u, self.op, "/take", {"box": self.op.id, "entry": e["entry"], "holder": "console", "purpose": "question"})
                if not t.get("won"):
                    continue
                env, sender = t.get("env") or {}, t.get("from") or e.get("from")
                card = peers.get(sender) or self.card(u, sender)
                payload, why = None, "not a question"
                if not card:
                    why = "its sender's key is unknown"
                else:
                    try:
                        payload = M.open_envelope(self.op, {"id": sender, "user": card.get("user"), "sign_pub": card["sign_pub"]}, env)
                    except (ValueError, KeyError, TypeError) as ex:
                        why = f"does not verify: {ex}"
                if payload and payload.get("kind") == "question":
                    self.questions.add(u, sender, payload, card, known=sender in peers)
                elif payload and payload.get("kind") == "recap":  # a session's work is over: kept until deleted
                    if (rc := self.recaps.add(u, sender, payload, card)) and self.desktop:
                        self.desktop.recap({**rc, "label": rc.get("label") or card.get("label")})
                elif payload and payload.get("kind") == "op_result":  # a daemon's answer to an op this console sent
                    res = getattr(self, "op_results", {})
                    res[str(payload.get("re") or payload.get("id"))] = {"from": sender, "label": card.get("label"), "server": u,
                                                                         "at": time.time(), "result": payload.get("op_result")}
                    self.op_results = dict(sorted(res.items(), key=lambda kv: kv[1]["at"])[-100:])
                else:
                    log(f"{u}: entry {e['entry']} from {sender} dropped: {why}")
                call(u, self.op, "/done", {"box": self.op.id, "entries": [e["entry"]]})
            return None
        res = parallel({s["url"]: (lambda s=s: one(s)) for s in servers
                        if s["operator"] and s["ok"] and "questions" in s["features"]})
        return [v for v in res.values() if v]

    def answer(self, qid: str, choices: list, text: str) -> dict:
        q = self.questions.items.get(qid)
        if not q or q["state"] != "pending":
            return {"ok": False, "err": "this question no longer waits for an answer"}
        idx = sorted({int(i) for i in choices if str(i).isdigit() and 0 <= int(i) < len(q["options"])})
        text = (text or "").strip()[:8000]
        if not idx and not text:
            return {"ok": False, "err": "choose at least one solution, or write an answer"}
        if len(idx) > 1 and not q["multiple"]:
            return {"ok": False, "err": "only one solution for this question"}
        chosen = [q["options"][i] for i in idx]
        body = (f"The user's answer to your question \"{q['subject']}\" ({qid}):\n"
                + ("".join(f"- {c}\n" for c in chosen) if chosen else "")
                + (("\nThe user adds:\n" if chosen else "\nThe user's free answer:\n") + text + "\n" if text else ""))
        r, to = self.deliver_answer(q["server"], q["from"], q.get("title") or q.get("label"), "Re: " + q["subject"], body, qid,
                                    {"question_id": qid, "choices": idx, "chosen": chosen, "text": text})
        if not r.get("ok"):
            return {"ok": False, "err": f"answer not delivered: {r.get('_error')} {r.get('error', '')}".strip()}
        with self.questions.lock:
            q.update(state="answered", closed_at=time.time(),
                     answer={"choices": idx, "chosen": chosen, "text": text, "to": to, "id": r["id"], "entry": r.get("entry")})
            self.questions.save()
        self.tick()
        return {"ok": True, "to": to, "id": r["id"], "rerouted": to != q["from"]}

    def deliver_answer(self, url: str, box: str, box_name: str | None, subject: str, body: str, re_: str,
                       answer: dict) -> tuple[dict, str]:
        """An `answer`, signed by the console, to `box` on service `url`; to its machine box when it closed meanwhile
        (said so: `rerouted`, `asked_by`). Returns the service's reply (with the message id) and the box it went to."""
        def send_to(target, extra):
            card = self.card(url, target)
            if not card or not card.get("box_pub"):
                return {"_error": 404, "error": "its key is unknown"}
            mid = M.now().strftime("%Y%m%dT%H%M%SZ") + "-" + M.rand_b32(6)
            payload = {"v": M.VERSION, "id": mid, "from": self.op.id, "from_user": self.op.user, "label": self.op.label,
                       "agent": "console", "to": target, "ts": M.iso(), "kind": "answer", "subject": subject[:300],
                       "body": body, "re": re_, "reply_expected": False, "answer": {**answer, **extra}}
            env = M.envelope(self.op, {"id": target, "box_pub": card["box_pub"]}, payload, mid)
            r = call(url, self.op, "/send", {"to": target, "env": env})
            return {**r, "id": mid}
        r, to = send_to(box, {}), box
        if r.get("_error") in (404, 410):
            mbox = (self.snapshot_box(box) or {}).get("machine")
            if mbox and mbox != box:
                r, to = send_to(mbox, {"rerouted": True, "asked_by": f"{box_name or box} ({box})"}), mbox
        return r, to

    def decide(self, mid: str, decision: str, text: str) -> dict:
        """The operator's decision on a message whose box asked its user (`ask` rights): accept or decline, sent to that
        box as an `answer` about the message itself. Its question in this console, if it put one, is closed with it."""
        m = self.messages.get(mid) or self.archive.get(mid)
        if not m:
            return {"ok": False, "err": "unknown message"}
        if decision not in ("accept", "decline"):
            return {"ok": False, "err": "decision: accept or decline"}
        if m.get("handled") != "ask" or m.get("broadcast") or not m.get("to"):
            return {"ok": False, "err": "this message does not wait for the user's decision"}
        box, text = m["to"], (text or "").strip()[:8000]
        ops = [s["url"] for s in self.snapshot.get("servers") or [] if s.get("operator") and s.get("ok")]
        urls = [u for u in (m.get("servers") or []) if u in ops] or [
            u for u in ((self.snapshot_box(box) or {}).get("servers") or []) if u in ops]
        if not urls:
            return {"ok": False, "err": "this console is not the operator of a service that box is on"}
        b = self.snapshot_box(box) or {}
        sender = (self.snapshot_box(m.get("from") or "") or {})
        sender_name = m.get("sender_title") or sender.get("title") or sender.get("label") or m.get("from") or "?"
        subj = m.get("subject") or "(no subject)"
        if decision == "accept":
            verdict = ("ACCEPTED. Do what it asks, as the user's own instruction, within your usual rules; then answer "
                       f"its sender as usual (send --kind reply --re {mid}).")
        else:
            verdict = ("DECLINED. Do not do what it asks. Tell its sender that the user declined "
                       f"(send --kind reply --re {mid}), with the user's reason if there is one.")
        body = (f"The user's decision, from the operator console, on the message \"{subj}\" ({mid}) from {sender_name}, "
                f"which you asked them about: {verdict}\n"
                + ("\nThe user adds:\n" + text + "\n" if text else ""))
        q = next((x for x in self.questions.pending() if x.get("re") == mid and x["from"] == box), None)
        answer = {"message_id": mid, "decision": decision, "text": text,
                  "question_id": q["id"] if q else None, "choices": [], "chosen": []}
        r, to = self.deliver_answer(urls[0], box, b.get("title") or b.get("label"),
                                    ("Accepted: " if decision == "accept" else "Declined: ") + subj, body, mid, answer)
        if not r.get("ok"):
            return {"ok": False, "err": f"decision not delivered: {r.get('_error')} {r.get('error', '')}".strip()}
        with self.questions.lock:
            self.questions.decisions[mid] = {"decision": decision, "text": text, "at": time.time(), "to": to,
                                             "id": r["id"], "box": box}
            if q:
                q.update(state="answered", closed_at=time.time(),
                         answer={"choices": [], "chosen": [], "text": f"{decision}: {text}" if text else decision,
                                 "to": to, "id": r["id"], "entry": r.get("entry"), "decision": decision})
            self.questions.save()
        self.tick()
        return {"ok": True, "to": to, "id": r["id"], "rerouted": to != box}

    def snapshot_box(self, bid: str) -> dict | None:
        return next((b for b in self.snapshot.get("boxes") or [] if b["id"] == bid), None)

    def touch(self) -> None:
        """The operator looked at the console now (kept once a minute): the next opening measures its absence."""
        if time.time() - self._seen_saved < 60:
            return
        self._seen_saved = time.time()
        self.console_state["last_seen"] = time.time()
        tmp = self.seen_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.console_state), encoding="utf-8")
        os.replace(tmp, self.seen_path)

    def tuning(self) -> dict:
        eff = M.apply_tuning()
        t = (M.Local(M.MACHINE_PROFILE).load("config.json", {}) or {}).get("tuning") or {}
        on_done = M.on_done() if hasattr(M, "on_done") else None  # an older client: no such setting
        return {"effective": {**eff, **({"on_done": on_done} if on_done else {})}, "set": t,
                "env": {k: os.environ.get(e) for k, (e, _, _) in M.TUNABLE.items() if os.environ.get(e)}
                | ({"on_done": os.environ["MMBOX_ON_DONE"]} if os.environ.get("MMBOX_ON_DONE") else {}),
                "help": {k: v[2] for k, v in M.TUNABLE.items()}}

    def check_startup(self, servers: list[dict]) -> None:
        """At the opening, after an absence longer than 70 % of a collector's retention: how many of its messages
        go soon (within the remaining 30 %). Asked only when there are some."""
        gap = time.time() - self.last_open if self.last_open else None
        out = []
        for srv in servers:
            if not (srv["operator"] and srv["ok"] and srv.get("collector")):
                continue
            r = call(srv["url"], self.op, "/col/config", {"stats": True})
            if not r.get("ok"):
                continue
            keep_s = r["keep_ms"] / 1000
            if gap is None or gap < 0.7 * keep_s or not r.get("expiring_messages"):
                continue
            out.append({"url": srv["url"], "name": srv["name"], "keep_days": r["keep_days"], "count": r["count"],
                        "expiring": r["expiring"], "expiring_messages": r["expiring_messages"], "soon_s": r["soon_s"],
                        "oldest_ms": r.get("oldest_ms")})
        self.startup = {"last_open": self.last_open or None, "gap_s": gap, "servers": out} if out else None

    # -- cards of senders, to verify the annotations (kept: a closed box's card leaves the service)
    def _load_cards(self) -> dict:
        try:
            return json.loads((self.op.home / "cards.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cards(self) -> None:
        p = self.op.home / "cards.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cards), encoding="utf-8")
        os.replace(tmp, p)

    def card(self, url: str, mid: str) -> dict | None:
        c = self.cards.get(mid)
        if c:
            return c
        r = call(url, self.op, "/card/get", {"id": mid})
        c = r.get("card")
        if c and M.verify_signed(c, c.get("sign_pub", "")):
            self.cards[mid] = {k: c.get(k) for k in ("id", "user", "label", "agent", "sign_pub", "box_pub", "fingerprint")}
            self._save_cards()
            return self.cards[mid]
        return None

    def open_note(self, url: str, note: dict, peers: dict) -> tuple[dict | None, str]:
        """An annotation sealed for this console's key: opened and its sender's signature checked."""
        if not isinstance(note, dict) or note.get("to") != self.op.id:
            return None, "sealed for another operator key"
        sender = note.get("from")
        peer = peers.get(sender) or self.card(url, sender)
        if not peer:
            return None, "sender's key unknown"
        try:
            return M.open_envelope(self.op, {"id": sender, "user": peer.get("user"), "sign_pub": peer["sign_pub"]}, note,
                                   etype="col"), "verified"
        except (ValueError, KeyError, TypeError) as e:
            return None, f"does not verify: {e}"

    # -- one refresh (one at a time: an action may ask for one while the periodic one runs)
    def refresh(self) -> None:
        with self.refreshing:
            self._refresh()
        self.tick()

    def _refresh(self) -> None:
        errors = []
        M.pin_hosts()
        srv_reg = M.servers_load()["servers"]
        users = M.server_users()
        profiles = {}
        for n in M.local_profiles():
            b = M.load_me(n)
            if b:
                profiles[n] = b
        machine = next((b for b in profiles.values() if b.role == "machine"), None)
        urls = []
        for u in [s.get("url", "").rstrip("/") for s in srv_reg.values()] + list(users):
            if u and u not in urls:
                urls.append(u)
        healths = parallel({u: (lambda u=u: health(u)) for u in urls})
        col_conf = M.col_conf()
        paused = M.paused_urls()
        servers = []
        for u in urls:
            name = next((n for n, s in srv_reg.items() if s.get("url", "").rstrip("/") == u), None)
            h = healths.get(u) or {}
            reg = srv_reg.get(name) or {}
            servers.append({"name": name or M.server_name_of(u), "url": u, "known": bool(name), "address": reg.get("address"),
                            "note": reg.get("note"), "deploy": reg.get("deploy"), "users": users.get(u, []),
                            "ok": bool(h.get("ok")), "ms": h.get("ms"), "error": h.get("error"),
                            "features": h.get("features") or [], "collector": h.get("collector"),
                            "operator": self.op.id in (h.get("operators") or []) or self.account_operator(h),
                            "box_ttl_s": h.get("box_ttl_s"),
                            "col_level": M.col_level(u), "col_pinned": (col_conf.get(u) or {}).get("fingerprint"),
                            "paused": u in paused})

        # every peer this computer knows: names, keys, machines
        peers: dict[str, dict] = {}
        for n, b in profiles.items():
            try:
                for pid, q in M.peers_load(b.local).items():
                    peers.setdefault(pid, q)
            except SystemExit:
                errors.append(f"peers of {n} unreadable")

        # how this computer handles what each box sends (execute, ask, archive): the machine box's view
        rights: dict = {}
        if machine:
            try:
                mp = M.peers_load(machine.local)
                default_rights, by_id = M.local_rights_conf()
                rights["local"] = {"rights": default_rights, "env": bool(os.environ.get("MMBOX_LOCAL_RIGHTS"))}
                rights["networks"] = {u: r for u, r in (machine.conf.get("service_rights") or {}).items() if r in M.RIGHTS}
                if hasattr(M, "other_project_conf"):  # the sessions of other projects of this computer
                    opc = M.other_project_conf()
                    rights["other_projects"] = {"computer": opc["computer"], "projects": opc["projects"],
                                                "networks": opc["networks"], "project": machine.conf.get("project")}
                for pid, q in mp.items():
                    if q.get("local_profile") and q.get("other_project"):
                        rights[pid] = {"rights": q.get("rights"), "source": q.get("rights_source") or "default",
                                       "local": True, "other_project": q.get("project")}
                    elif q.get("local_profile"):
                        rights[pid] = {"rights": q.get("rights"), "source": "own" if pid in by_id else "local", "local": True}
                    else:
                        src = q.get("rights_from")
                        intro = mp.get(q.get("introduced_by") or "") or {}
                        rights[pid] = {"rights": M.rights_of(mp, q), "source": "follows" if src else "own",
                                       "introduced_by": q.get("introduced_by"), "follow_label": intro.get("label") or intro.get("alias")}
            except SystemExit:
                errors.append("rights of the machine box unreadable")
        self.rights = rights


        # the tables of active boxes
        def boxes_of(srv):
            u = srv["url"]
            if not srv["ok"] or "boxes" not in srv["features"]:
                return {"error": srv.get("error") or "no table of active boxes on this service (older gateway)"}
            if srv["operator"]:
                return call(u, self.op, "/boxes", {})
            signer = next((b for b in ([machine] if machine else []) + list(profiles.values())
                           if b and u in M.gw_urls(b) and not b.closed_at), None)
            if not signer:
                return {"error": "no box of this computer on this service"}
            return call(u, signer, "/boxes", {})

        # the traffic of each network (messages and posts per minute, 7 days kept by the service)
        def stats_of(srv):
            u = srv["url"]
            if not srv["ok"] or "stats" not in srv["features"]:
                return None
            signer = self.op if srv["operator"] else next(
                (b for b in ([machine] if machine else []) + list(profiles.values()) if b and u in M.gw_urls(b) and not b.closed_at), None)
            return call(u, signer, "/stats", {}) if signer else None

        # the collectors this console reads
        def col_of(srv):
            u = srv["url"]
            if not (srv["ok"] and srv["operator"] and "collector" in srv["features"]):
                return None
            st = self.col.setdefault(u, {"cursor": None, "events": collections.deque(maxlen=100_000)})
            body = {"tail": 20000} if st["cursor"] is None else {"since": st["cursor"], "count": 5000}
            for _ in range(20):
                r = call(u, self.op, "/col/read", body)
                if r.get("_error") is not None:
                    return r
                st["events"].extend(r.get("events") or [])
                st["cursor"] = r.get("cursor") or st["cursor"] or "0"
                if not r.get("more") or "tail" in body:
                    break
                body = {"since": st["cursor"], "count": 5000}
            return {"ok": True}

        def col_conf_of(srv):  # each collector's retention, for the settings
            if srv["operator"] and srv["ok"] and "collector" in srv["features"]:
                return call(srv["url"], self.op, "/col/config", {})
            return None

        # every question to the services at once: they do not depend on each other, and a distant service takes
        # a second or more for each (their sum was most of a refresh, and of the start screen's wait)
        jobs = {"questions": lambda: self.fetch_questions(servers, peers)}
        for s in servers:
            for kind, fn in (("boxes", boxes_of), ("stats", stats_of), ("col", col_of), ("col_conf", col_conf_of)):
                jobs[(kind, s["url"])] = (lambda fn=fn, s=s: fn(s))
        first = self.startup is None and not getattr(self, "_startup_done", False)
        if first:
            self._startup_done = True
            jobs["startup"] = lambda: self.check_startup(servers)
        done = parallel(jobs)
        errors += done.get("questions") or []
        if first and "startup" not in done:
            errors.append("startup check: no answer")
        tables = {s["url"]: done.get(("boxes", s["url"])) for s in servers}
        cleared = self.console_state.get("stats_clear") or {}
        for srv in servers:
            u, r = srv["url"], done.get(("stats", srv["url"]))
            if not r or not r.get("ok"):
                srv["stats"] = None
                continue
            now_ms, since = r["now_ms"], int(cleared.get(u) or 0)
            win = {}
            for w in STATS_WINDOWS:
                lo = now_ms - w * 1000  # a span the window starts in counts; after a clear, only the spans that follow it
                rows = [b for b in r.get("buckets") or [] if b[0] + (b[4] if len(b) > 4 else 60000) > lo and b[0] >= since]
                win[str(w)] = {"messages": sum(b[1] for b in rows), "posts": sum(b[2] for b in rows), "bytes": sum(b[3] for b in rows)}
            srv["stats"] = {"windows": win, "cleared_ms": since or None, "keep_s": r.get("keep_s"),
                            "oldest_ms": (r.get("buckets") or [[None]])[0][0]}
        for s in servers:
            r = done.get(("col", s["url"]))
            s["col_read"] = None if r is None else ("ok" if r.get("ok") else f"{r.get('_error')} {r.get('error', '')}")

        # what waits unread in the boxes of this computer (read, never taken)
        waiting = {}
        active_waiting = {}
        for u, t in tables.items():
            for bx in (t or {}).get("boxes") or []:
                if bx.get("waiting"):
                    active_waiting[(bx["id"], u)] = bx["waiting"]

        def unread_of(b, u):
            r = call(u, b, "/list", {"box": b.id, "since": "0"})
            out = []
            bpeers = M.peers_load(b.local)
            for e in r.get("entries") or []:
                env, sender = e.get("env") or {}, e.get("from")
                q = bpeers.get(sender)
                payload, why = None, "sender not a peer of this box"
                if q:
                    try:
                        payload, why = M.open_envelope(b, q, env), "local"
                    except (ValueError, KeyError, TypeError) as ex:
                        why = f"does not open: {ex}"
                out.append({"entry": e["entry"], "from": sender, "to": b.id, "stored_ms": e.get("stored_ms"),
                            "taken": e.get("taken"), "payload": payload, "why": why, "env_id": env.get("id"),
                            "env_ts": env.get("ts"), "server": u, "profile": b.local.profile})
            return out
        jobs = {}
        for n, b in profiles.items():
            for u in M.gw_urls(b):
                if (b.id, u) in active_waiting:
                    jobs[(n, u)] = (lambda b=b, u=u: unread_of(b, u))
        for k, v in parallel(jobs).items():
            waiting[k] = v or []
        self.check_delivery(profiles, servers)

        for srv in servers:  # each collector's retention, for the settings
            r = done.get(("col_conf", srv["url"]))
            if r is not None:
                srv["col_conf"] = {k: r.get(k) for k in ("keep_days", "hold_until_ms", "count", "oldest_ms")} if r.get("ok") else None
        with self.lock:
            self._build(servers, tables, profiles, peers, waiting, errors)

    def check_delivery(self, profiles: dict, servers: list[dict]) -> None:
        """The messages the last model left "sent" (no collector saw them, their recipient is not on this computer):
        their sender's service says whether it still holds them (/v1/status), once consumed for good."""
        ok_urls = {s["url"] for s in servers if s["ok"]}
        now = time.time()
        groups: dict[tuple, list] = {}
        with self.lock:
            prev = list(self.messages.values())
        for m in prev:
            if m.get("state") != "sent" or not m.get("entry") or not m.get("to") or m.get("broadcast"):
                continue
            d = self.delivery.get(m["id"])
            if d and (d["st"] == "consumed" or now - d["at"] < DELIVERY_RECHECK_S * (30 if d["st"] == "unknown" else 1)):
                continue
            prof = next((x.split(":", 1)[1] for x in m.get("sources") or [] if x.startswith("local:")), None)
            url = next((u for u in m.get("servers") or [] if u in ok_urls), None)
            if prof in profiles and url:
                groups.setdefault((url, prof, m["to"]), []).append(m)
        if not groups:
            return
        picked = sorted(groups.items(), key=lambda kv: min((self.delivery.get(m["id"]) or {}).get("at", 0) for m in kv[1]))
        picked = dict(picked[:DELIVERY_GROUPS])

        def ask(url, prof, to, ms):
            ms = ms[-1000:]
            r = call(url, profiles[prof], "/status", {"to": to, "entries": [m["entry"] for m in ms]})
            st = r.get("status") if isinstance(r.get("status"), dict) else None
            return {m["id"]: (st or {}).get(m["entry"], "unknown") for m in ms} if st is not None else \
                {m["id"]: "unknown" for m in ms}
        for res in parallel({k: (lambda k=k, v=v: ask(*k, v)) for k, v in picked.items()}).values():
            for mid, st in (res or {}).items():
                self.delivery[mid] = {"st": st, "at": now}

    # -- the merged model
    def _build(self, servers, tables, profiles, peers, waiting, errors):
        horizon = time.time() - self.days * 86400 if self.days > 0 else 0
        boxes: dict[str, dict] = {}

        def box(bid, at_ms=None, model_ms=None, **kv):
            if not bid:
                return None
            b = boxes.setdefault(bid, {"id": bid, "servers": [], "sources": []})
            act = kv.pop("activity", None)
            if act and (at_ms or 0) >= (b.get("activity_ms") or 0):  # the most recent one wins, whatever said it
                b["activity"], b["activity_ms"] = act, at_ms or b.get("activity_ms")
            prov, mod = kv.pop("provider", None), kv.pop("model", None)
            model_ms = model_ms if model_ms is not None else at_ms
            if (prov or mod) and (model_ms or 0) >= (b.get("model_ms") or 0):  # a session may change model: the latest
                if prov:
                    b["provider"] = prov
                if mod:
                    b["model"] = mod
                b["model_ms"] = model_ms or b.get("model_ms")
            for k, v in kv.items():
                if v not in (None, "", []) and (b.get(k) in (None, "", []) or k in ("active", "listening", "waiting", "idle_s")):
                    b[k] = v
            return b

        for s in servers:  # the services' tables come first: what the boxes say of themselves
            t = tables.get(s["url"]) or {}
            s["table_error"] = None if t.get("ok") else (t.get("error") or (f"HTTP {t.get('_error')}" if t else None))
            s["table_scope"] = "all" if t.get("operator") else "peers" if t.get("ok") else None
            s["active"] = len(t.get("boxes") or [])
            for bx in t.get("boxes") or []:
                b = box(bx["id"], label=bx.get("label"), role=bx.get("role"), machine=bx.get("machine") if bx.get("machine_ok") or bx.get("role") == "machine" else bx.get("machine"),
                        work=bx.get("work"), project=bx.get("project"), agent=bx.get("agent"), user=bx.get("user"),
                        hostname=(bx.get("host") or {}).get("hostname"), fingerprint=bx.get("fingerprint"),
                        declared=bx.get("declared"), opened_ms=bx.get("opened_ms"),
                        provider=bx.get("provider"), model=bx.get("model"), model_ms=bx.get("opened_ms"))
                if bx.get("title"):  # fixed while the box is open: the service's word
                    b["title"] = bx["title"]
                if isinstance(bx.get("inventory"), dict) and (bx["inventory"].get("at") or 0) >= ((b.get("inventory") or {}).get("at") or 0):
                    b["inventory"] = bx["inventory"]  # a butler's: the agents and models of its computer
                if bx.get("activity"):
                    box(bx["id"], at_ms=bx.get("activity_ms") or bx.get("last_ms"), activity=bx["activity"])
                b["active"] = True
                b["listening"] = b.get("listening") or bx.get("listening")
                b["waiting"] = max(b.get("waiting") or 0, bx.get("waiting") or 0)
                b["idle_s"] = min(b.get("idle_s") if b.get("idle_s") is not None else 1e12, bx.get("idle_s") or 0)
                if s["url"] not in b["servers"]:
                    b["servers"].append(s["url"])
                b["sources"].append("table")
        machine = next((b for b in profiles.values() if b.role == "machine"), None)
        here = socket.gethostname()
        for n, y in profiles.items():
            ss = M.session_state(y.local)
            b = box(y.id, label=y.label, role=y.role, machine=machine.id if machine and y.role != "machine" else None,
                    work=y.conf.get("work_project"), project=y.conf.get("project"), agent=y.agent, user=y.user,
                    hostname=here, profile=n, title=y.conf.get("title"), activity=y.conf.get("activity"),
                    at_ms=_ms(y.conf.get("activity_at")), provider=y.conf.get("provider"), model=y.conf.get("model"),
                    model_ms=LOCAL_WINS_MS)  # this computer's own configuration says what it runs on now
            b["local"] = True
            b["role"] = y.role
            if y.role == "butler" and isinstance(y.conf.get("inventory"), dict):
                b["inventory"] = y.conf["inventory"]  # this computer's own configuration: what the box is, whatever an older service said
            b["closed_at"] = y.closed_at
            b["listening_here"] = bool(ss["listening"])
            if y.closed_at:
                b["active"] = b.get("active") or False
            for u in M.gw_urls(y):
                if u not in b["servers"]:
                    b["servers"].append(u)
        box(self.op.id, label="Operator console (you)", role="operator", hostname=here, agent="console")
        for pid, q in peers.items():
            h = q.get("last_host") or q.get("host") or {}
            # a remote peer paired directly is a machine box (pairing is between machine boxes); introduced: a session
            role = q.get("role") or ("session" if q.get("introduced_by") else None if q.get("local_profile") else "machine")
            b = box(pid, label=q.get("label"), role=role,
                    machine=q.get("introduced_by"), user=q.get("user"), agent=q.get("agent"), hostname=h.get("hostname"),
                    fingerprint=q.get("fingerprint"), alias=q.get("alias"))
            if q.get("role") in ("project", "butler"):  # said by its introduction: wins over an older service's "session"
                b["role"] = q["role"]
            if q.get("retired_at"):
                b["retired_at"] = q["retired_at"]
            for u in M.peer_urls(q):
                if u not in b["servers"]:
                    b["servers"].append(u)

        for bid, r in self.rights.items():  # every box this computer knows the rights of
            if bid not in ("local", "networks", "other_projects") and bid in boxes:
                boxes[bid]["rights"] = r

        msgs: dict[str, dict] = {}

        def msg(mid, **kv):
            m = msgs.setdefault(mid, {"id": mid, "sources": [], "servers": [], "seen_by": [], "events": []})
            for k, v in kv.items():
                if v not in (None, "") and m.get(k) in (None, ""):
                    m[k] = v
            return m

        def from_payload(m, p, src, local_log=False):
            for k in ("kind", "subject", "body", "re", "project", "work", "scope", "topic", "ttl_s"):
                if p.get(k) not in (None, "") and m.get(k) in (None, ""):
                    m[k] = p[k]
            if p.get("reply_expected"):
                m["reply_expected"] = True
            if isinstance(p.get("lock"), dict):
                m["lock"] = {k: p["lock"].get(k) for k in ("name", "event")}
            if p.get("via_session"):
                m["via_session"] = (p["via_session"] or {}).get("label") if isinstance(p["via_session"], dict) else p["via_session"]
            for k, mk in (("title", "sender_title"), ("activity", "sender_activity")):
                if p.get(k) and not m.get(mk):
                    m[mk] = p[k]
            fw = p.get("forwarded")
            atts = p.get("attachments") or (((fw.get("original") or {}) if isinstance(fw, dict) else {}).get("attachments"))
            if isinstance(atts, list) and atts:  # the best copy wins: the one with the most files on this computer
                new = att_meta(atts, self.op.home, local=local_log)
                old = m.get("attachments") or []
                if not old or sum(1 for a in new if a.get("path")) > sum(1 for a in old if a.get("path")):
                    m["attachments"] = new
            if isinstance(fw, dict) and not m.get("forwarded"):
                o = fw.get("original") if isinstance(fw.get("original"), dict) else {}
                m["forwarded"] = {"from": fw.get("from") or (fw.get("original_sender") or {}).get("id") or o.get("from"),
                                  "from_label": fw.get("from_label") or o.get("label") or (fw.get("original_sender") or {}).get("alias"),
                                  "subject": o.get("subject"), "body": o.get("body"), "kind": fw.get("kind") or o.get("kind"),
                                  "reason": fw.get("reason"), "stored_at": fw.get("stored_at") or fw.get("original_stored_at"),
                                  "id": fw.get("id") or o.get("id")}
            if "body" in p:
                m["level"] = "full"
            elif m.get("level") != "full":
                m["level"] = src
            h = p.get("host") or {}
            box(p.get("from"), label=p.get("label"), agent=p.get("agent"), user=p.get("from_user"),
                hostname=h.get("hostname"), work=p.get("work") if p.get("from") else None, title=p.get("title"),
                activity=p.get("activity"), at_ms=_ms(p.get("ts")), provider=p.get("provider"), model=p.get("model"))

        def ts_ok(ts):
            try:
                return not horizon or M.parse_iso(ts).timestamp() >= horizon
            except (TypeError, ValueError):
                return True

        # 1. the local logs: what each box of this computer sent and handled (decrypted)
        for n, y in profiles.items():
            for e in self.logs.read(n, y.local.path("log.jsonl")):
                p = e.get("payload") or {}
                mid = e.get("id") or p.get("id")
                if not mid or not ts_ok(p.get("ts") or e.get("at")):
                    continue
                is_chan = e.get("box") == "channel" or e.get("status") == "posted" or p.get("scope")
                m = msg(mid, ts=p.get("ts") or e.get("at"), frm=p.get("from"))
                from_payload(m, p, "full", local_log=True)
                if f"local:{n}" not in m["sources"]:
                    m["sources"].append(f"local:{n}")
                for u in ([e["gateway"]] if isinstance(e.get("gateway"), str) else e.get("gateway") or []):
                    if u not in m["servers"]:
                        m["servers"].append(u)
                if is_chan:
                    m["broadcast"] = True
                    if e.get("dir") == "out":
                        m["to_boxes"] = [x.get("box") for x in e.get("posted") or [] if x.get("box")]
                        m["posted"] = e.get("posted")
                        m.setdefault("state", "posted")
                    else:
                        if y.id not in m["seen_by"]:
                            m["seen_by"].append(y.id)
                        m["handled_" + y.id] = e.get("status")
                        m.setdefault("state", "posted")
                    continue
                if e.get("dir") == "out":
                    m["to"] = m.get("to") or p.get("to") or e.get("to") or e.get("peer")
                    st = e.get("status")
                    if st == "queued" and m.get("state") not in ("read", "taken", "unread", "sent"):
                        m["state"] = "queued"
                        m["queued_by"] = n
                        at = _ms(e.get("at") or p.get("ts"))
                        if at:
                            hd = m.setdefault("held", {"since": at, "local": n})
                            hd["since"] = min(hd["since"], at)
                            hd["local"] = n
                    elif st == "sent" and m.get("state") in (None, "queued", "dropped"):
                        m["state"] = "sent"
                    elif st == "dropped" and m.get("state") in (None, "queued"):
                        m["state"] = "dropped"
                        m["reason"] = e.get("reason")
                    if e.get("entry"):
                        m["entry"] = e["entry"]
                else:
                    m["to"] = m.get("to") or p.get("to") or y.id
                    m["state"] = "read"
                    m["handled"] = e.get("status")
                    if e.get("status") in ("rerouted", "givenback") and e.get("reason"):
                        m["passed_on"] = e["reason"]  # where it went: its project's box, the session that asked
                    m["read_by"] = y.id
                    m["read_at"] = e.get("at")
                    if e.get("box") == "machine":
                        m["via_machine"] = True
                    tb = e.get("taken_by") if isinstance(e.get("taken_by"), dict) else None
                    if tb and (tb.get("id") or tb.get("label")):  # the session whose listener took a machine-box message
                        m["taken_by"] = {k: tb.get(k) for k in ("id", "label", "profile")}
                        if tb.get("id"):
                            box(tb["id"], label=tb.get("label"), profile=tb.get("profile"))
                    if e.get("reason"):
                        m["reason"] = e["reason"]

        outboxes: dict[str, set] = {}  # queued, but no longer in its sender's outbox: dropped before the log said it
        for m in msgs.values():
            n = m.pop("queued_by", None)
            if m.get("state") == "queued" and n in profiles:
                if n not in outboxes:
                    st = profiles[n].local.load("state.json", {}) or {}
                    outboxes[n] = {i.get("msg_id") for i in st.get("outbox") or []}
                if m["id"] not in outboxes[n]:
                    m["state"] = "dropped"
                    m["reason"] = m.get("reason") or "no longer in its sender's outbox: dropped by its client"

        # what the services said of the messages still "sent" (check_delivery)
        for m in msgs.values():
            d = self.delivery.get(m["id"])
            if d and m.get("state") == "sent":
                m["state"] = {"consumed": "delivered", "taken": "taken", "waiting": "unread"}.get(d["st"], "sent")

        # 2. what waits unread in the boxes of this computer
        for (n, u), rows in waiting.items():
            for w in rows:
                p = w.get("payload") or {}
                mid = p.get("id") or w.get("env_id") or f"{u}#{w['entry']}"
                m = msg(mid, ts=p.get("ts") or w.get("env_ts"), frm=w.get("from"), to=w.get("to"))
                if p:
                    from_payload(m, p, "full")
                m["state"] = "taken" if w.get("taken") else "unread"
                m["entry"] = w["entry"]
                if u not in m["servers"]:
                    m["servers"].append(u)
                if f"waiting:{n}" not in m["sources"]:
                    m["sources"].append(f"waiting:{n}")
                if not p:
                    m["note_status"] = w.get("why")

        # 3. the collectors: what each service saw, and the annotations sealed for this console
        activity = []
        box_open: dict[str, tuple] = {}  # each box's last word in the collectors: (time, open or not)
        for u, st in self.col.items():
            by_entry = {}
            for ev in st["events"]:
                t = int(ev.get("t") or ev["entry"].split("-")[0])
                if horizon and t / 1000 < horizon:
                    continue
                kind = ev.get("ev")
                if kind in ("send", "chan"):
                    mid = ev.get("msg") or f"{u}#{ev['entry']}"
                    m = msg(mid, ts=ev.get("ts") or M.iso(_dt(t)), frm=ev.get("from"))
                    if kind == "send":
                        m["to"] = m.get("to") or ev.get("to")
                        by_entry[(ev.get("to"), ev["entry"])] = mid
                        if m.get("state") in (None, "sent", "queued"):
                            m["state"] = "unread"
                        m["entry"] = m.get("entry") or ev["entry"]
                    else:
                        m["broadcast"] = True
                        m["to_boxes"] = m.get("to_boxes") or ev.get("boxes")
                        m.setdefault("state", "posted")
                        m["ttl_s"] = m.get("ttl_s") or ev.get("ttl")
                    m["size"] = m.get("size") or ev.get("size")
                    if u not in m["servers"]:
                        m["servers"].append(u)
                    if f"collector:{u}" not in m["sources"]:
                        m["sources"].append(f"collector:{u}")
                    if ev.get("note"):
                        note, why = self.open_note(u, ev["note"], peers)
                        m["note_status"] = why
                        if note:
                            from_payload(m, note, note.get("level") or "meta")
                            m["level"] = note.get("level") if m.get("level") != "full" else "full"
                            box(note.get("from"), label=note.get("label"), work=note.get("work"), role=note.get("role"),
                                hostname=(note.get("host") or {}).get("hostname"))
                            if note.get("to") and note.get("to_label"):
                                box(note["to"], label=note["to_label"])
                    elif m.get("level") is None:
                        m["level"] = "none"
                elif kind == "refused":  # a send the service refused while its client keeps it queued
                    mid = ev.get("msg") or f"{u}#{ev['entry']}"
                    m = msg(mid, ts=ev.get("ts") or M.iso(_dt(t)), frm=ev.get("from"))
                    m["to"] = m.get("to") or ev.get("to")
                    if m.get("state") in (None, "queued"):
                        m["state"] = "queued"
                    hd = m.setdefault("held", {"since": t})
                    hd["since"], hd["last"] = min(hd["since"], t), max(hd.get("last") or 0, t)
                    hd["why"], hd["server"] = ev.get("why") or hd.get("why"), u
                    if u not in m["servers"]:
                        m["servers"].append(u)
                    if f"collector:{u}" not in m["sources"]:
                        m["sources"].append(f"collector:{u}")
                    if ev.get("note") and m.get("level") != "full":
                        note, why = self.open_note(u, ev["note"], peers)
                        m["note_status"] = why
                        if note:
                            from_payload(m, note, note.get("level") or "meta")
                            m["level"] = note.get("level")
                            box(note.get("from"), label=note.get("label"), work=note.get("work"), role=note.get("role"),
                                hostname=(note.get("host") or {}).get("hostname"))
                            if note.get("to") and note.get("to_label"):
                                box(note["to"], label=note["to_label"])
                    elif m.get("level") is None:
                        m["level"] = "none"
                elif kind in ("take", "done", "recover"):
                    ents = ev.get("entries") if kind == "done" else [ev.get("entry")]
                    for en in ents or []:
                        mid = by_entry.get((ev.get("box"), en))
                        if not mid:
                            continue
                        m = msgs[mid]
                        if kind == "done":
                            m["state"] = "read"
                            m["read_at"] = m.get("read_at") or M.iso(_dt(t))
                            h = (ev.get("handled") or {}).get(en)
                            if h:
                                m["handled"] = m.get("handled") or h
                        elif m.get("state") == "unread":
                            m["state"] = "taken"
                        m["events"].append({"ev": kind, "t": t, "server": u})
                elif kind == "empty":  # an operator deleted every box of this service: what waited there is gone
                    for mid in by_entry.values():
                        if msgs[mid].get("state") in ("unread", "taken"):
                            msgs[mid]["state"] = "emptied"
                            msgs[mid]["events"].append({"ev": kind, "t": t, "server": u})
                    activity.append({"id": f"{u}#{ev['entry']}", "ev": kind, "box": ev.get("by"), "t": t, "server": u,
                                     "reason": f"{ev.get('boxes', '?')} box(es) deleted with {ev.get('entries', '?')} waiting message(s)"})
                else:  # the boxes' lifecycle on this service
                    if kind in ("open", "active", "close") and ev.get("box") and t >= box_open.get(ev["box"], (0,))[0]:
                        box_open[ev["box"]] = (t, kind != "close")
                    info = ev.get("info") if isinstance(ev.get("info"), dict) else {}
                    if info:
                        box(ev.get("box"), label=info.get("label"), role=info.get("role"), machine=info.get("machine"),
                            work=info.get("work"), project=info.get("project"), hostname=(info.get("host") or {}).get("hostname"),
                            title=info.get("title"), activity=info.get("activity"), at_ms=t)
                    activity.append({"id": f"{u}#{ev['entry']}", "ev": kind, "box": ev.get("box"), "t": t, "server": u,
                                     "reason": ev.get("reason")})

        # 4. the recaps the sessions sent this console when their work was over (kept until the operator deletes them)
        recap_ids = set()
        for rc in self.recaps.all():
            m = msg(rc["id"], ts=rc.get("ts"), frm=rc.get("from"), to=rc.get("to") or self.op.id)
            from_payload(m, rc, "full")
            m["state"], m["to_operator"] = "delivered", True
            m["recap"] = {"then": rc.get("then"), "received_at": rc.get("received_at"),
                          "open_questions": rc.get("open_questions") or []}
            if rc.get("server") and rc["server"] not in m["servers"]:
                m["servers"].append(rc["server"])
            if "recap" not in m["sources"]:
                m["sources"].append("recap")
            recap_ids.add(rc["id"])

        # stranded: still in a box that closed (on this computer, or by what a collector saw last) and is not active now
        active_now = {b["id"] for b in boxes.values() if b.get("active") and "table" in b.get("sources", [])}
        closed_here = {y.id for y in profiles.values() if y.closed_at}
        for m in msgs.values():
            to = m.get("to")
            if m.get("state") in ("unread", "taken") and not m.get("broadcast") and to and to not in active_now \
                    and (to in closed_here or box_open.get(to, (0, True))[1] is False):
                m["state"] = "stranded"

        # held: its sender keeps it queued (the service refuses it: the recipient does not list it yet, or is full).
        # Times only (since, last refusal): the ages are computed where they are shown, or the snapshot would change
        # at every refresh
        for m in msgs.values():
            if m.get("state") != "queued" or m.get("broadcast"):
                m.pop("held", None)
                continue
            m["held"] = m.get("held") or {"since": _ms(m.get("ts")) or int(time.time() * 1000)}
            ack = (self.console_state.get("held_ack") or {}).get(m["id"])
            if ack:  # the operator marked it handled: still queued, no longer in the alert
                m["held"] = {**m["held"], "ack": ack}

        # labels and the relations of each message to the boxes
        for m in msgs.values():
            m["from"] = m.pop("frm", None) or m.get("from")
            box(m.get("from"))
            if m.get("to"):
                box(m["to"])
            for b in m.get("to_boxes") or []:
                box(b)
            m.setdefault("state", "sent")
            k = m.get("kind")
            m["tier"] = tier_of(m)
        purged = self.purged  # what the purge removed stays gone, unless it waits in a box again
        for mid in [i for i, m in msgs.items() if i in purged and m.get("state") not in WAITING and i not in recap_ids]:
            del msgs[mid]
        if not self.debug_all():  # only the useful messages: what still waits, what waits for the user, the operator's
            open_ids = {i for i, b in boxes.items() if b.get("active") or (b.get("local") and not b.get("closed_at"))}
            decided = set(self.questions.decisions)
            pending_q = {q.get("re") for q in self.questions.pending() if q.get("re")}

            def useful(m):
                if m["id"] in recap_ids or m.get("kind") in ("question", "recap") or m.get("to") == self.op.id:
                    return True  # written to the operator: kept until the operator deletes it
                if m.get("broadcast"):
                    return False
                st = m.get("state")
                if st in WAITING and not (st == "sent" and m.get("from") not in open_ids):
                    return True  # still in a box (or queued by its sender); "sent" by a box gone: done
                return (m.get("handled") == "ask" and m["id"] not in decided) or m["id"] in pending_q
            for mid in [i for i, m in msgs.items() if not useful(m)]:
                del msgs[mid]
        for m in msgs.values():  # answered: a later message of the thread, from another box than the asker
            o = msgs.get(m.get("re") or "")
            if o and not o.get("broadcast") and m.get("from") and m["from"] != o.get("from") \
                    and m.get("kind") not in ("ack", "pong", "notice") and not m.get("broadcast"):
                o.setdefault("answers", []).append(m["id"])
        for a in activity:
            box(a["box"])
        # machines: a session's machine box; a box with none is its own machine group by host name
        for b in boxes.values():
            if b.get("role") == "machine":
                b["machine"] = b["id"]
        snap_msgs = sorted(msgs.values(), key=lambda m: m.get("ts") or "")
        full = {m["id"]: m for m in snap_msgs}
        light = []
        for m in snap_msgs:
            body = m.get("body")
            lm = {k: v for k, v in m.items() if k != "body" and not k.startswith("handled_")}
            if isinstance(body, str):
                lm["preview"] = body[:280]
                lm["body_len"] = len(body)
            light.append(lm)
        snap = {"operator": {"id": self.op.id, "fingerprint": self.op.fingerprint, "label": self.op.label,
                             "home": str(self.op.home)},
                "local": {"hostname": socket.gethostname(), "machine": machine.id if machine else None,
                          "client": str(CLIENT), "mmbox_home": str(M.base_dir())},
                "servers": servers, "boxes": sorted(boxes.values(), key=lambda b: b["id"]), "messages": light,
                "activity": activity[-3000:], "days": self.days, "errors": errors,
                "tuning": self.tuning(), "startup": self.startup,
                "local_rights": self.rights.get("local"), "service_rights": self.rights.get("networks") or {},
                "other_project_rights": self.rights.get("other_projects"),
                "questions": {"pending": self.questions.pending(), "recent": self.questions.recent(),
                              "decisions": dict(self.questions.decisions)},
                "notify": self.notify_settings(), "sounds": SOUNDS,
                "custom_sound": (self.console_state.get("custom_sound") or None),
                "pinned": self.archive.pinned(), "conv_ttl": self.archive.overrides(),
                "purge_keep_s": self.purge_keep_s(), "debug_all": self.debug_all()}
        d = hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()
        if d != self.digest:
            self.digest = d
            snap["version"] = self.snapshot.get("version", 0) + 1
            snap["generated_at"] = M.iso()
            self.snapshot = snap
            self.messages = full
            try:  # only what changed since the last model: the conversations keep their own copy
                self.archive.ingest(full.values(), self.conv_default_ttl())
            except Exception as e:
                log(f"archive: {type(e).__name__}: {e}")


def fmt_age(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60} min" if sec < 3600 else f"{sec / 3600:.1f} h" if sec < 86400 else f"{sec / 86400:.1f} d"


def _ms(ts) -> int | None:
    try:
        return int(M.parse_iso(ts).timestamp() * 1000)
    except (TypeError, ValueError, AttributeError):
        return None


def _dt(ms: int):
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def tier_of(m: dict) -> int:
    """1 essential (task, request, reply, info, broadcasts), 2 normal (the tool's messages a human may care about),
    3 debug (wake-ups, links, lock events, unannotated traffic)."""
    k = m.get("kind")
    if k in ("ping", "pong", "link", "lock") or m.get("lock"):
        return 3
    if k is None:
        return 3
    if k in ("task", "request", "reply", "info", "question", "answer", "recap"):
        return 1
    if k == "forward" and ((m.get("forwarded") or {}).get("kind") or "") in ("task", "request", "reply", "info"):
        return 1  # a message passed on (relayed, given back, moved by the operator): as essential as what it carries
    return 2


# ---------------------------------------------------------------- actions: through mmbox.py itself
def run_client(args: list[str], stdin: str | None = None, timeout: float = 120) -> dict:
    try:
        p = subprocess.run([sys.executable, str(CLIENT)] + args, input=stdin, capture_output=True, text=True, creationflags=NOWIN,
                           timeout=timeout, env={**os.environ, "MMBOX_NO_SELF_UPDATE": "1"})
        return {"ok": p.returncode == 0, "code": p.returncode, "out": p.stdout[-20000:], "err": p.stderr[-20000:],
                "cmd": "mmbox.py " + " ".join(a if re.fullmatch(r"[\w@.:/=,+-]+", a) else json.dumps(a) for a in args)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": f"timed out after {timeout:.0f} s"}


def senders_for(target: str) -> list[dict]:
    """The boxes of this computer that may write to `target`: open, with it as a live peer."""
    out = []
    for n in M.local_profiles():
        b = M.load_me(n)
        if not b or b.closed_at:
            continue
        try:
            peers = M.peers_load(b.local)
        except SystemExit:
            continue
        q = peers.get(target)
        if b.id == target or not q or q.get("revoked_at") or q.get("retired_at") or M.rights_of(peers, q) is None:
            continue
        out.append({"profile": n, "id": b.id, "label": b.label, "role": b.role, "alias": q.get("alias"),
                    "work": b.conf.get("work_project")})
    out.sort(key=lambda s: (s["role"] != "machine", s["profile"]))
    return out


def posters() -> list[dict]:
    """The session boxes of this computer that may post a broadcast (the machine box never posts)."""
    out = []
    for n in M.local_profiles():
        b = M.load_me(n)
        if b and b.role != "machine" and not b.closed_at and M.gw_urls(b):
            out.append({"profile": n, "id": b.id, "label": b.label, "work": b.conf.get("work_project")})
    return out


NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,60}")
URL_RE = re.compile(r"https?://[A-Za-z0-9.\-\[\]:]+(/[\w./-]*)?")


def act(state: State, body: dict) -> dict:
    a = body.get("action")
    s = lambda k: str(body.get(k) or "").strip()
    if a == "send":
        to, prof = s("to"), s("from")
        if not re.fullmatch(r"m[0-9a-z]{12}", to) or not NAME_RE.fullmatch(prof):
            return {"ok": False, "err": "choose a box to write to and a box of this computer to write from"}
        if prof not in {x["profile"] for x in senders_for(to)}:
            return {"ok": False, "err": f"{prof} is not paired with that box (or closed)"}
        kind = s("kind") or "info"
        if kind not in ("task", "request", "info", "reply"):
            return {"ok": False, "err": "kind: task, request, info or reply"}
        if not s("subject") or not s("body"):
            return {"ok": False, "err": "a subject and a body"}
        args = ["--profile", prof, "send", to, "--subject", s("subject")[:300], "--kind", kind]
        if s("re"):
            args += ["--re", s("re")[:80]]
        if body.get("reply_expected"):
            args.append("--reply-expected")
        return run_client(args, stdin=str(body.get("body"))[:400_000])
    if a == "broadcast":
        prof, scope = s("from"), s("scope")
        if prof not in {x["profile"] for x in posters()} or scope not in ("machine", "network"):
            return {"ok": False, "err": "a session box of this computer, and the scope machine or network"}
        if not s("subject") or not s("body"):
            return {"ok": False, "err": "a subject and a body"}
        args = ["--profile", prof, "chan", "post", "--scope", scope, "--subject", s("subject")[:300],
                "--kind", "request" if s("kind") == "request" else "info", "--ttl", str(int(body.get("ttl") or 60))]
        if s("topic"):
            args += ["--topic", s("topic")[:80]]
        return run_client(args, stdin=str(body.get("body"))[:16000])
    if a in ("server_add", "server_edit", "server_remove", "server_use", "server_check"):
        name = s("name")
        if a != "server_check" and not NAME_RE.fullmatch(name):
            return {"ok": False, "err": "a service name: letters, digits, . _ -"}
        if a == "server_add":
            if not URL_RE.fullmatch(s("url")):
                return {"ok": False, "err": "a URL, http(s)://host[:port]"}
            args = ["server", "add", name, s("url")]
            if s("address"):
                args += ["--address", s("address")]
            if s("note"):
                args += ["--note", s("note")[:300]]
            if body.get("no_check"):
                args.append("--no-check")
        elif a == "server_edit":
            args = ["server", "edit", name]
            if s("rename"):
                args += ["--rename", s("rename")]
            if s("url"):
                if not URL_RE.fullmatch(s("url")):
                    return {"ok": False, "err": "a URL, http(s)://host[:port]"}
                args += ["--url", s("url")]
            if body.get("no_address"):
                args.append("--no-address")
            elif s("address"):
                args += ["--address", s("address")]
            if "note" in body:
                args += ["--note", s("note")[:300]]
        elif a == "server_remove":
            args = ["server", "remove", name] + (["--force"] if body.get("force") else [])
        elif a == "server_use":
            args = ["server", "use", name] + (["--quiet"] if body.get("quiet") else [])
        else:
            args = ["server", "list", "--check"]
        r = run_client(args, timeout=300)
        threading.Thread(target=state.refresh, daemon=True).start()
        return r
    if a == "collector":
        url, level = s("url"), s("level")
        if level == "off":
            r = run_client(["collector", "disable", url])
        elif level in ("meta", "subject", "full"):
            r = run_client(["collector", "enable", url, "--level", level, "--fingerprint",
                            s("fingerprint") or state.op.fingerprint])
        else:
            return {"ok": False, "err": "level: off, meta, subject or full"}
        threading.Thread(target=state.refresh, daemon=True).start()
        return r
    if a == "debug_all":  # Settings: keep every message in the list (debug), or only the useful ones
        state.console_state["debug_all"] = bool(body.get("on"))
        state.save_console_state()
        state.digest = ""
        threading.Thread(target=state.refresh, daemon=True).start()
        return {"ok": True, "debug_all": state.debug_all()}
    if a == "titlebar":  # Settings: the app's own title bar, or the system's
        state.console_state["own_titlebar"] = bool(body.get("on"))
        state.save_console_state()
        app = getattr(Handler, "app", None)
        if app:
            app.set_titlebar(state.own_titlebar())
        return {"ok": True, "titlebar": state.own_titlebar(), "window": app.frame() if app else None}
    if a == "window":  # the page's title bar: its buttons, its drag area, the window's edges
        app = getattr(Handler, "app", None)
        return app.window_op(s("op"), s("edge")) if app else {"ok": False, "err": "no window of its own (browser)"}
    if a == "tune":
        args = ["tune"]
        if body.get("reset"):
            args.append("--reset")
        if body.get("on_done") not in (None, ""):
            if body["on_done"] not in M.ON_DONE:
                return {"ok": False, "err": "on_done: " + " or ".join(M.ON_DONE)}
            args += ["--on-done", body["on_done"]]
        for k in M.TUNABLE:
            v = body.get(k)
            if v not in (None, ""):
                try:
                    args += ["--" + k.replace("_", "-"), str(float(v))]
                except (TypeError, ValueError):
                    return {"ok": False, "err": f"{k}: a number"}
        r = run_client(args)
        threading.Thread(target=state.refresh, daemon=True).start()
        return r
    if a == "purge_archive":  # the memory view: every message no longer waiting in a box, from this computer
        return state.purge_everything()
    if a == "purge_keep":  # Settings: how much of the open boxes' finished messages the purge keeps
        try:
            v = float(body.get("seconds"))
        except (TypeError, ValueError):
            return {"ok": False, "err": "a duration in seconds"}
        if not 0 <= v <= PURGE_KEEP_MAX_S:
            return {"ok": False, "err": f"between 0 and {PURGE_KEEP_MAX_S // 86400} days"}
        state.console_state["purge_keep_s"] = v
        state.save_console_state()
        state._usage = (0.0, {})
        return {"ok": True, "purge_keep_s": v}
    if a == "held_ack":  # the held alert: a message (or all of a recipient's) marked handled, or back in the alert
        ids = body.get("ids")
        if not isinstance(ids, list) or len(ids) > 5000:
            return {"ok": False, "err": "ids: a list of message ids"}
        return state.held_ack(ids, body.get("on", True) is not False)
    if a == "move_messages":  # the list's batch action: waiting messages handed to another box of this computer
        ids = body.get("ids")
        if not isinstance(ids, list) or len(ids) > 5000:
            return {"ok": False, "err": "ids: a list of message ids"}
        return state.move_messages(ids, str(body.get("to") or ""))
    if a == "delete_messages":  # the open message: itself (its menu) or its whole thread (the reader's bin)
        ids = body.get("ids")
        if not isinstance(ids, list) or len(ids) > 5000:
            return {"ok": False, "err": "ids: a list of message ids"}
        return state.delete_messages(ids)
    if a == "box_purge":  # a box's menu: its history, from this computer
        return state.purge_box(str(body.get("id") or ""))
    if a in ("box_empty", "box_close", "box_delete"):  # a box's menu: what waits in it, closing, deleting
        return state.box_command(str(body.get("id") or ""), a[4:])
    if a.startswith("account_"):  # the account: join, approve or decline a new device, revoke one
        return state.account_action(a, body)
    if a.startswith(("butler_", "daemon_", "session_")):  # the daemon and its butler: chat, sessions, inventory, ops
        return state.butler_action(a, body)
    if a == "empty_boxes":  # the memory view: every box of every service where this console is an operator
        return state.empty_boxes()
    if a == "col_config":  # an operator's collector: retention, hold every entry 48 h more, purge
        url = s("url")
        srv = next((x for x in state.snapshot.get("servers", []) if x["url"] == url), None)
        if not srv or not srv.get("operator"):
            return {"ok": False, "err": "this console is no operator of that service"}
        req = {}
        if body.get("keep_days") not in (None, ""):
            req["keep_days"] = body["keep_days"]
        if body.get("hold_hours"):
            req["hold_hours"] = int(body["hold_hours"])
        if body.get("purge"):
            req["purge"] = True
        r = call(url, state.op, "/col/config", req)
        if body.get("purge") and r.get("ok"):
            state.col.pop(url, None)
        if body.get("dismiss_startup") or r.get("ok"):
            state.startup = None
        threading.Thread(target=state.refresh, daemon=True).start()
        return {"ok": bool(r.get("ok")), "err": r.get("error"), "out": json.dumps(r.get("changed") or {}), "result": r}
    if a == "answer":  # the operator's answer to a box's question
        if not isinstance(body.get("choices"), list):
            return {"ok": False, "err": "choices: a list"}
        return state.answer(s("id"), body["choices"], str(body.get("text") or ""))
    if a == "decide":  # the operator's decision on a message a box asked its user about
        return state.decide(s("id"), s("decision"), str(body.get("text") or ""))
    if a == "rights":  # how this computer handles what a box (or a machine and its sessions) sends
        target, rights = s("target"), s("rights")
        urls = {x["url"] for x in state.snapshot.get("servers", [])}
        if rights not in ("execute", "ask", "archive", "follow") or not (
                target in ("local", "projects") or re.fullmatch(r"m[0-9a-z]{12}", target)
                or re.fullmatch(r"project:[A-Za-z0-9._-]{1,80}", target)
                or (target.startswith("@") and target[1:] in urls)
                or (target.startswith("projects@") and target[9:] in urls)):
            return {"ok": False, "err": "rights: execute, ask, archive or follow, for a box id, local, projects, "
                                        "project:<name>, @<service URL> or projects@<service URL>"}
        r = run_client(["--profile", M.MACHINE_PROFILE, "rights", target, rights])
        threading.Thread(target=state.refresh, daemon=True).start()
        return r
    if a in ("conv_ttl", "conv_pin"):  # a conversation's own expiry, or its pin
        box = s("box")
        if not re.fullmatch(r"m[0-9a-z]{12}", box):
            return {"ok": False, "err": "a box id"}
        if a == "conv_pin":
            state.archive.set_pin(box, bool(body.get("pinned")), state.conv_default_ttl())
        else:
            days = body.get("days")
            try:
                ttl = None if days in (None, "") else float(days) * 86400
            except (TypeError, ValueError):
                return {"ok": False, "err": "days: a number"}
            if ttl is not None and not 3600 <= ttl <= 3650 * 86400:
                return {"ok": False, "err": "between 1 hour (0.04 d) and 10 years"}
            state.archive.set_ttl(box, ttl, state.conv_default_ttl())
        threading.Thread(target=state.refresh, daemon=True).start()
        ttl_s, pinned = state.archive.settings(box)
        return {"ok": True, "ttl_s": ttl_s, "pinned": pinned}
    if a == "stats_clear":  # this console's counter of a network back to zero (the service keeps its 7 days)
        url = s("url")
        if url not in {x["url"] for x in state.snapshot.get("servers", [])}:
            return {"ok": False, "err": "unknown service"}
        state.console_state.setdefault("stats_clear", {})[url] = int(time.time() * 1000)
        state.save_console_state()
        threading.Thread(target=state.refresh, daemon=True).start()
        return {"ok": True}
    if a in ("pause", "resume"):  # stop or resume receiving from a service, on this computer (kept across restarts)
        srv = next((x for x in state.snapshot.get("servers", []) if x["url"] == s("url")), None)
        if not srv or not srv.get("known"):
            return {"ok": False, "err": "a service of this computer's list (Servers › Add to the list)"}
        r = run_client(["server", a, srv["name"]])
        threading.Thread(target=state.refresh, daemon=True).start()
        return r
    if a == "alert":  # the window's page: its alert shown by the system (its WebKit has no browser notifications)
        if not (state.desktop and state.desktop.ok):
            return {"ok": False, "err": "no system notifications on this computer"}
        mid = s("msg") or None
        state.desktop.alert(s("title")[:200] or "mmbox", s("body"), msg=mid, late=bool(body.get("late")))
        return {"ok": True}
    if a == "notify":  # how a new question is announced: persistent notification, red dot, sound
        cur = state.notify_settings()
        for k in ("popup", "badge", "audio"):
            if k in body:
                cur[k] = bool(body[k])
        if "sound" in body:
            snd = OLD_SOUND_NAMES.get(s("sound"), s("sound"))
            if snd not in SOUNDS and snd != "custom":
                return {"ok": False, "err": "unknown sound"}
            if snd == "custom" and not (state.op.home / "sounds" / "custom.mp3").is_file():
                return {"ok": False, "err": "no imported sound yet"}
            cur["sound"] = snd
        state.console_state["notify"] = cur
        state.save_console_state()
        state.tick()
        return {"ok": True, "notify": cur}
    if a == "sound_upload":  # an MP3 of the operator's own, kept in the console's folder
        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except ValueError:
            return {"ok": False, "err": "not base64"}
        if not 0 < len(data) <= 3_000_000:
            return {"ok": False, "err": "an MP3 of 3 MB at most"}
        if not (data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)):
            return {"ok": False, "err": "not an MP3 file"}
        d = state.op.home / "sounds"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "custom.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / "custom.mp3")
        state.console_state["custom_sound"] = {"name": re.sub(r"[^\w .()-]+", "_", s("name"))[:120] or "sound.mp3", "size": len(data), "at": M.iso()}
        state.console_state["notify"] = {**state.notify_settings(), "sound": "custom", "audio": True, "badge": state.notify_settings()["badge"]}
        state.save_console_state()
        threading.Thread(target=state.refresh, daemon=True).start()
        return {"ok": True}
    if a == "test_sound":  # played on this computer, as when no tab is open
        p = state.sound_path()
        if not p:
            return {"ok": False, "err": "no sound chosen"}
        Desktop.play(p)
        return {"ok": True}
    if a == "dismiss_startup":
        state.startup = None
        return {"ok": True}
    if a == "register":  # this console's key on a service where it is an operator (for the clients to seal for it)
        url = s("url")
        return call(url, state.op, "/card", {"card": state.op.card()})
    if a == "refresh":
        state.refresh()
        return {"ok": True}
    return {"ok": False, "err": f"unknown action {a!r}"}


# ---------------------------------------------------------------- the HTTP side
class Handler(BaseHTTPRequestHandler):
    server_version = "mmbox-console"
    state: State = None
    token = ""
    port = 0

    def log_message(self, fmt, *args):
        if VERBOSE:
            log(fmt % args)

    PAGE_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
                "media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
    FILE_CSP = "default-src 'none'; img-src 'self'; frame-ancestors 'none'; sandbox"  # a file never runs as a page

    def _headers(self, status: int, ctype: str, length: int, extra: dict | None = None, csp: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", csp or self.PAGE_CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, obj, status: int = 200):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self._headers(status, "application/json; charset=utf-8", len(raw))
        self.wfile.write(raw)

    def _host_ok(self) -> bool:  # DNS rebinding: only our own name and port
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}")

    def _api_ok(self) -> bool:
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Console-Token", ""), self.token)

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "wrong host"}, 403)
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/"):
            if not self._api_ok():
                return self._json({"error": "missing or wrong token: open the URL the console printed"}, 401)
            q = urllib.parse.parse_qs(u.query)
            st = self.state
            if u.path == "/api/snapshot":
                st.touch()
                have = int((q.get("version") or ["0"])[0] or 0)
                with st.lock:
                    snap = st.snapshot
                if have and have == snap.get("version"):
                    return self._json({"version": have, "unchanged": True})
                # what changes between two refreshes, as it is now: the operator's settings, the questions
                return self._json({**snap, "notify": st.notify_settings(), "custom_sound": st.console_state.get("custom_sound"),
                                   "questions": {"pending": st.questions.pending(), "recent": st.questions.recent(),
                                                 "decisions": dict(st.questions.decisions)}})
            if u.path == "/api/message":
                mid = (q.get("id") or [""])[0]
                with st.lock:
                    m = st.messages.get(mid)
                return self._json(m or {"error": "unknown message"}, 200 if m else 404)
            if u.path == "/api/chat":  # a box's conversation, as this console keeps it
                box = (q.get("box") or [""])[0]
                ttl_s, pinned = st.archive.settings(box)
                return self._json({"box": box, "messages": st.archive.chat(box), "ttl_s": ttl_s, "pinned": pinned,
                                   "default_ttl_s": st.conv_default_ttl(), "now": time.time()})
            if u.path == "/api/pending":  # every few seconds, even from a hidden tab: what waits, and what the tab does
                now = time.time()
                if (q.get("notify") or ["0"])[0] == "1":
                    st.clients["notify"] = now
                if (q.get("audio") or ["0"])[0] == "1":
                    st.clients["audio"] = now
                pend = st.questions.pending()
                return self._json({"count": len(pend), "ids": [x["id"] for x in pend], "version": st.snapshot.get("version", 0),
                                   "latest": ({k: pend[-1].get(k) for k in ("id", "from", "label", "title", "subject")} if pend else None),
                                   "desktop": bool(st.desktop and st.desktop.ok), "held": len(st.held()),
                                   # the window's own page: what a notification's click asked it to open
                                   **({"goto": g} if (q.get("app") or ["0"])[0] == "1" and getattr(Handler, "app", None)
                                      and (g := Handler.app.take_goto()) else {})})
            if u.path == "/api/passphrase":  # the onboarding's Generate: a recovery passphrase from the system's CSPRNG
                try:
                    return self._json({"ok": True, **recovery_passphrase()})
                except (OSError, RuntimeError) as e:
                    return self._json({"ok": False, "err": str(e)})
            if u.path == "/api/account":  # Settings: the account, its devices, the requests to approve
                return self._json(st.account_state())
            if u.path == "/api/butler":  # the Butler tab: its state, inventory, started sessions, chat
                return self._json(st.butler_state())
            if u.path == "/api/window":  # the window's page at its loading: its own title bar or not, maximized
                app = getattr(Handler, "app", None)
                return self._json({"window": app.frame() if app else None, "titlebar": st.own_titlebar()})
            if u.path == "/api/show":  # a second launch of the app: this window to the front
                if getattr(Handler, "app", None):
                    Handler.app.show()
                return self._json({"ok": True, "window": bool(getattr(Handler, "app", None))})
            if u.path == "/api/session_output":  # a hidden session's output (this computer), from a byte offset
                try:
                    since = int((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                return self._json(B.session_output((q.get("profile") or [""])[0], since) if B else {"lines": [], "next": 0})
            if u.path == "/api/butler_log":  # the butler's live output, from a byte offset
                try:
                    since = int((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0
                return self._json(st.butler_log(since))
            if u.path == "/api/usage":  # the explorer's memory frame and the memory view
                return self._json(st.usage(fresh=(q.get("fresh") or ["0"])[0] == "1"))
            if u.path == "/api/sound":  # the operator's own sound (the presets are static files)
                f = st.op.home / "sounds" / "custom.mp3"
                if not f.is_file():
                    return self._json({"error": "no imported sound"}, 404)
                raw = f.read_bytes()
                self._headers(200, "audio/mpeg", len(raw))
                self.wfile.write(raw)
                return
            if u.path == "/api/attachment":
                return self._attachment(q)
            if u.path == "/api/senders":
                return self._json({"senders": senders_for((q.get("to") or [""])[0]), "posters": posters()})
            return self._json({"error": "not found"}, 404)
        path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        if (old := re.fullmatch(r"sounds/([\w-]+)\.mp3", path)) and old[1] in OLD_SOUND_NAMES:  # a tab opened before the rename
            path = f"sounds/{OLD_SOUND_NAMES[old[1]]}.mp3"
        f = (STATIC / path).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        raw = f.read_bytes()
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._headers(200, ctype, len(raw))
        self.wfile.write(raw)

    def _attachment(self, q: dict):
        """GET /api/attachment?id=<message id>&i=<index>[&dl=1]: the file of one attachment. Inline only for a raster
        image whose bytes match its type; anything else (SVG included) as a download."""
        st = self.state
        mid = (q.get("id") or [""])[0]
        try:
            i = int((q.get("i") or ["-1"])[0])
        except ValueError:
            i = -1
        with st.lock:
            m = st.messages.get(mid)
        atts = (m or {}).get("attachments") or []
        if not atts and mid:
            atts = (st.archive.get(mid) or {}).get("attachments") or []
        if not m and not atts:
            return self._json({"error": "unknown message"}, 404)
        if not 0 <= i < len(atts) or not isinstance(atts[i], dict):
            return self._json({"error": "no such attachment"}, 404)
        a = atts[i]
        if not a.get("path"):
            return self._json({"error": "not on this computer"}, 404)
        f = att_allowed(a["path"], st.op.home)
        if f is None:
            log(f"attachment refused, outside the attachment folders: {a['path']}")
            return self._json({"error": "outside the attachment folders"}, 403)
        try:
            if not f.is_file() or f.stat().st_size > 8_000_000:
                return self._json({"error": "not on this computer"}, 404)
            raw = f.read_bytes()
        except OSError:
            return self._json({"error": "not on this computer"}, 404)
        name = str(a.get("name") or "file")
        declared = {"image/jpg": "image/jpeg", "image/x-ms-bmp": "image/bmp"}.get(str(a.get("type")).lower(), str(a.get("type")).lower())
        kind = sniff_image(raw[:64])
        inline = (q.get("dl") or ["0"])[0] != "1" and declared in RASTER and kind == declared
        ascii_name = re.sub(r'[^\w.() -]', "_", name.encode("ascii", "replace").decode())[:120] or "file"
        disp = f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"
        self._headers(200, kind if inline else "application/octet-stream", len(raw),
                      {"Content-Disposition": disp, "Cross-Origin-Resource-Policy": "same-origin"}, csp=self.FILE_CSP)
        self.wfile.write(raw)

    def do_POST(self):
        if not self._api_ok():
            return self._json({"error": "missing or wrong token"}, 401)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json({"error": "JSON only"}, 415)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4_200_000:  # an imported sound, base64
            return self._json({"error": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "not JSON"}, 400)
        if urllib.parse.urlparse(self.path).path != "/api/action" or not isinstance(body, dict):
            return self._json({"error": "not found"}, 404)
        try:
            return self._json(act(self.state, body))
        except Exception as e:  # shown to the operator, never a crash of the console
            log(f"action {body.get('action')}: {type(e).__name__}: {e}")
            return self._json({"ok": False, "err": f"{type(e).__name__}: {e}"}, 500)


def main():
    global M, CLIENT, B
    ap = argparse.ArgumentParser(description="mmbox console: the operator's view of the mmbox services, boxes and messages")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MMBOX_CONSOLE_PORT", "8765")))
    ap.add_argument("--client", help="path of mmbox.py (default: ../client/mmbox.py, then the one mmbox-install put here)")
    ap.add_argument("--days", type=float, default=7, help="how far back to show messages (0: everything kept)")
    ap.add_argument("--refresh", type=float, default=8, help="seconds between two refreshes")
    ap.add_argument("--open", action="store_true", help="open the console at once (its window, or the browser with --browser)")
    ap.add_argument("--browser", action="store_true",
                    help="no window of its own: the icon and --open open the console in the default browser")
    ap.add_argument("--whoami", action="store_true", help="print the operator id and fingerprint, then exit")
    ap.add_argument("--has-key", action="store_true", help="exit 0 when this computer holds the operator key, 1 when not "
                                                          "(nothing made); prints where it is kept")
    ap.add_argument("--export-key", metavar="FILE", help="write the operator key to FILE, sealed with a passphrase asked "
                                                        "twice, for another computer of the same operator; then exit")
    ap.add_argument("--import-key", metavar="FILE", help="take the operator key from such a FILE (its passphrase asked), kept "
                                                        "in this system's keyring or DPAPI; then exit")
    ap.add_argument("--force", action="store_true", help="with --import-key: replace another operator key held here")
    ap.add_argument("--account-create", metavar="RECOVERY_FILE", help="make the account: its root key (kept here, and "
                    "sealed into RECOVERY_FILE with a passphrase) and this console as its first device")
    ap.add_argument("--account-show", action="store_true", help="the account, its devices and revocations")
    ap.add_argument("--account-join", metavar="FILE", help="this console asks to join an account: a request with a code")
    ap.add_argument("--account-approve", metavar="FILE", help="from a device of the account: approve that request")
    ap.add_argument("--code", help="with --account-approve: the code the new device shows")
    ap.add_argument("--account-complete", metavar="FILE", help="on the new device: the approved request, back")
    ap.add_argument("--account-revoke", metavar="DEVICE", help="revoke a device of the account")
    ap.add_argument("--account-head", action="store_true", help="sign the chain's head now (done daily by the console)")
    ap.add_argument("--account-chain", metavar="FILE", help="write the account's chain (for `mmbox.py daemon operator "
                    "--account <fp> --chain FILE`)")
    ap.add_argument("--passphrase-stdin", action="store_true", help=argparse.SUPPRESS)  # tests and automation only
    ap.add_argument("--no-tray", action="store_true", help="no icon in the notification area, no system notification")
    a = ap.parse_args()
    CLIENT = find_client(a.client)
    M = load_client(CLIENT)
    M.pin_hosts()
    B = load_butler()
    if a.has_key or a.export_key or a.import_key:
        sys.exit(key_command(a))
    if any(getattr(a, k) for k in ("account_create", "account_show", "account_join", "account_approve", "account_complete",
                                   "account_revoke", "account_head", "account_chain")):
        sys.exit(account_command(a))
    op = Operator()
    if a.whoami:
        print(f"operator id {op.id}\nfingerprint {op.fingerprint}\nkeys in {op.home}")
        return
    if not a.browser and show_running(op.home, a.port):
        log("already running: its window is brought to the front")
        return
    state = State(op, a.days)
    log(f"client {CLIENT}; operator {op.id} ({op.fingerprint}); first refresh in the background, the window opens now")

    def loop():
        n = 0
        try:  # the first refresh, while the window shows its start screen (it reads every service: seconds)
            state.refresh()
        except Exception as e:
            log(f"first refresh failed: {type(e).__name__}: {e}")
        while True:
            time.sleep(max(2.0, a.refresh))
            try:
                state.refresh()
                if n % 8 == 0:
                    state.ensure_butler()  # runs with the console (Settings), one per computer
                if n % 4 == 0 and (account_chain() or state.console_state.get("joining")):
                    state.account_sync()  # the chain in step with the services, the requests to approve
                if n % 400 == 0 and account_chain():
                    try:
                        if account_head_due(op):  # the daily head of the account's chain (client/ACCOUNTS.md)
                            log("account: daily head signed")
                    except Exception as e:  # noqa: BLE001 - logged; the console goes on
                        log(f"account head failed: {type(e).__name__}: {e}")
                n += 1
            except Exception as e:
                log(f"refresh failed: {type(e).__name__}: {e}")
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=state.ensure_butler, daemon=True).start()  # the daemon with the app, at once (Settings)
    Handler.state, Handler.token, Handler.port = state, secrets.token_urlsafe(24), a.port
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/#token={Handler.token}"
    def url_of(question: bool | str = False, held: bool = False, msg: str | None = None) -> str:
        """The console's address in the browser, on a message, a question (or the next one) or the held messages."""
        q = urllib.parse.quote
        return url + (f"&msg={q(msg, safe='')}" if msg else f"&question={q(question, safe='') if isinstance(question, str) else 'next'}"
                      if question else "&view=held" if held else "")
    app = None if a.browser else app_window(url, a.open or a.no_tray, op.home, state.own_titlebar())
    Handler.app = app
    instance = op.home / "instance.json"  # for a second launch: bring this window to the front instead
    try:
        fd = os.open(instance, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": a.port, "token": Handler.token}, f)
    except OSError:
        pass
    if not a.no_tray:
        def quit_console():
            log("stopped from the notification area")
            if app:
                app.quit()
            threading.Thread(target=httpd.shutdown, daemon=True).start()
        state.desktop = Desktop(op.home, url_of, quit_console, app)
        state.tick()
    print(f"mmbox console: {url}\n  operator id {op.id} · fingerprint {op.fingerprint}\n  Ctrl+C to stop", flush=True)
    if a.open and not app:
        import webbrowser
        webbrowser.open(url)
    if app is None:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    if desktop_session():  # Ctrl+C and SIGTERM, which the GTK loop would otherwise never let Python see
        import signal
        from gi.repository import GLib
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (app.quit(), False)[1])
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    httpd.shutdown()


# ---------------------------------------------------------------- the account (client/ACCOUNTS.md): root key, device chain
# The recovery passphrase the onboarding offers: 12 words drawn uniformly from the BIP39 English list (2048 words: 11
# bits each, 132 bits, no modulo bias) with `secrets`, the system's CSPRNG (getrandom / os.urandom). Never Math.random,
# a Mersenne Twister or a time seed: the wallet thefts of Randstorm (BitcoinJS falling back to Math.random), Milk Sad
# (bx seed: mt19937 seeded with 32 bits of time), the Debian OpenSSL key space (PID-seeded) all came from that. The list
# is checked against its published SHA-256 before use: a truncated or altered list is refused.
BIP39_SHA256 = "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"
PASSPHRASE_WORDS = 12


def recovery_passphrase() -> dict:
    raw = (Path(__file__).resolve().parent / "bip39-english.txt").read_bytes()
    if hashlib.sha256(raw).hexdigest() != BIP39_SHA256:
        raise RuntimeError("bip39-english.txt does not match its published SHA-256: refused")
    words = raw.decode("ascii").split()
    if len(words) != 2048 or len(set(words)) != 2048:
        raise RuntimeError("bip39-english.txt: not 2048 distinct words")
    phrase = " ".join(secrets.choice(words) for _ in range(PASSPHRASE_WORDS))
    return {"phrase": phrase, "words": PASSPHRASE_WORDS, "bits": PASSPHRASE_WORDS * 11,
            "source": "the system's CSPRNG (Python secrets), BIP39 English list"}


def default_recovery_path() -> Path:
    """Where the onboarding offers to write the recovery export: the user's Documents (Windows) or home folder."""
    docs = Path.home() / "Documents"
    return (docs if os.name == "nt" and docs.is_dir() else Path.home()) / "mmbox-account-recovery.json"


def account_chain() -> list | None:
    try:
        return json.loads((console_home() / "account.json").read_text(encoding="utf-8")).get("chain") or None
    except (OSError, ValueError):
        return None


def account_save(chain: list) -> None:
    p = console_home() / "account.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"chain": chain}, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def account_lib():
    p = Path(CLIENT).with_name("mmbox_account.py")
    spec = importlib.util.spec_from_file_location("mmbox_account", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ask_passphrase(a, prompt: str, twice: bool) -> str:
    import getpass
    if getattr(a, "passphrase_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass(prompt)
    if twice and pw != getpass.getpass("The same passphrase again: "):
        raise SystemExit("the passphrases differ")
    return pw


def device_record(op: "Operator") -> dict:
    return {"id": op.id, "sign_pub": M.b64e(op.sign_pub), "box_pub": M.b64e(op.box_pub),
            "label": op.label, "host": socket.gethostname(), "user": op.user}


def account_head_due(op: "Operator", hours: float = 20) -> bool:
    """The daily head: this device re-signs the chain's end, so that daemons know it is current (a withheld revocation
    shows as a stale chain). Returns whether it signed one."""
    chain = account_chain()
    if not chain or time.time() - chain[-1]["ts"] < hours * 3600:
        return False
    A = account_lib()
    if op.id not in A.verify(chain, max_age=None)["devices"]:
        return False
    chain.append(A.make_link(chain, "head", None, op.id, op.sign))
    account_save(chain)
    return True


def account_create(op: "Operator", passphrase: str, recovery: Path) -> dict:
    """The account: its root key (kept here: keyring or DPAPI), sealed into `recovery` with `passphrase`, and this
    console as its first device. Shared by --account-create and the onboarding window."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if account_chain():
        raise ValueError("this console is already a device of an account")
    if len(passphrase) < 16:
        raise ValueError("the passphrase is shorter than 16 characters")
    A = account_lib()
    root = Ed25519PrivateKey.generate()
    rpub = root.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    raw = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
               encryption_algorithm=serialization.NoEncryption())
    rec = {"id": A.account_id(rpub), "sign_key": M.b64e(root.private_bytes(**raw)), "box_key": M.b64e(os.urandom(32)),
           "created": M.iso()}
    recovery.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({**seal_identity(rec, passphrase), "kind": "mmbox account root"}, f)
    where = KeyStore(console_home(), "root").save(rec)
    account_save([A.make_link([], "add", device_record(op), "root", root, rpub)])
    fp = A.root_fingerprint(rpub)
    pin = run_client(["daemon", "operator", "--account", fp], timeout=60) if CLIENT else {"ok": False}
    return {"account": rec["id"], "fingerprint": fp, "root_kept": where, "recovery": str(recovery),
            "pinned_here": bool(pin.get("ok"))}


def account_command(a) -> int:
    """--account-*: make the account (its root key and the first device: this console), a new device asking to join,
    approving it from a device of the account (its code checked), revoking one, the daily head, the chain for a daemon."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    A = account_lib()
    op = Operator()
    chain = account_chain()
    raw_pub = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    if a.account_create:
        if chain:
            raise SystemExit("this console is already a device of an account (--account-show)")
        pw = ask_passphrase(a, "A passphrase for the recovery export of the root key (16 characters at least): ", True)
        try:
            res = account_create(op, pw, Path(a.account_create))
        except ValueError as e:
            raise SystemExit(f"not made: {e}")
        print(f"account {res['account']} made; root fingerprint {res['fingerprint']} (pinned on this computer's daemon: "
              f"{'yes' if res['pinned_here'] else 'no'}; on the others: `mmbox.py daemon operator --account "
              f"{res['fingerprint']}`); root key kept in {res['root_kept']}; recovery export written to {res['recovery']}: "
              f"keep it offline, then delete it from this computer")
        return 0
    if a.account_join:
        if chain:
            raise SystemExit("this console is already a device of an account")
        code = A.join_code(device_record(op))
        Path(a.account_join).write_text(json.dumps({"join": device_record(op), "code": code}, indent=1), encoding="utf-8")
        print(f"join request written to {a.account_join}; its code: {code} (the approving device checks it)")
        return 0
    if a.account_approve:
        if not chain:
            raise SystemExit("this console is no device of an account")
        req = json.loads(Path(a.account_approve).read_text(encoding="utf-8"))
        dev = req.get("join") or {}
        print(f"approve {dev.get('label')} on {dev.get('host')} ({dev.get('id')})?")
        code = a.code or input("The code the new device shows: ").strip()
        if code != A.join_code(dev) or code != req.get("code"):
            raise SystemExit("the codes differ: not approved")
        chain.append(A.make_link(chain, "add", dev, op.id, op.sign))
        A.verify(chain, max_age=None)
        account_save(chain)
        Path(a.account_approve).write_text(json.dumps({**req, "chain": chain}, indent=1), encoding="utf-8")
        print(f"approved: {dev.get('id')} is a device of the account; {a.account_approve} now holds the chain: "
              f"carry it back to the new device (--account-complete)")
        return 0
    if a.account_complete:
        req = json.loads(Path(a.account_complete).read_text(encoding="utf-8"))
        st = A.verify(req.get("chain") or [], max_age=None)
        if op.id not in st["devices"]:
            raise SystemExit("this console is not in that chain: not approved yet")
        account_save(req["chain"])
        print(f"this console is now a device of account {st['account']} (root {st['fingerprint']})")
        return 0
    if not chain:
        raise SystemExit("this console is no device of an account (--account-create, or --account-join)")
    if a.account_revoke:
        st = A.verify(chain, max_age=None)
        dev = st["devices"].get(a.account_revoke)
        if not dev:
            raise SystemExit(f"{a.account_revoke}: no active device of the account")
        chain.append(A.make_link(chain, "revoke", dev, op.id, op.sign))
        account_save(chain)
        print(f"revoked {a.account_revoke}; the daemons learn it with the next op of a device (or from the service)")
        return 0
    if a.account_head:
        print("head signed" if account_head_due(op, hours=0) else "not signed (this console is not an active device)")
        return 0
    if a.account_chain:
        Path(a.account_chain).write_text(json.dumps(chain, indent=1), encoding="utf-8")
        print(f"chain written to {a.account_chain}")
        return 0
    st = A.verify(chain, max_age=None)
    print(f"account {st['account']} · root {st['fingerprint']} · {st['length']} link(s), newest "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st['head_ts']))}")
    for i, d in st["devices"].items():
        print(f"  device {i}: {d.get('label')} on {d.get('host')}" + ("  (this console)" if i == op.id else ""))
    for i in st["revoked"]:
        print(f"  revoked {i}")
    return 0


def key_command(a) -> int:
    """--has-key, --export-key, --import-key: the operator key between the computers of one operator."""
    import getpass
    store = KeyStore(console_home())
    console_home().mkdir(parents=True, exist_ok=True)
    try:
        ident = store.load()
    except (RuntimeError, OSError, ValueError) as e:
        print(f"the operator key cannot be read: {e}", file=sys.stderr)
        return 3
    if a.has_key:
        if not ident:
            print("no operator key on this computer")
            return 1
        op = Operator(create=False)
        print(f"operator key {op.id} (fingerprint {op.fingerprint}) kept in {store.where()}")
        return 0
    if a.export_key:
        if not ident:
            print("no operator key here to export", file=sys.stderr)
            return 1
        pw = getpass.getpass("A passphrase for this export (12 characters at least): ")
        if len(pw) < 12 or pw != getpass.getpass("The same passphrase again: "):
            print("not exported: the passphrases differ, or it is shorter than 12 characters", file=sys.stderr)
            return 1
        out = Path(a.export_key)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(seal_identity(ident, pw), f)
        print(f"exported to {out} (sealed): carry it to the other computer, import it there, then delete it")
        return 0
    blob = json.loads(Path(a.import_key).read_text(encoding="utf-8"))
    if not blob.get("mmbox_operator_key"):
        print(f"{a.import_key}: not an operator key export", file=sys.stderr)
        return 1
    if ident and ident.get("id") != blob.get("id") and not a.force:
        print(f"this computer holds another operator key ({ident.get('id')}): --force replaces it", file=sys.stderr)
        return 1
    try:
        new = open_identity(blob, getpass.getpass(f"The passphrase of {a.import_key}: "))
    except ValueError as e:
        print(f"not imported: {e}", file=sys.stderr)
        return 1
    where = store.save(new)
    op = Operator(create=False)
    print(f"operator key {op.id} (fingerprint {op.fingerprint}) imported, kept in {where}; delete {a.import_key} now")
    return 0


def show_running(home: Path, port: int) -> bool:
    """Whether this console already runs (its instance file, its server answering with its token): its window is then
    brought to the front, and this launch stops."""
    try:
        inst = json.loads((home / "instance.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{int(inst.get('port') or port)}/api/show",
                                     headers={"X-Console-Token": str(inst.get("token") or "")})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def app_window(url: str, shown: bool, home: Path, own_titlebar: bool = True) -> AppWindow | None:
    """The console's own window (pywebview), or None: the browser then, as before."""
    if os.name != "nt" and sys.platform != "darwin" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None  # no graphical session: a server, an SSH session
    try:
        if desktop_session():
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GLib
            GLib.set_prgname("mmbox-console")  # the window's class, and where WebKit keeps the page's storage
            GLib.set_application_name("mmbox")
        import webview
    except (ImportError, ValueError) as e:
        log(f"no window of its own ({e}): install pywebview (Debian: python3-webview); the browser opens the console")
        return None
    return AppWindow(webview, url, shown, home, own_titlebar)


if __name__ == "__main__":
    main()
