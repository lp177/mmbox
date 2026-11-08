"""The daemon of a computer and its butler (client/BUTLER.md): one per computer, the only one that takes any
project's messages; run by the desktop console, or alone as a service (systemd, launchd, a Windows logon task).

`mmbox.py butler run` watches the project boxes and the machine box. A message nobody took for BUTLER_AFTER_S
(5 min) is acted on: a session of its project is resumed or started (in a detached tmux session, the agent in its
no-prompt mode, its first prompt making it listen), else the butler's own agent takes it and decides (handle it,
or leave it waiting). It keeps the inventory of the agents and models this computer runs (every 6 h, or when
asked), starts sessions on request (its box, the console), and talks with the operator (the console's Butler tab).
Everything it does, and every agent turn's stream, goes to its log, read live by the console.

Sessions are started hidden: a runner process per session (`daemon session-run`, detached, no window on any OS)
keeps the agent's input open (Claude Code in stream-JSON mode: it stays alive, listens, wakes on its background
commands) and writes its output to a file the console shows live; the operator can type into it from there.
Other agents: a detached tmux session (Linux, macOS). What the daemon does on request is only what the operator's
console asks, signed with its operator key (`op` messages to the butler's box).

Needs the full client (a clone: mmbox.py beside this file), Python 3.10+.
"""
from __future__ import annotations

import datetime as dt
import json
import socket
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

M = None  # the client module (mmbox.py), set by main()

BUTLER_PROFILE = os.environ.get("MMBOX_BUTLER_PROFILE", "butler")
AFTER_S = float(os.environ.get("MMBOX_BUTLER_AFTER_S", "300"))  # a message nobody took for this long is the butler's
INVENTORY_EVERY_S = float(os.environ.get("MMBOX_BUTLER_INVENTORY_HOURS", "6")) * 3600
LOOP_S = 15.0
START_WAIT_S = 180.0  # a started session that does not listen within this long has failed
START_EVERY_S = 900.0  # one start per project at most in this long
FAILS_MAX = 3  # a project whose start failed this many times in a row waits for the operator
TURN_TIMEOUT_S = 900.0
LOG_MAX = 5_000_000  # bytes, then butler.log.1 (kept 7 days by its date)
CHAT_KEEP = 400
NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: never a console window on Windows
DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
HOST = socket.gethostname()

# What each agent CLI is, how to find it, how to start or resume it in its no-prompt mode. Models: a known list when
# the CLI has no command listing them (marked so in the inventory).
AGENTS = {
    "claude-code": {"cli": "claude", "provider": "anthropic",
                    "known": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]},
    "codex": {"cli": "codex", "provider": "openai", "known": []},
    "gemini": {"cli": "gemini", "provider": "google", "known": []},
    "vibe": {"cli": "vibe", "provider": "mistral", "known": []},
    "ollama": {"cli": "ollama", "provider": "ollama", "known": []},
}


# ---------------------------------------------------------------- files
def home() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "mmbox" / "daemon"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mmbox" / "daemon"
    if os.environ.get("MMBOX_HOME"):  # a bench, or a second computer on this one: its own daemon folder
        base = Path(os.environ["MMBOX_HOME"]) / ".daemon"
    base.mkdir(parents=True, exist_ok=True)
    return base


def jload(name: str, default):
    try:
        return json.loads((home() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(name: str, value) -> None:
    p = home() / name
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


_LOG_LOCK = threading.Lock()


def log(src: str, text: str | None = None, **kv) -> None:
    """One line of the butler's log (JSON): what the daemon does, or one event of an agent turn's stream."""
    rec = {"t": time.time(), "src": src, **({"text": text} if text else {}), **kv}
    p = home() / "butler.log"
    with _LOG_LOCK:
        try:
            if p.is_file() and p.stat().st_size > LOG_MAX:
                os.replace(p, p.with_name("butler.log.1"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
    if text and src in ("daemon", "error"):
        print(("ERROR: " if src == "error" else "") + text, file=sys.stderr, flush=True)


def config() -> dict:
    """butler.json: roots (where sessions may be started), projects (default agent/model/path per project), the
    butler's own agent and model, permission modes per agent."""
    c = jload("butler.json", {})
    c.setdefault("roots", [str(Path.home() / "Code")])
    c.setdefault("projects", {})
    c.setdefault("permission_mode", {})
    return c


# ---------------------------------------------------------------- inventory
def find_cli(cli: str, agent: str = "") -> str | None:
    if os.environ.get("MMBOX_BUTLER_CLI_" + re.sub(r"\W", "_", agent.upper())):  # tests: a stand-in for the agent's CLI
        return os.environ["MMBOX_BUTLER_CLI_" + re.sub(r"\W", "_", agent.upper())]
    p = shutil.which(cli)
    if p:
        return p
    names = [f"{cli}.exe", f"{cli}.cmd"] if os.name == "nt" else [cli]
    mine = "windows" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux"
    others = {"windows", "darwin", "macos", "linux"} - {mine} - ({"macos"} if mine == "darwin" else set())
    for d in (Path.home() / ".vscode" / "extensions", Path.home() / ".vscode-server" / "extensions",
              Path.home() / ".cursor" / "extensions"):
        if not d.is_dir():
            continue
        # the IDE extensions ship their CLI (Codex, Claude Code), sometimes for several systems (bin/linux-x86_64 in a
        # Windows extension): the build of this system only
        found = [c for n in names for c in list(d.glob(f"*/bin/*/{n}")) + list(d.glob(f"*/bin/{n}"))]
        for c in sorted(found, reverse=True):
            parts = {p.split("-")[0].lower() for p in c.parts[-3:-1]}
            if parts & others and not parts & {mine, "macos" if mine == "darwin" else mine}:
                continue
            if c.is_file() and (os.name == "nt" or os.access(c, os.X_OK)):
                return str(c)
    return None


def argv0(path: str) -> list[str]:
    """The CLI as a command: a path, or (MMBOX_BUTLER_CLI_*, tests) a command line."""
    import shlex
    return shlex.split(path) if " " in path.strip() else [path]


def run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NOWIN)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def probe() -> dict:
    """What this computer can run now: each agent CLI, its version, whether it is logged in, its models."""
    out = []
    for name, a in AGENTS.items():
        path = find_cli(a["cli"], name)
        rec = {"agent": name, "provider": a["provider"], "path": path, "available": bool(path)}
        if not path:
            out.append(rec)
            continue
        code, v = run(argv0(path) + ["--version"])
        rec["version"] = (v.strip().splitlines() or ["?"])[0][:80] if code == 0 else None
        models, source = [], None
        if name == "claude-code":
            models, source = list(a["known"]), "known list for Claude Code"
            st = jload_path(Path.home() / ".claude" / "settings.json")
            if isinstance(st.get("model"), str) and st["model"] not in models:
                models.insert(0, st["model"])
            rec["logged_in"] = (Path.home() / ".claude" / ".credentials.json").is_file() or bool(os.environ.get("ANTHROPIC_API_KEY"))
        elif name == "codex":
            m = re.search(r'^\s*model\s*=\s*"([^"]+)"', read(Path.home() / ".codex" / "config.toml"), re.M)
            models, source = ([m.group(1)] if m else []), "its configuration (~/.codex/config.toml)"
            rec["logged_in"] = (Path.home() / ".codex" / "auth.json").is_file() or bool(os.environ.get("OPENAI_API_KEY"))
        elif name == "ollama":
            code, txt = run(argv0(path) + ["list"])
            models = [ln.split()[0] for ln in txt.splitlines()[1:] if ln.strip()] if code == 0 else []
            source, rec["logged_in"] = "ollama list", True
        elif name == "gemini":
            m = re.search(r'"model"\s*:\s*\{?[^}]*?"name"\s*:\s*"([^"]+)"', read(Path.home() / ".gemini" / "settings.json"))
            models, source = ([m.group(1)] if m else []), "its configuration (~/.gemini/settings.json)"
            rec["logged_in"] = (Path.home() / ".gemini" / "oauth_creds.json").is_file() or bool(os.environ.get("GEMINI_API_KEY"))
        rec.update(models=models, models_source=source,  # Claude Code: hidden anywhere; the others: tmux only
                   can_start=name == "claude-code" or (name in ("codex", "gemini") and os.name != "nt" and bool(shutil.which("tmux"))))
        out.append(rec)
    inv = {"at": time.time(), "host": HOST, "agents": out,
           "tmux": bool(shutil.which("tmux"))}
    jsave("inventory.json", inv)
    publish(inv)
    log("daemon", "inventory: " + ", ".join(f"{r['agent']} {r.get('version') or ''} ({len(r.get('models') or [])} model(s))"
                                            for r in out if r["available"]) + ("" if inv["tmux"] else
                                            "; no tmux: Claude Code sessions only (hidden), not the other agents"))
    return inv


def publish(inv: dict) -> None:
    """The inventory declared with the butler's box: every service's table of active boxes (and so every operator's
    console) shows it, without a message to anyone."""
    b = butler_me(create=False)
    if not b:
        return
    keep = ("agent", "provider", "version", "available", "logged_in", "can_start", "models", "models_source")
    compact = {"at": inv.get("at"), "tmux": inv.get("tmux"),
               "agents": [{k: a.get(k) for k in keep if a.get(k) is not None} for a in inv.get("agents") or []]}
    b.local.update("config.json", lambda c: c.__setitem__("inventory", compact))
    try:
        M.gw_sync(M.load_me(BUTLER_PROFILE), force=True)
    except SystemExit:
        log("error", "inventory not declared to the services (one unreachable?): next probe")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def jload_path(p: Path) -> dict:
    try:
        v = json.loads(read(p) or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def inventory(refresh: bool = False) -> dict:
    inv = jload("inventory.json", {})
    if refresh or not inv or time.time() - float(inv.get("at") or 0) > INVENTORY_EVERY_S:
        inv = probe()
    return inv


def agent_rec(inv: dict, agent: str) -> dict | None:
    return next((r for r in inv.get("agents") or [] if r["agent"] == agent and r.get("available")), None)


def default_agent(inv: dict) -> tuple[str | None, str | None]:
    """The best agent and model this computer runs: Claude Code with the first model of its list, else the first one
    that can start."""
    for r in inv.get("agents") or []:
        if r.get("available") and r.get("can_start") and r.get("logged_in", True):
            return r["agent"], (r.get("models") or [None])[0]
    return None, None


# ---------------------------------------------------------------- the butler's box
def butler_me(create: bool = True):
    me = M.load_me(BUTLER_PROFILE)
    if me is not None:
        return me if me.role == "butler" else None
    if not create:
        return None
    m = M.machine_me()
    if not m:
        M.die("no machine box on this computer: run `mmbox.py start --session <name>` once first")
    import contextlib
    import io
    local = M.Local(BUTLER_PROFILE)
    with contextlib.redirect_stdout(io.StringIO()):
        M.init_identity(local, m.conf.get("project") or "mmbox", "mmbox-butler", f"{m.label}-butler",
                        parent=M.MACHINE_PROFILE, role="butler", user=m.user, gateway=m.conf.get("gateway"))
    local.update("config.json", lambda c: c.update(title="Butler", activity=f"The butler of {m.label}: takes what no "
                 f"session took for {AFTER_S / 60:g} min, starts sessions, knows the agents and models of this computer."))
    me = M.load_me(BUTLER_PROFILE)
    for u in M.gw_urls(m)[1:]:
        if u not in M.gw_urls(me):
            M.join_gateway(me, u, me.conf.get("project") or "mmbox", announce=False)
    M.sync_local_links(m.user)
    log("daemon", f"butler box made: {me.label} ({me.id}), introduced to the machine box's peers")
    return M.load_me(BUTLER_PROFILE)


# ---------------------------------------------------------------- sessions and folders
def local_boxes() -> list:
    return [y for n in M.local_profiles() if (y := M.load_me(n))]


def project_folders() -> dict:
    """Project key -> folder, from the boxes of this computer (where their sessions ran) and butler.json."""
    out = {}
    for y in local_boxes():
        k, d = M.session_key(y), y.conf.get("work_dir")
        if k and d and Path(d).is_dir():
            out.setdefault(k, d)
    for k, p in (config().get("projects") or {}).items():
        if isinstance(p, dict) and p.get("path") and Path(os.path.expanduser(p["path"])).is_dir():
            out[M.project_key(k) or k] = os.path.expanduser(p["path"])
    return out


def allowed_path(path: str) -> Path:
    """A folder a session may be started in: under a configured root, or a known project folder; never any path."""
    p = Path(os.path.expanduser(path)).resolve()
    if not p.is_dir():
        raise ValueError(f"{p}: no such folder")
    known = {Path(d).resolve() for d in project_folders().values()}
    roots = [Path(os.path.expanduser(r)).resolve() for r in config()["roots"]]
    if p in known or any(p == r or r in p.parents for r in roots):
        return p
    raise ValueError(f"{p} is neither under a root of butler.json ({', '.join(map(str, roots))}) nor a known project folder")


def agent_command(agent: str, path: str, model: str | None, prompt: str, resume: str | None = None) -> list[str]:
    mode = (config().get("permission_mode") or {}).get(agent)
    if agent == "claude-code":
        cmd = argv0(path) + (["--resume", resume] if resume else []) + ["--permission-mode", mode or "bypassPermissions"]
        return cmd + (["--model", model] if model else []) + [prompt]
    if agent == "codex":
        cmd = argv0(path) + (["resume", resume] if resume else [])
        return cmd + ([mode] if mode else ["--dangerously-bypass-approvals-and-sandbox"]) + (["-m", model] if model else []) + [prompt]
    if agent == "gemini":
        return argv0(path) + ([mode] if mode else ["--yolo"]) + (["-m", model] if model else []) + ["-i", prompt]
    raise ValueError(f"the butler does not start {agent} sessions")


def first_prompt(name: str, box_id: str, title: str | None, task: str | None, resumed: bool = False,
                 why: str | None = None) -> str:
    head = (f"The mmbox butler of {HOST} {'resumed' if resumed else 'started'} this session"
            + (f" because {why}" if why else "") + ". ")
    return (head + f"First start the encrypted mailbox for it with the skill memories-mailbox-start: session name "
            f"`{name}` (its box exists already, {box_id})" + (f", title \"{title}\"" if title else "")
            + ", then keep its listener running as that skill says, and handle what it hands over (its project's box "
            "included). " + (f"Then: {task}" if task else "Then wait for messages; do nothing else on your own."))


# ---------------------------------------------------------------- hidden sessions: one runner per session
def session_dir(name: str) -> Path:
    d = home() / "sessions" / re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def headless_command(agent: str, path: str, model: str | None, resume: str | None = None) -> list[str]:
    """The agent with its input and output as JSON lines: alive while its input is open (Claude Code)."""
    if agent != "claude-code":
        raise ValueError(f"{agent} has no hidden mode yet")
    mode = (config().get("permission_mode") or {}).get(agent) or "bypassPermissions"
    return argv0(path) + ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                          "--permission-mode", mode] + (["--model", model] if model else []) + (["--resume", resume] if resume else [])


def user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False)


def spawn_runner(name: str, cwd: str, cmd: list[str], prompt: str) -> str:
    """The session's runner, detached from the daemon (it outlives a restart) and without any window."""
    d = session_dir(name)
    for f in ("stop", "in.jsonl"):
        (d / f).unlink(missing_ok=True)
    (d / "in.jsonl").write_text(user_line(prompt) + "\n", encoding="utf-8")
    jsave_path(d / "meta.json", {"name": name, "cmd": cmd, "cwd": cwd, "created": time.time(), "state": "starting"})
    py = sys.executable
    if os.name == "nt" and Path(py).with_name("pythonw.exe").is_file():
        py = str(Path(py).with_name("pythonw.exe"))
    client = str(Path(M.__file__).resolve())
    err = open(d / "runner.err", "a", encoding="utf-8")
    kw = {"creationflags": DETACHED | NOWIN} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([py, client, "daemon", "session-run", name], cwd=cwd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=err, env=clean_env(), **kw)
    return f"hidden:{name}"


def clean_env() -> dict:
    """The environment of what the daemon starts: not that of an agent session the daemon may have been started from."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "MMBOX_SESSION"))}
    env["MMBOX_STARTED_BY"] = "butler"
    return env


def jsave_path(p: Path, value) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def jload_path_any(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def session_run(name: str) -> int:
    """`daemon session-run <name>`: the agent of one session, its input fed from in.jsonl (the first prompt, then what
    the operator types), its output appended to out.jsonl; ends with it, or when `stop` appears."""
    d = session_dir(name)
    meta = jload_path_any(d / "meta.json", {})
    if not meta.get("cmd"):
        return 2
    out = open(d / "out.jsonl", "a", encoding="utf-8")
    lock = threading.Lock()

    def write(rec):
        with lock:
            out.write(json.dumps({"t": time.time(), **rec}, ensure_ascii=False) + "\n")
            out.flush()
    try:
        p = subprocess.Popen(meta["cmd"], cwd=meta.get("cwd") or None, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                             env=clean_env(), creationflags=NOWIN)
    except OSError as e:
        write({"src": "error", "text": f"could not start the agent: {e}"})
        meta.update(state="ended", error=str(e), ended=time.time())
        jsave_path(d / "meta.json", meta)
        return 1
    meta.update(state="running", runner_pid=os.getpid(), agent_pid=p.pid, started=time.time())
    jsave_path(d / "meta.json", meta)
    write({"src": "daemon", "text": f"agent started (pid {p.pid}) in {meta.get('cwd')}"})

    def reader():
        for line in p.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                write({"src": "agent", "event": json.loads(line)})
            except ValueError:
                write({"src": "agent", "text": line[:4000]})
    threading.Thread(target=reader, daemon=True).start()
    pos = 0
    while p.poll() is None:
        if (d / "stop").exists():
            write({"src": "daemon", "text": "stopped on request"})
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            break
        try:
            with open(d / "in.jsonl", "r", encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for ln in chunk.splitlines():
                if ln.strip():
                    p.stdin.write(ln + "\n")
                    p.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        time.sleep(1)
    code = p.wait()
    time.sleep(0.5)
    write({"src": "daemon", "text": f"agent ended (exit {code})"})
    meta.update(state="ended", code=code, ended=time.time())
    jsave_path(d / "meta.json", meta)
    return 0


def session_input(name: str, text: str) -> dict:
    """What the operator types into a hidden session: its next user message."""
    d = session_dir(name)
    if jload_path_any(d / "meta.json", {}).get("state") != "running":
        return {"error": f"{name} is not running"}
    with open(d / "in.jsonl", "a", encoding="utf-8") as f:
        f.write(user_line(text) + "\n")
    with open(d / "out.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "src": "operator", "text": text}, ensure_ascii=False) + "\n")
    return {"ok": True}


def session_output(name: str, since: int = 0, cap: int = 400_000) -> dict:
    """A hidden session's output from byte `since`: {lines, next, meta}."""
    d = session_dir(name)
    p, lines = d / "out.jsonl", []
    try:
        size = p.stat().st_size
    except OSError:
        return {"lines": [], "next": 0, "meta": jload_path_any(d / "meta.json", {})}
    since = since if 0 <= since <= size else 0
    with open(p, "rb") as f:
        f.seek(max(since, size - cap))
        for ln in f.read(cap).decode("utf-8", "replace").splitlines():
            try:
                lines.append(json.loads(ln))
            except ValueError:
                pass
    return {"lines": lines, "next": size, "reset": since == 0, "meta": jload_path_any(d / "meta.json", {})}


def session_tail(name: str, n: int = 12) -> str:
    out = []
    for l in session_output(name, 0, 60_000)["lines"][-60:]:
        e = l.get("event") or {}
        if l.get("text"):
            out.append(l["text"])
        elif e.get("type") == "result":
            out.append("result: " + str(e.get("result") or "")[:300])
        elif e.get("type") == "assistant":
            out += [c.get("text", "")[:300] for c in (e.get("message") or {}).get("content") or [] if c.get("type") == "text"]
    return "\n".join(out[-n:])


def stop_session(s: dict) -> dict:
    t = str(s.get("tmux") or "")
    if t.startswith("hidden:"):
        (session_dir(t[7:]) / "stop").touch()
    else:
        run(["tmux", "kill-session", "-t", t], 10)
    st = jload("state.json", {})
    if s.get("profile") in (st.get("started") or {}):
        st["started"][s["profile"]]["state"] = "stopped"
        jsave("state.json", st)
    return {"stopped": t}


def launch(name: str, cwd: str, agent: str, rec: dict, model: str | None, prompt: str, resume: str | None = None) -> str:
    """Hidden when the agent has a hidden mode (Claude Code), else tmux (not on Windows)."""
    if agent == "claude-code" and (config().get("launcher") or "hidden") != "tmux":
        return spawn_runner(name, cwd, headless_command(agent, rec["path"], model, resume), prompt)
    if os.name == "nt":
        raise RuntimeError(f"{agent} has no hidden mode yet, and tmux does not exist on Windows")
    return tmux_start(name, cwd, agent_command(agent, rec["path"], model, prompt, resume))


def tmux_start(name: str, cwd: str, cmd: list[str]) -> str:
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is not installed on this computer")
    sess = "mmbox-" + re.sub(r"[^A-Za-z0-9_-]+", "-", name)[:60]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "MMBOX_SESSION"))}
    env["MMBOX_STARTED_BY"] = "butler"
    p = subprocess.run(["tmux", "new-session", "-d", "-s", sess, "-x", "200", "-y", "50", "-c", cwd, "--", *cmd],
                       capture_output=True, text=True, env=env, timeout=20)
    if p.returncode != 0:
        raise RuntimeError(f"tmux: {(p.stderr or p.stdout).strip()[:300]}")
    return sess


def tmux_tail(sess: str, lines: int = 40) -> str:
    code, out = run(["tmux", "capture-pane", "-p", "-t", sess, "-S", f"-{lines}"], 10)
    return out if code == 0 else ""


def start_session(path: str, agent: str | None = None, model: str | None = None, title: str | None = None,
                  task: str | None = None, why: str | None = None, project: str | None = None) -> dict:
    """A new session in a folder (or the folder of a project this computer knows): its box made first (its id known
    at once), then the agent started in tmux with a first prompt that makes it listen. {box, profile, tmux, state}"""
    if not path and project:
        path = project_folders().get(M.project_key(project) or "") or ""
        if not path:
            raise ValueError(f"project {project}: no folder known on this computer (start one session there first, or set "
                             f"projects.{M.project_key(project)}.path in butler.json)")
    folder = allowed_path(path)
    inv = inventory()
    if not agent:
        p = (config()["projects"].get(M.project_key(folder.name)) or {})
        agent, model = p.get("agent") or default_agent(inv)[0], model or p.get("model") or default_agent(inv)[1]
    rec = agent_rec(inv, agent or "")
    if not rec or not rec.get("can_start"):
        raise ValueError(f"{agent}: not an agent this computer can start (inventory: "
                         + ", ".join(r["agent"] for r in inv.get("agents") or [] if r.get("can_start") and r.get("available")) + ")")
    m = M.machine_me()
    key = M.project_key(folder.name) or "session"
    name = f"{key}-{agent.split('-')[0]}-{secrets.token_hex(2)}"
    local = M.Local(name)
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        M.init_identity(local, m.conf.get("project") or "mmbox", agent, f"{m.label}-{name}", parent=M.MACHINE_PROFILE,
                        user=m.user, gateway=m.conf.get("gateway"))
    local.update("config.json", lambda c: c.update(work_project=folder.name, work_dir=str(folder), started_by="butler",
                                                   **({"title": M.title_words(title)} if title else {}),
                                                   **({"model": model} if model else {}), provider=rec["provider"]))
    me = M.load_me(name)
    sess = launch(name, str(folder), agent, rec, model, first_prompt(name, me.id, title, task, why=why))
    st = jload("state.json", {})
    st.setdefault("started", {})[name] = {"box": me.id, "profile": name, "tmux": sess, "agent": agent, "model": model,
                                          "path": str(folder), "project": key, "at": time.time(), "state": "starting",
                                          "why": why}
    jsave("state.json", st)
    log("daemon", f"started {agent}{' ' + model if model else ''} in {folder} as box {name} ({me.id}), {sess}"
        + (f": {why}" if why else ""))
    return {"box": me.id, "profile": name, "tmux": sess, "state": "starting", "agent": agent, "model": model,
            "path": str(folder)}


def resume_session(y, why: str) -> dict:
    """A closed or idle session of a project, resumed in tmux by its agent session id, told to listen again."""
    inv = inventory()
    rec = agent_rec(inv, str(y.agent))
    if not rec or not rec.get("can_start") or not y.session_id:
        raise ValueError(f"{y.label} cannot be resumed (agent {y.agent}, session id {y.session_id or 'unknown'})")
    cwd = y.conf.get("work_dir") or str(Path.home())
    sess = launch(y.local.profile, cwd, str(y.agent), rec, y.conf.get("model"),
                  first_prompt(y.local.profile, y.id, None, None, resumed=True, why=why), resume=y.session_id)
    st = jload("state.json", {})
    st.setdefault("started", {})[y.local.profile] = {"box": y.id, "profile": y.local.profile, "tmux": sess, "agent": y.agent,
                                                     "path": cwd, "project": M.session_key(y), "at": time.time(),
                                                     "state": "starting", "resumed": True, "why": why}
    jsave("state.json", st)
    log("daemon", f"resumed {y.label} ({y.agent} session {y.session_id}), {sess}: {why}")
    return {"box": y.id, "profile": y.local.profile, "tmux": sess, "state": "starting", "resumed": True}


def check_started() -> None:
    """The sessions the butler started: listening (done), or failed after START_WAIT_S (the tmux pane's tail kept);
    a hidden one whose agent ended is `ended`."""
    st = jload("state.json", {})
    changed = False
    for name, s in (st.get("started") or {}).items():
        if s.get("state") in ("starting", "listening") and str(s.get("tmux")).startswith("hidden:") \
                and jload_path_any(session_dir(s["tmux"][7:]) / "meta.json", {}).get("state") == "ended":
            s["state"], changed = "ended", True
            continue
        if s.get("state") != "starting":
            continue
        y = M.load_me(name)
        if y and M.session_state(y.local)["listening"]:
            s["state"], changed = "listening", True
            log("daemon", f"{name} listens: started and ready")
        elif time.time() - s["at"] > START_WAIT_S:
            tail = session_tail(s["tmux"][7:]) if str(s.get("tmux")).startswith("hidden:") else tmux_tail(s["tmux"], 15)
            s["state"], s["error"], changed = "failed", (tail.strip()[-800:] or "its session ended"), True
            proj = s.get("project")
            if proj:
                f = st.setdefault("fails", {})
                f[proj] = int(f.get(proj) or 0) + 1
            log("error", f"{name} did not listen within {START_WAIT_S:g} s: start failed", tail=s["error"])
    if changed:
        jsave("state.json", st)


# ---------------------------------------------------------------- agent turns
_TURN_LOCK = threading.Lock()


def butler_agent() -> tuple[str | None, str | None, str | None]:
    """(agent, CLI path, model) the butler thinks with: butler.json `agent`/`model`, else the best available."""
    c, inv = config(), inventory()
    agent = c.get("agent") or default_agent(inv)[0]
    rec = agent_rec(inv, agent or "")
    if not rec or agent != "claude-code":
        return None, None, None  # its turns use Claude Code's print mode and stream (other agents: later)
    return agent, rec["path"], c.get("model") or (rec.get("models") or [None])[0]


def agent_turn(prompt: str, purpose: str, resume: str | None = None, session_id: str | None = None) -> dict:
    """One turn of the butler's agent, headless, its whole stream in the log. {ok, text, session_id}"""
    agent, path, model = butler_agent()
    if not path:
        log("error", f"{purpose}: no agent to think with (Claude Code needed)")
        return {"ok": False, "text": "no agent available for the butler (Claude Code needed)"}
    client = M.__file__ if getattr(M, "__file__", None) else "mmbox.py"
    system = (f"You are the mmbox butler of this computer ({HOST}). The mmbox client is `python3 {client}`; "
              f"your box is the profile `{BUTLER_PROFILE}` (`--profile {BUTLER_PROFILE}`). You may start sessions with "
              f"`python3 {client} butler start --path <folder> [--agent A --model M --title T --task …]`, list them with "
              f"`butler status`, and answer peers with `send … --profile {BUTLER_PROFILE}`. Act only within the rights "
              f"the messages carry. Be brief; say what you did.")
    cmd = argv0(path) + ["-p", "--output-format", "stream-json", "--verbose", "--permission-mode",
           (config().get("permission_mode") or {}).get("claude-code") or "bypassPermissions",
           "--append-system-prompt", system] + (["--model", model] if model else [])
    cmd += ["--resume", resume] if resume else (["--session-id", session_id] if session_id else [])
    cmd.append(prompt)
    turn = secrets.token_hex(4)
    log("turn", f"{purpose} (turn {turn}, {model or 'default model'})", turn=turn, phase="start")
    text, sid, ok = "", None, False
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE"))}
    with _TURN_LOCK:
        try:
            p = subprocess.Popen(cmd, cwd=str(home()), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=env, creationflags=NOWIN)
            t0 = time.time()
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    log("agent", line[:4000], turn=turn)
                    continue
                log("agent", None, turn=turn, event=ev)
                if ev.get("type") == "result":
                    text, sid, ok = str(ev.get("result") or ""), ev.get("session_id") or sid, not ev.get("is_error")
                elif ev.get("session_id"):
                    sid = ev["session_id"]
                if time.time() - t0 > TURN_TIMEOUT_S:
                    p.kill()
                    log("error", f"turn {turn} cut after {TURN_TIMEOUT_S:g} s", turn=turn)
                    break
            p.wait(30)
        except (OSError, subprocess.SubprocessError) as e:
            log("error", f"turn {turn}: {e}", turn=turn)
    log("turn", f"turn {turn} {'done' if ok else 'failed'}", turn=turn, phase="end", ok=ok, result=text[:4000])
    return {"ok": ok, "text": text, "session_id": sid}


def context_text() -> str:
    """What the butler's agent knows of this computer at each turn: its boxes, what waits, the inventory."""
    lines = ["Boxes of this computer:"]
    for y in local_boxes():
        s = M.session_state(y.local)
        lines.append(f"- {y.local.profile} ({y.role}, {y.label}, {y.id}) project {M.session_key(y) or '-'}"
                     + (" CLOSED" if y.closed_at else " listening" if s["listening"] else " agent alive" if s["session_alive"] else " idle")
                     + (f", agent {y.agent} session {y.session_id}" if y.session_id else ""))
    inv = inventory()
    lines.append("Agents: " + "; ".join(f"{r['agent']} ({', '.join(r.get('models') or []) or 'models unknown'})"
                                        for r in inv.get("agents") or [] if r.get("available")))
    lines.append("Project folders: " + ", ".join(f"{k}: {v}" for k, v in project_folders().items()))
    return "\n".join(lines)


# ---------------------------------------------------------------- chat with the operator
def chat_add(role: str, text: str, **kv) -> dict:
    rec = {"t": time.time(), "role": role, "text": text, **kv}
    with _LOG_LOCK:
        with open(home() / "chat.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def chat_history(n: int = 200) -> list[dict]:
    try:
        lines = (home() / "chat.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def chat_turn(text: str) -> None:
    st = jload("state.json", {})
    sid = st.get("chat_session")
    r = agent_turn(f"The operator writes to you from the console:\n\n{text}\n\n(Context now)\n{context_text()}",
                   "chat with the operator", resume=sid if sid else None, session_id=None if sid else None)
    if r.get("session_id") and r["session_id"] != sid:
        st = jload("state.json", {})
        st["chat_session"] = r["session_id"]
        jsave("state.json", st)
    chat_add("butler", r.get("text") or "(no answer: see the live output)", ok=r.get("ok"))


# ---------------------------------------------------------------- the daemon
def waiting_for_butler() -> list[tuple]:
    """(box, project key or None, waiting entries) for the project boxes and the machine box: what nobody took for
    AFTER_S."""
    out = []
    m = M.machine_me()
    if not m:
        return out
    store = M.Store(m.local)
    for y in local_boxes():
        if not (y.role == "machine" or (y.role == "project" and not y.closed_at)):
            continue
        try:
            _, waiting = M.untaken(y, store, y.conf.get("project") or "", AFTER_S, forwards=True)
        except SystemExit:
            continue
        waiting = [w for w in waiting if w["age"] <= M.EXPIRE_S]  # older: the backlog, which the user decides on
        if waiting:
            out.append((y, M.session_key(y) if y.role == "project" else None, waiting))
    return out


def project_sessions(key: str) -> list:
    return [y for y in local_boxes() if y.role == "session" and M.session_key(y) == key]


def sort_machine_box(y, waiting, st) -> list:
    """The machine box's waiting messages meant for a project (send --for, else the sender's project) go to that
    project's box first, where a session of it is resumed or started; what is meant for the computer stays."""
    by_entry = {}
    for gw in M.reading_gateways(y):
        for e in (gw.list(y, soft=True).get("entries") or []):
            by_entry[(gw.url, e.get("entry"))] = e.get("env") or {}
    keep, groups = [], {}
    for w in waiting:
        try:
            p = M.open_envelope(y, w["peer"], by_entry.get((w.get("gw"), w.get("entry"))) or {})
        except (ValueError, KeyError, TypeError):
            keep.append(w)
            continue
        k = M.message_project(p) if p.get("kind") != "forward" else (p.get("forwarded") or {}).get("project")
        (groups.setdefault(k, []) if k else keep).append(w)
    store, ypeers = M.Store(y.local), M.peers_load(y.local)
    for k, ws in groups.items():
        pb = M.ensure_project_box(y, k, y.conf.get("project") or "")
        ypeers = ypeers if pb and pb.id in ypeers else M.peers_load(y.local)
        if not pb or pb.id not in ypeers:
            keep += ws
            continue
        done = M.forward_to_machine(y, pb, ypeers[pb.id], y, ypeers, ws, f"meant for project {k}, which no session took "
                                    f"for {AFTER_S / 60:g} min", f"meant for project {k}: the butler puts it in its box",
                                    store, y.conf.get("project") or "", lambda t: log("daemon", t), f"the box of project {k}")
        if done:
            log("daemon", f"{len(done)} message(s) of the machine box meant for project {k}: put in its box")
    return keep


def act_on(y, key, waiting, st) -> None:
    """One box with messages nobody took: wake, resume or start a session of its project, else take them."""
    claims = st.setdefault("claims", {})
    if y.role == "machine":
        waiting = sort_machine_box(y, [w for w in waiting if w["msg_id"] not in claims], st)
    ids = [w["msg_id"] for w in waiting]
    fresh = [i for i in ids if i not in claims]
    if not fresh:
        return
    if key:
        live = [s for s in project_sessions(key) if not s.closed_at and M.session_state(s.local)["listening"]]
        starting = [s for s in (st.get("started") or {}).values() if s.get("project") == key and s.get("state") == "starting"]
        if live or starting:
            return  # a session of the project listens, or is starting: it takes them
        recent = float((st.get("last_start") or {}).get(key) or 0)
        fails = int((st.get("fails") or {}).get(key) or 0)
        why = f"{len(fresh)} message(s) for project {key} waited {AFTER_S / 60:g} min for a session"
        if time.time() - recent > START_EVERY_S and fails < FAILS_MAX:
            res = None
            cands = sorted([s for s in project_sessions(key) if s.session_id and agent_rec(inventory(), str(s.agent))],
                           key=lambda s: M.last_active(s), reverse=True)
            try:
                if cands and time.time() - M.last_active(cands[0]) < 7 * 86400:
                    res = resume_session(cands[0], why)
                else:
                    folder = project_folders().get(key)
                    if folder:
                        res = start_session(folder, why=why)
            except (ValueError, RuntimeError, SystemExit) as e:
                log("error", f"project {key}: could not start a session: {e}")
                st.setdefault("fails", {})[key] = fails + 1
            if res:
                st.setdefault("last_start", {})[key] = time.time()
                for i in fresh:
                    claims[i] = {"at": time.time(), "box": y.id, "action": "session", "session": res["profile"]}
                return
    # nobody to start (or the machine box): the butler takes them and its agent decides, but only what a sender with
    # `execute` rights sent: its agent acts without asking; the rest stays where it is, for the operator
    b = butler_me()
    store = M.Store(y.local)
    ypeers = M.peers_load(y.local)
    rights = rights_by_msg(y, ypeers, [w for w in waiting if w["msg_id"] in fresh])
    for w in waiting:
        if w["msg_id"] in fresh and rights.get(w["msg_id"]) != "execute":
            claims[w["msg_id"]] = {"at": time.time(), "box": y.id, "action": "left"}
            ask_operator(f"{w['msg_id']} waits in {y.label} (from {w['peer'].get('alias')}, rights "
                         f"{rights.get(w['msg_id']) or 'unknown'}): no session took it, and I act only on what a sender "
                         f"with `execute` rights sent. It stays there; tell me what to do, or start a session for it.")
    fresh = [i for i in fresh if rights.get(i) == "execute"]
    if not fresh:
        return
    bpeer = ypeers.get(b.id)
    if not bpeer:
        M.sync_local_links(b.user)
        log("daemon", f"{y.label}: the butler box is not its peer yet; next round")
        return
    mine = [w for w in waiting if w["msg_id"] in fresh]
    done = M.forward_to_machine(y, b, bpeer, y, ypeers, mine, f"which no session took for {AFTER_S / 60:g} min",
                                f"no session took it for {AFTER_S / 60:g} min; the butler takes it", store,
                                y.conf.get("project") or "", lambda t: log("daemon", t), "the butler")
    for i, *_ in done:
        claims[i] = {"at": time.time(), "box": y.id, "action": "taken"}
    if done:
        log("daemon", f"took {len(done)} message(s) of {y.label}: its agent decides")


def rights_by_msg(y, ypeers, waiting) -> dict:
    """msg id -> the rights its sender has here: the original sender's for what was put in a project box."""
    envs = {}
    for gw in M.reading_gateways(y):
        for e in (gw.list(y, soft=True).get("entries") or []):
            envs[(gw.url, e.get("entry"))] = e.get("env") or {}
    out = {}
    for w in waiting:
        r = M.rights_of(ypeers, w["peer"]) or "ask"
        try:
            p = M.open_envelope(y, w["peer"], envs.get((w.get("gw"), w.get("entry"))) or {})
            if p.get("kind") == "forward":
                r = ((p.get("forwarded") or {}).get("original_sender") or {}).get("rights") or "ask"
        except (ValueError, KeyError, TypeError):
            r = None
        out[w["msg_id"]] = r
    return out


def ask_operator(text: str) -> None:
    """A word to the operator in the Butler chat (the console shows it), and in the log."""
    chat_add("butler", text, notice=True)
    log("daemon", "to the operator: " + text)


def handle_own_box(b, st) -> None:
    """What reached the butler's box: requests (start a session, inventory, chat), or what it took: its agent decides."""
    import contextlib
    import io
    items = [i for i in M.fetch_new(b, b.conf.get("project") or "", announce=b.local) if i["status"] not in M.SILENT]
    if not items:
        return
    for it in items:
        p = it.get("payload") or {}
        if it["status"] == "op":  # the operator's console (its pinned key, or a device of a pinned account), nobody else
            via = it.get("via")
            why = account_check(it["peer_id"], p)
            reply = {"error": f"refused: {why}"} if why else do_op(p.get("op") if isinstance(p.get("op"), dict) else {})
            opeer = (M.operator_peer(via, it["peer_id"]) or M.account_peer(it["peer_id"])) if via else None
            if opeer:
                opeer = {**opeer, "gateway": via, "gateways": [via]}
                M.send_payload(b, it["peer_id"], opeer, b.conf.get("project") or "", f"Re: {p.get('subject', 'op')}",
                               json.dumps(reply, ensure_ascii=False)[:200_000], "op_result", it["id"], False,
                               extra={"op_result": reply})
            log("daemon", f"operator op {(p.get('op') or {}).get('op')}: {json.dumps(reply, ensure_ascii=False)[:300]}")
            continue
        rights = it.get("orig_rights") or it["status"]
        if rights != "execute":  # never acted on by the butler's agent (it acts without asking): the operator decides
            if rights != "archived":
                ask_operator(f"{it['peer']} wrote to me (rights {rights}): \"{p.get('subject', '')}\" ({it['id']}). I did "
                             f"nothing: tell me whether to act on it.\n\n{str(p.get('body') or '')[:1500]}")
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            M.print_items([it], False)
        agent_turn(f"This reached your box (the butler's), as the mmbox listener prints it:\n\n{buf.getvalue()}\n\n"
                   f"Decide: if a session of its project can do it, start one (`butler start`) and tell the sender; if "
                   f"you can do it yourself within the rights shown, do it and answer the sender (`send --re`); if "
                   f"neither, answer the sender that it waits, and say so here. Never drop it silently.\n\n"
                   f"(Context now)\n{context_text()}", f"message {it['id']} from {it['peer']}")


def account_check(sender: str, p: dict) -> str | None:
    """For an op sent by a device of a pinned account: the chain it carries updates this computer's cache (only forward:
    never a fork, never going back), then the sender must be a device of that fresh chain, not revoked. None: allowed.
    An op from the pinned operator key (no account) passes as before."""
    A = M.account_module()
    pins = M.account_pins()
    acct = None
    for fp, pin in pins.items() if A else []:
        try:
            if sender in A.verify(pin.get("chain") or [], fp, max_age=None)["devices"]:
                acct = fp
                break
        except A.ChainError:
            continue
    if acct is None:
        return None  # the pinned operator key itself (the client accepted it as such), not an account's device
    chain = p.get("chain") if isinstance(p.get("chain"), list) else None
    if chain:
        try:
            keep = A.newer(pins[acct].get("chain"), chain, acct)
        except A.ChainError as e:
            return f"the chain it carries does not hold: {e}"
        if keep is None:
            log("error", f"a chain that forks from this computer's was offered by {sender}: refused")
            return "its chain forks from the one this computer knows"
        old = pins[acct].get("chain") or []
        if len(keep) != len(old) or not A.extends(old, keep):
            pins[acct] = {"chain": keep, "at": time.time()}
            M.account_pins_save(pins)
            log("daemon", f"account {acct}: chain now {len(keep)} link(s)")
    try:
        st = A.verify(pins[acct]["chain"], acct)
    except A.ChainError as e:
        return str(e)
    if sender not in st["devices"]:
        return f"{sender} is revoked from account {st['account']}"
    return None


def do_op(op: dict) -> dict:
    """What the operator's console asks the daemon (signed with its operator key, nobody else): status, config,
    inventory, start, stop, input (a line typed into a hidden session), output (a hidden session's output)."""
    try:
        o = op.get("op")
        if o == "status":
            st = status()
            return {k: st[k] for k in ("running", "box", "beat", "started", "fails", "folders", "after_s")} | {
                "inventory": st["inventory"], "config": {k: st["config"].get(k) for k in ("agent", "model", "roots", "launcher")}}
        if o == "config":
            c = jload("butler.json", {})
            for k in ("agent", "model", "launcher"):
                if k in op:
                    v = str(op.get(k) or "").strip()[:80]
                    c.pop(k, None) if not v else c.__setitem__(k, v)
            jsave("butler.json", c)
            return {"config": {k: c.get(k) for k in ("agent", "model", "launcher")}}
        if o == "input":
            return session_input(str(op.get("profile") or ""), str(op.get("text") or "")[:20000])
        if o == "output":
            return session_output(str(op.get("profile") or ""), int(op.get("since") or 0), 60_000)
        if o == "start":
            return start_session(str(op.get("path") or ""), op.get("agent"), op.get("model"), op.get("title"), op.get("task"),
                                 why=op.get("why") or "asked", project=op.get("project"))
        if o == "inventory":
            return inventory(refresh=bool(op.get("refresh")))
        if o == "list":
            return {"started": (jload("state.json", {}).get("started") or {})}
        if o == "stop":
            s = (jload("state.json", {}).get("started") or {}).get(str(op.get("profile") or ""))
            if not s:
                return {"error": "not a session the butler started"}
            return stop_session(s)
        return {"error": f"unknown op {o}"}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}


def daemon(once: bool = False) -> int:
    lock = open(home() / "daemon.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("ALREADY RUNNING: the daemon runs on this computer already", flush=True)
        return 6
    (home() / "butler.pid").write_text(str(os.getpid()), encoding="utf-8")
    b = butler_me()
    M.listeners(b.local, register=True)
    log("daemon", f"butler {b.label} ({b.id}) runs, pid {os.getpid()}; takes what waited {AFTER_S / 60:g} min")
    inventory()
    while True:
        try:
            if (home() / "refresh").exists():
                (home() / "refresh").unlink(missing_ok=True)
                inventory(refresh=True)
            else:
                inventory()
            st = jload("state.json", {})
            for y, key, waiting in waiting_for_butler():
                act_on(y, key, waiting, st)
            # claims of messages no longer waiting are dropped; those older than a day too
            st["claims"] = {k: v for k, v in (st.get("claims") or {}).items() if time.time() - v.get("at", 0) < 86400}
            jsave("state.json", {**jload("state.json", {}), "claims": st["claims"], "fails": st.get("fails") or {},
                                 "last_start": st.get("last_start") or {}, "beat": time.time()})
            check_started()
            handle_own_box(b, st)
            q = home() / "chat.queue"
            if q.exists():
                texts = q.read_text(encoding="utf-8").split("\x1e")
                q.unlink(missing_ok=True)
                for t in texts:
                    if t.strip():
                        chat_turn(t.strip())
        except Exception as e:  # noqa: BLE001 - the daemon goes on, the error is in its log
            log("error", f"round failed: {type(e).__name__}: {e}")
        if once:
            return 0
        time.sleep(LOOP_S)


def status() -> dict:
    st = jload("state.json", {})
    pid = read(home() / "butler.pid").strip()
    alive = pid.isdigit() and M.pid_alive(int(pid))  # never os.kill(pid, 0): on Windows it terminates the process
    b = butler_me(create=False)
    running = alive and time.time() - float(st.get("beat") or 0) < LOOP_S * 8
    return {"running": running, "pid": int(pid) if running else None,
            "box": {"id": b.id, "label": b.label} if b else None, "beat": st.get("beat"), "home": str(home()),
            "inventory": jload("inventory.json", {}), "started": st.get("started") or {}, "claims": st.get("claims") or {},
            "fails": st.get("fails") or {}, "config": config(), "folders": project_folders(),
            "after_s": AFTER_S, "inventory_every_s": INVENTORY_EVERY_S}


def install_service(start: bool = True) -> list[str]:
    """The daemon as a service of this user, started at login and now: systemd (Linux), launchd (macOS), a logon task
    running pythonw (Windows: no window). Returns what was done."""
    client = str(Path(M.__file__).resolve())
    done = []
    if sys.platform.startswith("linux"):
        unit = Path.home() / ".config" / "systemd" / "user" / "mmbox-daemon.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(f"""[Unit]
Description=mmbox daemon (its butler takes what no session took, starts sessions)
After=network-online.target

[Service]
ExecStart={sys.executable} {client} daemon run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
""", encoding="utf-8")
        done.append(f"written {unit}")
        if start:
            for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "mmbox-daemon.service"]):
                code, out = run(cmd, 60)
                done.append(f"{' '.join(cmd)}: {'ok' if code == 0 else out.strip()[:200]}")
            done.append("on a server, also `loginctl enable-linger $USER` (it then runs without a login)")
    elif sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.mmbox.daemon.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mmbox.daemon</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{client}</string><string>daemon</string><string>run</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>{home() / "daemon.err"}</string>
</dict></plist>
""", encoding="utf-8")
        done.append(f"written {plist}")
        if start:
            run(["launchctl", "unload", str(plist)], 30)
            code, out = run(["launchctl", "load", "-w", str(plist)], 30)
            done.append("launchctl load: " + ("ok" if code == 0 else out.strip()[:200]))
    elif os.name == "nt":
        # a shortcut in this user's Startup folder: started at each logon, without any administrator right (a logon
        # task, schtasks /SC ONLOGON, needs an elevated prompt), pythonw: no window
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.is_file() else sys.executable)
        link = startup_link()
        q = lambda v: "'" + str(v).replace("'", "''") + "'"  # noqa: E731
        code, out = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                         f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(link)}); $s.TargetPath={q(exe)};"
                         f"$s.Arguments={q(chr(34) + client + chr(34) + ' daemon run')}; $s.WorkingDirectory={q(home())};"
                         f"$s.WindowStyle=7; $s.Description='mmbox daemon'; $s.Save()"], 60)
        if code != 0 or not link.is_file():
            raise RuntimeError(f"the Startup shortcut could not be made: {out.strip()[:300]}")
        done.append(f"written {link} (started at each logon, " + ("pythonw: no window)" if pyw.is_file() else "python)"))
        if start and not status()["running"]:
            subprocess.Popen([exe, client, "daemon", "run"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=DETACHED | NOWIN)
            done.append("started now")
    else:
        done.append(f"{sys.platform}: no service installer; run `mmbox.py daemon run` at login yourself")
    return done


def operator_pins(fingerprint: str | None = None, ask: bool = False) -> tuple[bool, list[str]]:
    """The operator key the daemon obeys, pinned on each service this computer's machine box is on (an `op` is accepted
    from that key only). With `fingerprint` (or asked, on a terminal), the services without a pin get it (`collector
    enable --level off`: questions and ops, no annotation); an existing pin is never replaced here. (all pinned, lines)"""
    m = M.machine_me()
    if not m:
        return False, ["no machine box on this computer: run `mmbox.py start --session <name>` once first"]
    urls, lines = M.gw_urls(m), []
    missing = [u for u in urls if not M.operator_pin(u)]
    for u in urls:
        if (c := M.operator_pin(u)) is not None:
            lines.append(f"{u}: operator {c.get('id')} pinned (fingerprint {c.get('fingerprint')})"
                         + (f" - NOT the key {fingerprint} given: kept; `mmbox.py collector enable {u} --fingerprint "
                            f"{fingerprint}` replaces it" if fingerprint and fingerprint != c.get("fingerprint") else ""))
    if missing and not fingerprint and ask and sys.stdin.isatty():
        fingerprint = input(f"The fingerprint of your operator key, as your console shows it (`mmbox_console.py --has-key`), "
                            f"for {', '.join(missing)} (empty: later): ").strip() or None
    for u in missing:
        if not fingerprint:
            lines.append(f"{u}: no operator key pinned: the daemon refuses every op there until one is "
                         f"(`mmbox.py daemon operator --fingerprint <it>`)")
            continue
        code, out = run([sys.executable, str(Path(M.__file__).resolve()), "collector", "enable", u, "--level", "off",
                         "--fingerprint", fingerprint], 60)
        lines.append(f"{u}: " + ("operator key pinned" if code == 0 and M.operator_pin(u) else f"not pinned: {out.strip()[-300:]}"))
    return all(M.operator_pin(u) for u in urls), lines


def startup_link() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup" / "mmbox daemon.lnk"


def uninstall_service() -> list[str]:
    done = []
    if sys.platform.startswith("linux"):
        run(["systemctl", "--user", "disable", "--now", "mmbox-daemon.service"], 60)
        (Path.home() / ".config" / "systemd" / "user" / "mmbox-daemon.service").unlink(missing_ok=True)
        done.append("systemd user service removed")
    elif sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.mmbox.daemon.plist"
        run(["launchctl", "unload", str(plist)], 30)
        plist.unlink(missing_ok=True)
        done.append("launchd agent removed")
    elif os.name == "nt":
        link = startup_link()
        if link.is_file():
            link.unlink()
            done.append(f"removed {link}")
        else:
            done.append("no Startup shortcut")
        if (pid := status().get("pid")):
            run(["taskkill", "/PID", str(pid), "/F"], 30)
            done.append(f"daemon pid {pid} stopped")
    return done


def main(module, argv: list[str]) -> int:
    global M
    M = module
    import argparse
    ap = argparse.ArgumentParser(prog="mmbox.py daemon", description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("run", help="the butler's daemon (one per computer)")
    x.add_argument("--once", action="store_true", help="one round, then exit")
    sp.add_parser("status", help="running or not, inventory, started sessions, claims (JSON)")
    x = sp.add_parser("inventory", help="the agents and models of this computer (JSON)")
    x.add_argument("--refresh", action="store_true", help="probe now (else the last one, at most 6 h old)")
    x = sp.add_parser("start", help="start a session in a folder (its box first, then the agent in tmux)")
    x.add_argument("--path", default="")
    x.add_argument("--project", help="instead of --path: a project whose folder this computer knows")
    x.add_argument("--agent")
    x.add_argument("--model")
    x.add_argument("--title")
    x.add_argument("--task")
    x = sp.add_parser("stop", help="end a session the butler started (its tmux session)")
    x.add_argument("profile")
    x = sp.add_parser("chat", help="a message to the butler's agent; its answer goes to the chat (console)")
    x.add_argument("text")
    x = sp.add_parser("log", help="the butler's log (JSON lines)")
    x.add_argument("-n", type=int, default=50)
    x = sp.add_parser("install", help="run the daemon as a service of this user: systemd, launchd, a Windows Startup shortcut")
    x.add_argument("--systemd", action="store_true", help=argparse.SUPPRESS)  # the old spelling
    x.add_argument("--no-start", action="store_true", help="install it, start it at the next login only")
    x.add_argument("--operator-fingerprint", help="the operator key it obeys, pinned where none is (asked otherwise)")
    sp.add_parser("uninstall", help="remove that service")
    x = sp.add_parser("operator", help="the operator key the daemon obeys: pinned on each service of this computer")
    x.add_argument("--fingerprint", help="pin this operator key where none is pinned (asked on a terminal otherwise)")
    x.add_argument("--operator-fingerprint", dest="fingerprint", help=argparse.SUPPRESS)
    x.add_argument("--account", help="pin this account's root fingerprint: its devices may then send ops (client/ACCOUNTS.md)")
    x.add_argument("--chain", help="with --account: its device chain (a JSON file the console exports), checked against it")
    x = sp.add_parser("session-run", help=argparse.SUPPRESS)  # a hidden session's runner (started by the daemon)
    x.add_argument("name")
    x = sp.add_parser("session-input", help="type a line into a hidden session the daemon started")
    x.add_argument("profile")
    x.add_argument("text")
    x = sp.add_parser("session-output", help="a hidden session's output (JSON lines)")
    x.add_argument("profile")
    x.add_argument("-n", type=int, default=40)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return daemon(a.once)
    if a.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
    elif a.cmd == "inventory":
        print(json.dumps(inventory(a.refresh), ensure_ascii=False, indent=1))
    elif a.cmd == "start":
        try:
            print(json.dumps(start_session(a.path, a.agent, a.model, a.title, a.task, why="asked", project=a.project),
                             ensure_ascii=False))
        except (ValueError, RuntimeError) as e:
            print(json.dumps({"error": str(e)}))
            return 1
    elif a.cmd == "stop":
        print(json.dumps(do_op({"op": "stop", "profile": a.profile})))
    elif a.cmd == "chat":
        chat_add("operator", a.text)
        with open(home() / "chat.queue", "a", encoding="utf-8") as f:
            f.write(a.text + "\x1e")
        print("queued: the running butler answers in its chat")
    elif a.cmd == "log":
        print("\n".join(read(home() / "butler.log").splitlines()[-a.n:]))
    elif a.cmd == "install":
        ok, lines = operator_pins(a.operator_fingerprint, ask=True)
        print("\n".join(lines))
        try:
            print("\n".join(install_service(start=not a.no_start)))
        except (RuntimeError, OSError) as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        if not ok:
            print("WARNING: the daemon runs, but obeys no operator on the services listed above until its key is pinned")
    elif a.cmd == "operator" and a.account:
        A = M.account_module()
        pins = M.account_pins()
        chain = json.loads(Path(a.chain).read_text(encoding="utf-8")) if a.chain else (pins.get(a.account) or {}).get("chain") or []
        if chain:
            try:
                st = A.verify(chain, a.account, max_age=None)
            except A.ChainError as e:
                print(f"not pinned: {e}", file=sys.stderr)
                return 1
            print(f"account {st['account']} (root {a.account}): {len(st['devices'])} device(s): "
                  + ", ".join(f"{d.get('label')} ({i})" for i, d in st["devices"].items()))
        pins[a.account] = {"chain": chain, "at": time.time()}
        M.account_pins_save(pins)
        print(f"pinned: the devices of account {a.account} may send ops to this computer's daemon"
              + ("" if chain else " (its chain comes with their first op)"))
    elif a.cmd == "operator":
        ok, lines = operator_pins(a.fingerprint, ask=True)
        print("\n".join(lines))
        return 0 if ok else 1
    elif a.cmd == "uninstall":
        print("\n".join(uninstall_service()))
    elif a.cmd == "session-run":
        return session_run(a.name)
    elif a.cmd == "session-input":
        print(json.dumps(session_input(a.profile, a.text)))
    elif a.cmd == "session-output":
        for l in session_output(a.profile)["lines"][-a.n:]:
            print(json.dumps(l, ensure_ascii=False))
    return 0
