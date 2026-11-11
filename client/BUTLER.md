# Project boxes and the butler (design and as built)

Status: design of 2026-11-08, validated by lp177 (decisions below). Steps 1 to 3 built (project boxes; the
daemon, client/mmbox_daemon.py; the console's Butler tab): see "As built" and "The daemon" at the end. Builds on PROTOCOL.md.

Decisions: sessions the butler starts run in a detached tmux session, in the agent's no-prompt mode; a project is
the name of the folder its session runs in; step 1 (project boxes) comes first.

## The problem

Today a message that is not for one session lands in the machine box, and any session of the computer may take
it. A session of another project then reads a task it cannot do, and either drops it or keeps it without doing
anything: nothing puts it back in the queue, so the task is lost. A session box that closes sends what it still
receives to the machine box, where the same thing happens.

## Goals

1. Projects can always talk to each other. Writing to a box of another project is never blocked (rights as today).
2. A session takes messages on its own only when they are addressed to it or to its project. What belongs to
   another project waits for a session of that project.
3. A task meant for a project never ends up in the machine box, and never disappears when its session closes. It
   waits until a session that can do it takes it.
4. A butler, one per computer, is the only one that may take any project's waiting messages (after 5 min) and
   decides what to do with each: wake or start a session of that project, or do the task itself.
5. The butler reports which agents, providers and models this computer can run, and starts sessions when asked.

## 1. Three kinds of box

| Box | Profile | Who takes its messages on their own | Closes |
| --- | --- | --- | --- |
| session | `<session name>` | its own session | yes (unused 1 h, removed 7 d later) |
| project (new) | `project-<name>` | every open session of that project; the butler after 5 min | never |
| machine | `default` | any session, but only messages with no project or of its own project; the butler after 5 min | never |

- A **project box** is a child of the machine box (the same peers and rights, introduced to them like a session
  box) with `role: project`. It is made the first time a session of that project starts on this computer. It
  never closes and is never removed: it is the project's address on this computer.
- **Which project:** the name of the folder the session runs in (its working directory at `start`), in lower
  case; `--work-project <name>` names another one. Not the git repository (a folder may hold none, or several, and
  git may be missing), and not the pairing space (`--project`): the folder tells what a session can do, the pairing
  space only who it is paired with (`ready` holds Ready, Companion and the others).
- **Addressing:** `send <peer>` reaches the peer's machine box as today; `send <peer>:<project>` reaches that
  project's box on the peer's computer; `send <session>` reaches that session. The gateway table of active boxes
  publishes the project boxes, so the console and `peers` show them.
- **The machine box keeps the tasks of the computer itself** (its files, its tools, a restart), not those of a
  project. A message that reaches the machine box but carries a work project (`work` in the payload, or the
  sender's own project) is handled as if it were sent to that project box: only sessions of that project, or the
  butler, take it on their own.

## 2. What a listener takes on its own

A session's `wait` hands over:

1. its session box (everything, whatever the sender's project: talking across projects is not blocked);
2. its project box;
3. from the machine box: messages with no work project, or with its own.

It no longer takes, on its own, a machine-box message of another project. `inbox --box machine --all` still shows
those messages, and a session may still take one by id on purpose (`take <id>`), for instance when the user asks it.

The butler is the only exception: it takes from every project box and from the machine box, but only messages
nobody took for `BUTLER_AFTER_S` (300 s by default).

## 3. Nothing lost

- **A closed session box** forwards what it still receives to its **project box**, not to the machine box. Same
  for the relay round: a session that does not answer its wake-up ping gets its waiting messages moved to its
  project box, with the same header as today ("for <session>, which does not answer: …").
- **Retention:** the same for every box, one setting (lp177's decision): 7 days (`MMBOX_RECEIPT_DAYS`), with the
  sender's "nobody took it" alert at the end.
- **A message read by the wrong session:** a session that takes a message and finds it cannot do it gives it back
  (`mmbox.py giveback <id> --why "…"`): it goes back to its project box (or to the box the session names) with a
  line saying who gave it back and why. The inbox skill tells agents to give back rather than drop. The butler may
  take a message that was given back right away, without the 5 min wait.

## 4. The butler

### What it is

A service of the client, not one more agent window: `mmbox.py butler run`, one per computer (a machine lock,
`<user>-butler`). It is started:

- by the desktop console (`mmbox-console`), when the console starts, if the setting "Run the butler" is on;
- as a systemd user service on a server (`mmbox.py butler install --systemd`), with no console.

It has its own box (`role: butler`, profile `butler`, child of the machine box, never closes), so peers and the
console can write to it. The mechanical work (watching, waking, starting sessions, the inventory) is code. For a
choice, such as "can I do this task, or which session should do it", or for a chat with the operator, it runs one
turn of a configured agent (by default the computer's best available model, headless, in its own folder
`~/.local/share/mmbox/butler/`, with the message and the list of projects and sessions as context). That agent
answers in a fixed JSON (`{"action": "wake|start|handle|wait", "project": …, "why": …}`) that the code then applies.

### What it does with a message waiting for 5 min

1. **An open session of the project whose listener is down** but whose agent still runs: a wake-up ping (as the
   relay does); if the session's harness can be told (the Claude Code hook), the next prompt reminds it.
2. **A closed session of the project** with a known agent session id: resumed (`claude --resume <id>`, `codex
   resume <id>`…) with a first prompt: "Start mmbox listening (`mmbox-start --session <name>`), then
   take message <id> from the project box."
3. **No session of the project:** a new session is started in the project's folder with the project's default
   agent and model (section 6).
4. **Nothing could be started** (no folder known, no agent, failed or did not listen within 3 min): the agent turn
   decides whether the butler can do the task itself (`handle`). Otherwise the message stays in the project box
   (`wait`), the console shows it as held ("waits for a session of <project>: start failed: …"), and the sender
   is told once.

The butler does not take the message in steps 1–3: it records a **claim** (`claimed by butler: starting <session>
at <time>`, local state and a collector event), so that it does not start a second session for the same message,
and it watches the message being taken. At most one start per project every 15 min; a project whose start failed
three times in a row waits for the operator.

### Inventory of agents and models

Every 6 h, at its start, and on request, the butler probes what this computer can run:

- the agent CLIs found on `PATH` and in the IDE extensions (`claude`, `codex`, `vibe`, `gemini`, `ollama`…), their
  version, whether they are logged in (a command that needs no model call);
- the models each one offers (its own model-listing command or configuration where it has one; otherwise a known
  list for its version, marked as such);
- the project folders it knows: the folders where this computer's boxes ran (`cwd` recorded by `start`), plus those
  listed in its configuration (`butler.json`: `{"roots": ["~/Code"], "projects": {"ready": {"path": …, "agent":
  "claude-code", "model": "claude-opus-5-5"}}}`).

The result is published with the butler's box on the services (the table of active boxes: a `butler` field) and
in a local file. The console shows it per machine. "Refresh now" sends a `butler` control message (`{"op":
"inventory"}`) to the butler box.

### Starting a session on request

A message to the butler box, kind `butler`, from a peer with `execute` rights (or the console, locally):

```json
{"op": "start", "path": "/home/lp177/Code/ready", "agent": "claude-code", "model": "claude-opus-5-5",
 "title": "Paiement Stripe", "task": "optional first task"}
```

1. Checks: the agent and model are in the inventory; the path is under a configured root or is a known project
   folder (never any path); rate limit.
2. **Makes the session box first** (a child of the machine box, name `<project>-<agent>-<4 chars>`), so its id is
   known before the agent starts.
3. Starts the agent in that folder in a **detached tmux session** named `mmbox-<box name>` (one can attach to it;
   it works on a server), with a first prompt: "Run `mmbox-start --session <name> --title … `, listen,
   then: <task>".
4. Answers at once `{"box": "<id>", "profile": "<name>", "tmux": "mmbox-<name>", "state": "starting"}`, then
   `"listening"` once the session's listener runs, or `"failed"` with the reason after 3 min.

`{"op": "stop", "box": …}` closes such a session (its box, then its tmux session). `{"op": "list"}` lists the
sessions the butler started.

## 5. Security

- Starting an agent is running code on this computer: only peers with `execute` rights, and the console, may ask.
- The started agent runs in its no-prompt mode (Claude Code `--permission-mode bypassPermissions`, Codex
  `--dangerously-bypass-approvals-and-sandbox`, and so on), as the operator's own IDE sessions run: in a detached
  tmux session nobody would answer a permission prompt. `butler.json` (`"permission_mode"`) may set another mode
  per agent. This is why the checks above matter: who may ask, which folders, which agent.
- Paths are limited to the configured roots and the known project folders; the agent CLI comes from the
  inventory, never from the message.
- The butler takes nothing from a session box: only project boxes and the machine box.

## 6. Console: the Butler tab

A new tab per machine that runs a butler:

- **Chat:** a conversation with the butler (kind `butler`, `{"op": "chat", "text": …}`); each answer is an agent
  turn with the computer's context (sessions, project boxes, held messages, inventory). Kept as a thread like the
  other conversations.
- **New session:** a form: folder (a list of known project folders plus a picker limited to the roots), agent and
  model (from the inventory: only what this computer can run), title, optional first task. "Start" sends the
  `start` request and shows the new box, its state (starting / listening / failed) and a button to open its
  conversation.
- **Live output:** the butler's whole output, as it runs, for debugging: what the daemon does (messages seen,
  read, claimed, sessions woken or started, probes), and for each agent turn its full stream (the agent run in
  stream-JSON mode: its reasoning text where the provider gives it, its tool calls and their results, its
  answer). Written to `~/.local/share/mmbox/butler/butler.log` (rotated, 7 days) and streamed to the tab, with
  a filter (daemon / agent turns / errors) and a pause. A session the butler started can be watched the same way
  (`tmux capture-pane` of its tmux session, read-only).
- **Inventory:** agents, versions, logged in or not, models, the time of the last probe, and "Refresh now".
- **Project boxes:** each project's waiting messages and their claim ("starting ready-claude-7f2a since 2 min").
- **Settings:** run the butler with the console (on/off), the 5 min delay, the default agent and model per
  project, the roots, the permission mode of started sessions.

## 7. Order of work

1. Project boxes, the take rules, forwarding to the project box, `giveback` (client, PROTOCOL,
   skills `mmbox-inbox` and `-start`).
2. The butler daemon: watching, claims, waking, resuming, starting in tmux, inventory, the `butler` messages.
3. The console: Butler tab, chat, the new-session form, inventory, project boxes, settings.
4. Systemd unit for servers; doc and skills (`mmbox-butler`).

Each step works without the next: step 1 alone already keeps tasks from being lost; they wait in their project
box for a session the operator starts.

## As built (2026-11-08)

- **Code**: `client/mmbox_daemon.py`, run as `mmbox.py butler run|status|inventory|start|stop|chat|log|install
  --systemd` (it needs the full client: the module beside mmbox.py). Its box: profile `butler`, role `butler` (never
  closed, introduced with its role). Its folder: `~/.local/share/mmbox/butler/` (`butler.json`, `inventory.json`,
  `state.json`, `butler.log`, `chat.jsonl`); one daemon per computer (a lock on `butler.pid`).
- **A round every 15 s**: the inventory (every 6 h, `refresh` file or `butler_refresh`); the project boxes and the
  machine box (what nobody took for 5 min, `MMBOX_BUTLER_AFTER_S`; never the backlog, older than 24 h: the user
  decides on it); the sessions it started (listening, or failed after 3 min with the tail of their tmux pane); its
  own box; the chat.
- **Machine box**: what is meant for a project goes to that project's box first; what is meant for the computer is
  taken by the butler.
- **Project box**: nothing while a session of the project listens or starts; else the session of the project used
  last within 7 days is resumed by its agent session id (`claude --resume`, `codex resume`), or a new one is started in
  the project's folder (where its sessions ran, or `butler.json` `projects.<key>.path`), with the project's agent and
  model (`butler.json`) or the computer's best one. One start per project per 15 min; three failures in a row and the
  project waits for the operator.
- **What the butler takes** (nobody to start, or the computer's own messages): only what a sender with `execute`
  rights sent; its agent acts without asking. The rest stays where it is, and the butler tells the operator in its
  chat. A request in its box whose body is JSON (`{"op": "start" | "stop" | "list" | "inventory", …}`) from a sender
  with `execute` rights is done at once and answered (`reply`, JSON); any other message from such a sender goes to its
  agent; from another sender, to the operator (chat).
- **Its agent**: Claude Code in print mode (`claude -p --output-format stream-json`), in its folder, no-prompt mode,
  the computer's context (boxes, folders, inventory) in each turn; the chat resumes one conversation. Every event of
  every turn is in `butler.log`. Other agents for the butler's own thinking: later.
- **Started sessions**: tmux `mmbox-<box>`, the agent in its no-prompt mode (Claude Code `--permission-mode
  bypassPermissions`, Codex `--dangerously-bypass-approvals-and-sandbox`, Gemini `--yolo`; `butler.json`
  `permission_mode.<agent>` overrides), first prompt: start the mailbox with that box and listen. Folders: under
  `butler.json` `roots` (default `~/Code`) or a known project folder.
- **Console**: the Butler tab (state, run it with the console: on by default; chat; new session with folder, agent,
  model, title, first task; the sessions it started, with "End the session"; the inventory; the live output, filtered,
  paused); this computer's menu has "Refresh agents and models".
- **Every computer's inventory**: the butler declares a compact inventory with its box (`inventory` in its
  declaration: agents, versions, logged in, models, the probe's time), kept by the services' table of active boxes
  (a new inventory is no "open" event for the collector). An operator's console shows every butler's; a refresh of
  another computer's is asked to its butler through this computer's machine box (`{"op": "inventory", "refresh":
  true, "quiet": true}`: done, no answer, the table shows it).
- **Console, from the explorer**: on each computer its butler comes first, highlighted; a computer without one shows
  "Start a butler…", which opens its agent and model with Cancel and Launch (this computer: run with the console;
  another: a task to its machine box, which a session there carries out). "New session…" above the list, when a
  computer, a project or a box is selected, opens the computer, the project (or a folder here), the agent and model
  (the Settings' defaults), title and first task; on another computer its butler starts it (`{"op": "start",
  "project": …, "quiet": true}`). Settings: the butler's agent and model (butler.json), the sessions' default agent
  and model, the butler with the console.
- **Tests**: `MMBOX_BUTLER_CLI_<AGENT>` replaces an agent's CLI (a command line), so that a bench runs without real
  agents.

## The daemon (2026-11-08)

- **One program per computer**: `client/mmbox_daemon.py`, `mmbox.py daemon run|status|install|uninstall|start|stop|
  session-input|session-output|…` (`butler` is the same command). Desktop: the console starts it when it opens (one per
  computer; Settings can turn it off). Server or headless: `mmbox.py daemon install` makes it a service of the user:
  systemd (Linux; `loginctl enable-linger` to run without a login), launchd (macOS), a logon task running pythonw
  (Windows: no window). `daemon uninstall` removes it. Its folder: `~/.local/share/mmbox/daemon/`
  (Windows `%LOCALAPPDATA%\mmbox\daemon`).
- **Its own controls** (`daemon start|stop` start and stop agent sessions): `daemon up` starts it (through systemd or
  launchd when it is their service, else as a detached process), `daemon down` stops it and keeps it stopped (a file
  `stopped`: the console's "Run the butler" no longer starts it again; `up` or the next login does), `daemon restart`
  (it loads the client as it is on disk), `daemon update [--apply]` (the client's clone against its origin, the same
  6 h lock file as the skills' updater; `--apply`: fast-forward only, never over local changes, selftest, restart),
  `daemon about` (JSON: state, boxes, version, last update check).
- **Its icon** (`client/mmbox_tray.py`, `daemon tray`): when the computer has a desktop and no console with its own icon
  runs, the daemon starts an icon in the notification area (Linux: a StatusNotifierItem with Dbusmenu, as the console's;
  Windows: Shell_NotifyIcon through ctypes, nothing to install; not on macOS yet). A dot says its state (green running,
  grey stopped, gold busy). The right button: its state, Information..., Start, Stop, Restart, Check for updates
  (Update to ... when the clone is behind), Quit (stops the daemon, removes the icon). The left button opens its
  window (`daemon window`, tkinter, one at a time; Debian: `python3-tk`): the logo, the state, this computer's boxes,
  the agents found, the client's version, the last check, and the same buttons. The icon quits when a console with its
  own icon starts (its menu and its Butler tab then have the same actions), comes back within a minute or two after
  it quits, starts again by itself on a new client, and is tried again 30 min after it could not show.
  `butler.json` `"tray": false` turns it off.
- **Hidden sessions**: Claude Code sessions run without a terminal: a runner per session (`daemon session-run`,
  detached, no window on any OS, outlives a restart of the daemon) keeps the agent's input open (stream-JSON: it stays
  alive, listens, wakes on its background commands) and appends its output to `sessions/<box>/out.jsonl`; what the
  operator types goes to `in.jsonl`. The console shows it live ("Output" beside each session the daemon started) and
  lets the operator write into it or stop it. Other agents: tmux (Linux, macOS), not on Windows yet.
  `butler.json` `"launcher": "tmux"` brings Claude Code back to tmux.
- **Ops, operator key only**: what the daemon does on request (status, config, inventory, start, stop, input,
  output, box: empty, close or delete one of this computer's boxes, the console's box menu for another computer's
  box; unqueue: held messages a box of this computer keeps resending, dropped when the console deletes them) is asked by an `op` message signed by the operator's console and sent to the butler's box. The client accepts
  an `op` only from the operator key it pinned (`collector enable … --fingerprint`), and only in the butler's box; the
  daemon answers with an `op_result` to the operator's box. Messages from peers are never ops, whatever their rights.
  `mmbox.py daemon operator [--fingerprint FP]` shows the pins of this computer's services and pins FP where none is
  (never replacing one); `daemon install` asks for it on a terminal when a service has none.
- **Windows**: the service is a shortcut in the user's Startup folder running pythonw (a logon task needs an
  administrator); hidden Claude Code sessions work there (tested on a Windows computer: no window, input and output, stop); the IDE
  extensions' CLIs are taken for this system only (`codex.exe`, not the Linux build some ship).
