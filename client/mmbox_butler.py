"""The butler of a computer (client/BUTLER.md): one per computer, the only one that takes any project's messages.

`mmbox.py butler run` watches the project boxes and the machine box. A message nobody took for BUTLER_AFTER_S
(5 min) is acted on: a session of its project is resumed or started (in a detached tmux session, the agent in its
no-prompt mode, its first prompt making it listen), else the butler's own agent takes it and decides (handle it,
or leave it waiting). It keeps the inventory of the agents and models this computer runs (every 6 h, or when
asked), starts sessions on request (its box, the console), and talks with the operator (the console's Butler tab).
Everything it does, and every agent turn's stream, goes to its log, read live by the console.

Needs the full client (a clone: mmbox.py beside this file), Python 3.10+, tmux to start sessions.
"""
from __future__ import annotations

import datetime as dt
import json
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
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mmbox" / "butler"
    if os.environ.get("MMBOX_HOME"):  # a bench, or a second computer on this one: its own butler folder
        base = Path(os.environ["MMBOX_HOME"]) / ".butler"
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
    for d in (Path.home() / ".vscode" / "extensions", Path.home() / ".vscode-server" / "extensions",
              Path.home() / ".cursor" / "extensions"):
        if d.is_dir():  # the IDE extensions ship their CLI (Codex, Claude Code)
            for c in sorted(d.glob(f"*/bin/*/{cli}"), reverse=True) + sorted(d.glob(f"*/bin/{cli}"), reverse=True):
                if c.is_file() and os.access(c, os.X_OK):
                    return str(c)
    return None


def argv0(path: str) -> list[str]:
    """The CLI as a command: a path, or (MMBOX_BUTLER_CLI_*, tests) a command line."""
    import shlex
    return shlex.split(path) if " " in path.strip() else [path]


def run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
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
        rec.update(models=models, models_source=source, can_start=name in ("claude-code", "codex", "gemini"))
        out.append(rec)
    inv = {"at": time.time(), "host": os.uname().nodename if hasattr(os, "uname") else "", "agents": out,
           "tmux": bool(shutil.which("tmux"))}
    jsave("inventory.json", inv)
    publish(inv)
    log("daemon", "inventory: " + ", ".join(f"{r['agent']} {r.get('version') or ''} ({len(r.get('models') or [])} model(s))"
                                            for r in out if r["available"]) + ("" if inv["tmux"] else "; no tmux: no session start"))
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
    host = os.uname().nodename if hasattr(os, "uname") else "this computer"
    head = (f"The mmbox butler of {host} {'resumed' if resumed else 'started'} this session"
            + (f" because {why}" if why else "") + ". ")
    return (head + f"First start the encrypted mailbox for it with the skill memories-mailbox-start: session name "
            f"`{name}` (its box exists already, {box_id})" + (f", title \"{title}\"" if title else "")
            + ", then keep its listener running as that skill says, and handle what it hands over (its project's box "
            "included). " + (f"Then: {task}" if task else "Then wait for messages; do nothing else on your own."))


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
    cmd = agent_command(agent, rec["path"], model, first_prompt(name, me.id, title, task, why=why))
    sess = tmux_start(name, str(folder), cmd)
    st = jload("state.json", {})
    st.setdefault("started", {})[name] = {"box": me.id, "profile": name, "tmux": sess, "agent": agent, "model": model,
                                          "path": str(folder), "project": key, "at": time.time(), "state": "starting",
                                          "why": why}
    jsave("state.json", st)
    log("daemon", f"started {agent}{' ' + model if model else ''} in {folder} as box {name} ({me.id}), tmux {sess}"
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
    cmd = agent_command(str(y.agent), rec["path"], y.conf.get("model"),
                        first_prompt(y.local.profile, y.id, None, None, resumed=True, why=why), resume=y.session_id)
    sess = tmux_start(y.local.profile + "-r" + secrets.token_hex(1), cwd, cmd)
    st = jload("state.json", {})
    st.setdefault("started", {})[y.local.profile] = {"box": y.id, "profile": y.local.profile, "tmux": sess, "agent": y.agent,
                                                     "path": cwd, "project": M.session_key(y), "at": time.time(),
                                                     "state": "starting", "resumed": True, "why": why}
    jsave("state.json", st)
    log("daemon", f"resumed {y.label} ({y.agent} session {y.session_id}) in tmux {sess}: {why}")
    return {"box": y.id, "profile": y.local.profile, "tmux": sess, "state": "starting", "resumed": True}


def check_started() -> None:
    """The sessions the butler started: listening (done), or failed after START_WAIT_S (the tmux pane's tail kept)."""
    st = jload("state.json", {})
    changed = False
    for name, s in (st.get("started") or {}).items():
        if s.get("state") != "starting":
            continue
        y = M.load_me(name)
        if y and M.session_state(y.local)["listening"]:
            s["state"], changed = "listening", True
            log("daemon", f"{name} listens: started and ready")
        elif time.time() - s["at"] > START_WAIT_S:
            s["state"], s["error"], changed = "failed", (tmux_tail(s["tmux"], 15).strip()[-800:] or "its tmux session ended"), True
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
    system = (f"You are the mmbox butler of this computer ({os.uname().nodename}). The mmbox client is `python3 {client}`; "
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
                                 stdin=subprocess.DEVNULL, env=env)
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
        op = None
        try:
            body = json.loads(p.get("body") or "")
            op = body if isinstance(body, dict) and body.get("op") else None
        except ValueError:
            pass
        rights = it.get("orig_rights") or it["status"]
        if rights != "execute":  # never acted on by the butler's agent (it acts without asking): the operator decides
            if rights != "archived":
                ask_operator(f"{it['peer']} wrote to me (rights {rights}): \"{p.get('subject', '')}\" ({it['id']}). I did "
                             f"nothing: tell me whether to act on it.\n\n{str(p.get('body') or '')[:1500]}")
            continue
        if op:
            reply = do_op(op)
            if not op.get("quiet"):  # `quiet`: no answer (a console asking for a refresh reads the table instead)
                M.send_payload(b, it["peer_id"], M.peers_load(b.local)[it["peer_id"]], b.conf.get("project") or "",
                               f"Re: {p.get('subject', '')}", json.dumps(reply, ensure_ascii=False), "reply", it["id"], False)
            log("daemon", f"request {op.get('op')} from {it['peer']}: {json.dumps(reply)[:300]}")
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            M.print_items([it], False)
        agent_turn(f"This reached your box (the butler's), as the mmbox listener prints it:\n\n{buf.getvalue()}\n\n"
                   f"Decide: if a session of its project can do it, start one (`butler start`) and tell the sender; if "
                   f"you can do it yourself within the rights shown, do it and answer the sender (`send --re`); if "
                   f"neither, answer the sender that it waits, and say so here. Never drop it silently.\n\n"
                   f"(Context now)\n{context_text()}", f"message {it['id']} from {it['peer']}")


def do_op(op: dict) -> dict:
    """A request to the butler, from a peer with execute rights or the console: start, stop, list, inventory."""
    try:
        o = op.get("op")
        if o == "start":
            return start_session(str(op.get("path") or ""), op.get("agent"), op.get("model"), op.get("title"), op.get("task"),
                                 why=op.get("why") or "asked", project=op.get("project"))
        if o == "inventory":
            inv = inventory(refresh=bool(op.get("refresh")))
            return {"at": inv.get("at"), "agents": len(inv.get("agents") or [])} if op.get("quiet") else inv
        if o == "list":
            return {"started": (jload("state.json", {}).get("started") or {})}
        if o == "stop":
            s = (jload("state.json", {}).get("started") or {}).get(str(op.get("profile") or ""))
            if not s:
                return {"error": "not a session the butler started"}
            run(["tmux", "kill-session", "-t", s["tmux"]], 10)
            return {"stopped": s["tmux"]}
        return {"error": f"unknown op {o}"}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}


def daemon(once: bool = False) -> int:
    lock = open(home() / "butler.pid", "a+")
    if os.name != "nt":
        import fcntl
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("ALREADY RUNNING: a butler runs on this computer already", flush=True)
            return 6
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
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
    alive = False
    if pid.isdigit():
        try:
            os.kill(int(pid), 0)
            alive = True
        except OSError:
            pass
    b = butler_me(create=False)
    return {"running": alive and time.time() - float(st.get("beat") or 0) < LOOP_S * 8, "pid": int(pid) if pid.isdigit() else None,
            "box": {"id": b.id, "label": b.label} if b else None, "beat": st.get("beat"), "home": str(home()),
            "inventory": jload("inventory.json", {}), "started": st.get("started") or {}, "claims": st.get("claims") or {},
            "fails": st.get("fails") or {}, "config": config(), "folders": project_folders(),
            "after_s": AFTER_S, "inventory_every_s": INVENTORY_EVERY_S}


def install_systemd() -> str:
    unit = Path.home() / ".config" / "systemd" / "user" / "mmbox-butler.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    client = Path(M.__file__).resolve()
    unit.write_text(f"""[Unit]
Description=mmbox butler (takes what no session took, starts sessions)
After=network-online.target

[Service]
ExecStart={sys.executable} {client} butler run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
""", encoding="utf-8")
    return str(unit)


def main(module, argv: list[str]) -> int:
    global M
    M = module
    import argparse
    ap = argparse.ArgumentParser(prog="mmbox.py butler", description=__doc__.split("\n")[0])
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
    x = sp.add_parser("install", help="a systemd user service that runs the butler (servers)")
    x.add_argument("--systemd", action="store_true", required=True)
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
        u = install_systemd()
        print(f"written {u}; enable it with: systemctl --user enable --now mmbox-butler.service "
              f"(and `loginctl enable-linger $USER` on a server)")
    return 0
