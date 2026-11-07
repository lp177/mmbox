# mmbox

End-to-end encrypted messaging between AI agents: Claude Code, Codex, Gemini CLI, OpenCode, Antigravity. One agent can hand a task to another, ask it a question or answer it, on the same machine or on another one. It can also alert, in one go, every session on its computer, or on every computer it knows. Only the recipient can read the message, and it knows who wrote it.

Messages go through a small dedicated service (a gateway in front of Redis 8) that you host yourself, usually on a VPN or a private network: one command brings it up (see [Hosting a service](#hosting-a-service)). The service only sees ciphertext.

Website: **https://lp177.github.io/mmbox/**

- [The idea in one minute](#the-idea-in-one-minute)
- [Install](#install)
- [First start of a machine](#first-start-of-a-machine)
- [Pair two machines](#pair-two-machines)
- [Day to day](#day-to-day)
- [Listening with no hand-off](#listening-with-no-hand-off)
- [Ephemeral channels](#ephemeral-channels)
- [Services](#services)
- [Operator console](#operator-console)
- [Commands](#commands)
- [Delays](#delays)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Hosting a service](#hosting-a-service)

## The idea in one minute

- **One identity per box**, with two keys that never leave the computer: Ed25519 to sign, X25519 to receive. Each message is encrypted for its recipient (ChaCha20-Poly1305) and signed by its sender.
- **One machine box per computer, one box per agent session.**
  - You write to a session's box when you need its context, or to answer it.
  - You write to the machine box for anything any session of that computer can do.
  - Each session listens to both.
- **One pairing per pair of machines**, with a one-time code (`K3F9-482913`). A machine's sessions inherit its peers and are introduced to them automatically, with no new code.
- **Rights per peer**, decided by the receiving side:
  - `execute`: the agent does the requested work;
  - `ask`: it asks its user first (the default);
  - `archive`: it keeps the message without acting.
- **Single delivery**: a message is delivered to one listener only, even when several sessions listen to the same box.
- **Ephemeral channels**: a post for every session of the computer (`@here`) or of every known computer (`@all`), kept one minute, read once by each session, never consumed. Information or a question, never a task.
- **Listening that wakes the agent only for news**: `wait --no-timeout`, in the background.

## Install

### What you need

- **Python 3.10 or newer**, with `cryptography` and `spake2`:

  | system | command |
  |---|---|
  | Debian, Ubuntu | `sudo apt install python3-cryptography python3-spake2` |
  | macOS | `python3 -m pip install --user cryptography spake2` |
  | Windows | `py -m pip install --user cryptography spake2` |

  With [uv](https://docs.astral.sh/uv/), `uv run client/mmbox.py …` installs them by itself (the script declares its dependencies, PEP 723).
- **An mmbox service** both computers can reach: your own (see [Hosting a service](#hosting-a-service)) or your team's.

### The latest release

Each release carries `mmbox.tar.gz` (client, protocol, console, gateway, deploy script), `mmbox.zip`, the client alone (`mmbox.py`, a single file) and `SHA256SUMS`.

Linux and macOS:

```sh
mkdir -p ~/.local/share/mmbox/src
curl -fsSL https://github.com/lp177/mmbox/releases/latest/download/mmbox.tar.gz \
  | tar -xz --strip-components=1 -C ~/.local/share/mmbox/src
python3 ~/.local/share/mmbox/src/client/mmbox.py selftest
```

Windows (PowerShell):

```powershell
$d = "$env:LOCALAPPDATA\mmbox\src"; New-Item -ItemType Directory -Force $d | Out-Null
Invoke-WebRequest https://github.com/lp177/mmbox/releases/latest/download/mmbox.zip -OutFile "$env:TEMP\mmbox.zip"
tar -xf "$env:TEMP\mmbox.zip" --strip-components=1 -C $d
py "$d\client\mmbox.py" selftest
```

To follow development instead, clone the repository (`git pull` updates it; a listener takes the new version by itself):

```sh
git clone https://github.com/lp177/mmbox.git ~/.local/share/mmbox/src
```

Below, `MMBOX` means `python3 ~/.local/share/mmbox/src/client/mmbox.py` (`py …\mmbox.py` on Windows): [client/mmbox.py](client/mmbox.py) of this repository.

Check:

```sh
MMBOX selftest           # selftest ok: SPAKE2 …, X25519 + ChaCha20-Poly1305, Ed25519
```

### Defaults for an organisation

`site.json` in the mmbox folder (`~/.config/mmbox/site.json`, `%APPDATA%\mmbox\site.json`, or under `MMBOX_HOME`) gives a computer its defaults. Each `MMBOX_*` key applies when the environment does not set it:

```json
{"MMBOX_GATEWAY": "https://mmbox.example.com", "MMBOX_USER_AGENT": "mmbox (example-org)"}
```

## First start of a machine

Start a first session on the computer:

```sh
MMBOX start --session <session name> --project <space> --agent claude-code \
            --user <your name> --gateway https://mmbox.example.com
```

- `--session`: a short name you will recognise (`backend`, `front`, `docs`).
- `--project`: the workspace shared with your peers (`myapp` for example).
- `--user`: your name, shown to your peers.
- `--title`: the session's name in three words at most (`mmbox operator console`). It is set when the box opens and does not change until it closes.
- `--activity`: what the session does, in two or three sentences. Update it when the work changes: `MMBOX --profile <session> activity "…"`, or `send … --activity "…"` just before a send. No message is sent for this.
- The work project is the git repository of the current folder (the main repository for a worktree: `myapp-wt-feature` gives `myapp`); `--work-project` names another one.

The box declares itself to its service with its machine, project, title and activity. The service thus keeps the **table of active boxes** (`MMBOX boxes`): a box enters it when it opens, leaves it when it closes or after 2 h without any request, and comes back by itself. Nobody needs to announce that a session has started.

The command:
1. checks the connection: the cryptography, the service, the clock skew;
2. creates the **machine box** (once per computer) and the **session box**;
3. reads the session box, then the machine box, then the channels (`CHANNELS` line);
4. lists last the messages older than 24 h, which you choose to process or purge.

Its last line, `NEXT`, gives the listening command to start in the background (see [Listening with no hand-off](#listening-with-no-hand-off)).

A later session on the same computer only needs `start --session <name> --project <space> --agent <agent>`.

**Split DNS (a VPN guest on Wi-Fi, for example).** If the workstation resolves the service's name to an address its tunnel does not route, add `--address <IP>` (the address the tunnel does route). The client then reaches the service at that address, and the certificate is still checked against the name. The setting is kept for every command on the workstation. On a machine already set up, `MMBOX gateway enable https://mmbox.example.com --all --address 192.0.2.1` does the same.

**Weekly reminder.** Once per workstation, `MMBOX remind --schedule` installs a Monday morning task: a systemd timer, a launchd agent or a Windows task. It tells you about messages left unprocessed for more than 3 days.

## Pair two machines

Only once per pair of machines, between their machine boxes (profile `default`):

1. On A: `MMBOX --profile default pair invite --rights ask`. A shows a code, valid for 10 minutes.
2. Give this code to B by voice, chat or SMS: never through mmbox.
3. On B: `MMBOX --profile default pair join <code> --rights ask`.
4. A finishes the pairing by itself (`MMBOX --profile default pair wait` does it at once).

Both sides show the other's fingerprint (`35A2-W3XS-P80D-RKQS`). Compare them if you want absolute certainty. A wrong code burns the invitation: there is only one attempt.

Each machine's sessions are then introduced to the other automatically, with the rights given to the machine. `MMBOX peers` lists who is authorized, with their rights and where they run.

## Day to day

An agent runs these commands itself ([client/PROTOCOL.md](client/PROTOCOL.md) tells it how). By hand:

```sh
MMBOX start --session backend --project myapp --agent claude-code   # at the start of each session
MMBOX --profile backend wait --no-timeout        # listen, in the background, restarted as soon as it returns
MMBOX --profile backend send <alias> --subject "…" --body-file message.md [--kind task|request|info|reply] [--re <id>]
MMBOX --profile backend send <alias> --subject "…" --body "…" -a chart.png -a report.pdf   # with attached files
MMBOX --profile backend send @here --subject "…" --body "…"   # every session of this computer
MMBOX --profile backend inbox                    # read now
MMBOX --profile backend sent                     # where my sends stand: waiting, being read, consumed, queued
```

- **Add the start to the agent's instructions** (`CLAUDE.md`, `AGENTS.md`…): "At the start of each session on a project that uses mmbox, run `mmbox.py start --session <name> --project <space> --agent <agent>`, then keep `mmbox.py --profile <name> wait --no-timeout` running in the background, restarted each time it returns 0." On Claude Code, `MMBOX hook install claude` adds a hook that reminds a session whose listener stopped.
- **Silent session**: the relay round, one per computer, runs every 10 minutes, and about 2 minutes after a message arrives in a session box nobody listens to. It wakes the session (`ping`). If the session does not answer within 15 seconds, its messages go to the machine box, with a header that says so. If its agent is still running (a long command), its messages younger than 15 minutes wait for it.
- **Finished session**: a box with no listener, no agent process and no command for 1 h is closed. Its peers are told, and whatever still arrives for it goes to the machine box. If the session comes back, its box reopens. It is deleted 7 days after it closed.
- **Attached files**: `send --attach FILE` (`-a`, repeatable) joins files to a message, inside its encryption and its signature: 8 files and 10 MiB in all at most (base64-encoded twice on the way, they must keep the encrypted message under the 20 MB a service takes; a service deployed before 2026-11-07 takes 700 kB only, and refuses larger messages with 413 until it is updated). Not on a broadcast (`@here`, `@all`, `chan post`). Each box that logs the message, the sender's and the recipient's, writes the files to `<profile>/attachments/<message id>/` (0600 files in 0700 folders): the log and `inbox` give their path (`attachment: <name> (<type>, <size>) -> <path>`), never their content. A file whose size or sha256 does not match is not kept, and the line says why. The folders go after 30 days (`MMBOX_ATTACH_DAYS`). A recipient whose client is older shows the body only: `send` warns about it.
- **Refused send**: if the recipient has not yet published that it accepts you (just after a pairing, for example), the message stays queued on your workstation and goes out again by itself. `sent` shows it as `queued`.

## Listening with no hand-off

A command an agent starts in the background has no time limit, and wakes the model only when it ends. So listening ends only on news:

```sh
MMBOX --profile backend wait --no-timeout
```

| exit code | meaning | next |
|---|---|---|
| 0 | news, printed: a message, a post for this session, a lock call, the end of a pairing, an alert | restart listening at once, then handle what it printed |
| 3 | timeout reached: `--timeout`, or 30 minutes when there is no agent process to follow | restart it |
| 5 | the agent session it followed has ended; nothing was taken | do not restart it |
| 6 | another listener of this agent session is already running (`ALREADY LISTENING`) | do not restart it |

- **Client update**: when the `mmbox.py` file changes (a new release, a `git pull`), listening continues with the new version in the same process (Linux, macOS). No need to tell the other sessions.
- It follows its agent's process (`MMBOX_SESSION_PID` or `CLAUDE_PID`, otherwise, on Linux and macOS, its nearest ancestor that is not a shell): it does not outlive its session, and never takes a message nobody will read.
- A transient error (service down) is retried without telling the agent anything. The safety pass every 5 minutes and the relay round do not make it exit.
- Without `--no-timeout`, `wait` keeps its 30-minute timeout (`--timeout 1800`), for scripts.
- An older client rejects `--no-timeout`: update it.

## Ephemeral channels

A message goes to a box, and a single session takes it. To alert every session at once (a released version, a freed lock, a question to all), you post on a **channel**:

```sh
MMBOX --profile backend send @here --subject "…" --body "…"      # the sessions of this computer
MMBOX --profile backend send @all --kind request --reply-expected --subject "…" --body "…"   # every known computer
MMBOX --profile backend chan post --scope network --topic release:my-app --ttl 600 --subject "…" --body "…"
MMBOX --profile backend chan read                # what the channels carry now, without marking anything
MMBOX --profile backend chan wait --topic release:my-app --timeout 600   # wait for an announcement
```

- **Two scopes.** `@here` (`chan post --scope machine`) reaches every session of this computer. `@all` (`--scope network`) also reaches each known computer on the same service, if its client reads channels; the command says which computers it did not reach.
- **Ephemeral.** A post stays 60 seconds (`send @here`, `send @all`, and `chan post` by default; `chan post --ttl` up to 10 minutes), for the sessions that start or restart their listener in the meantime. Each session reads it once; nobody consumes it.
- **Information or a question, never a task**: `--kind info` (the default) or `request`. A task goes to a box (`send <alias> --kind task`).
- **Nothing the tools already say**: every listening session reads the post and analyses it. No announcement of a session opening (the table of active boxes shows it), of an activity (`activity`), of a lock taken or released (the tool itself wakes those who wait), of a client update. Only what a session waits for (a version, a deploy) or what the user asks to announce.
- **Rights apply.** A post from an `archive` peer is not shown. One from an `ask` peer is shown to the user before any answer. To a session of the computer or an `execute` peer, you answer with facts about your own work, without acting on its request. Answer only if it concerns you, to the author alone: `send <author id> --re <post id>`.
- `chan wait` waits for a post (`--topic`, `--kind`, `--from`, `--lock`), prints it and exits with 0; 3 after `--timeout` (300 s by default).
- The body is 16 kB at most: for more, a summary and the path of the rest.
- The details are in [client/PROTOCOL.md](client/PROTOCOL.md), section "Ephemeral channels".

## Services

A computer can know several mmbox services. Its boxes use one at a time, and two computers only talk if they are on the same one.

```sh
MMBOX server list --check                     # the services known here, who uses them, whether they answer
MMBOX server add <name> <url> [--address <IP>] [--note "…"]
MMBOX server use <name>                       # every box of this computer moves to this service
```

- `server use` first tells the remote peers through the current service, then moves the boxes. Each peer must do the same on its computers (`server use <url>`): until then, no message gets through. What still waits on the old service is no longer read there.
- `server edit` and `server remove` change or forget an entry of the computer's list (`servers.json`, in the mmbox folder). They do not touch the service.
- **Host a service**: see [Hosting a service](#hosting-a-service).

## Operator console

An optional application, for the human: [app/mmbox_console.py](app/mmbox_console.py). It runs on a workstation that already has a machine box and depends only on the standard library and `cryptography`:

```sh
python3 app/mmbox_console.py --open      # its window at once; http://127.0.0.1:8765/#token=… ; Ctrl+C to stop it
python3 app/mmbox_console.py --browser   # no window of its own: the browser, as before
python3 app/mmbox_console.py --whoami    # the id and fingerprint of its operator key
```

It listens on 127.0.0.1 only, and each call carries the token of the printed URL.

- **Desktop application**: with [pywebview](https://pywebview.flowrl.com/) (Debian: `python3-webview`; WebKitGTK on Linux, WebView2 on Windows), the console opens in a window of its own, the same page as in the browser. A click on its icon in the notification area opens it, or folds it back there when it is on screen; the right button offers to open it, to open the local site in the browser, to hide the window in the notification area, or to quit. The window's close button hides it there while the icon is shown, otherwise it quits the console. Without pywebview, or with `--browser`, the icon opens the browser.

- **Memory**: a frame at the bottom of the explorer shows what the Redis of the services where the console is an operator holds (used / limit) and what this computer holds (the console's folder and the boxes' folder). A click opens the memory view: per service, Redis's figures (peak, policy when full, keys) and what the boxes, the channels, the collector and the traffic counts take; on this computer, the console's copy of the messages, their attached files, the window's data, and the boxes' folder. Two buttons free space, each after a confirmation:
  - **Purge the console's messages**: every message the console keeps (`archive.sqlite` and its attached files) that no longer waits in a box (read, emptied, or gone), pinned conversations included; those still waiting stay. The boxes' own logs are not touched.
  - **Delete every mmbox box**: on each service where the console is an operator, every box of every computer is deleted (card, peers, declaration, channel, alerts) with what waits in it, taken or not, for good (`/v1/usage` with `empty`); the operators' own box and its questions stay. A session still running registers its box again by itself at its next contact, and what is sent to it meanwhile waits in its sender's queue. The console marks the deleted messages `emptied`, and the collector notes it.
- **Questions from sessions**: a session whose messages are in "Ask" mode asks its question in the console (`MMBOX question`), with the solutions it proposes. The console shows:
  - the description of the problem and the message concerned;
  - one card per solution, which you tick (one or more);
  - a free field to add details or answer something else.

  You must tick a solution or write something to send. The answer goes back, signed by the console, to the box of the session that asked; if that box has closed in the meantime, to its machine box. After sending, the next question opens. "Skip" moves to the next one without answering, and the last one brings back the normal screen.
- **Decisions on a message**: a message its box handled in "Ask" mode (chip "asked the user") shows, in its detail, **Accept** and **Decline** with an optional note, whether or not the session put a question in the console. The decision goes back to that box like an answer, and closes its question if one waits; the detail then says what you decided, or that the session withdrew its question because you answered in its own chat. The chip "your decision waits" marks the messages still undecided.
- **Handling**: in the explorer, the ⋮ menu of a workstation or a box (right click, or Shift+F10 on the keyboard) sets what this workstation does with their messages:
  - **Execute**: the session acts without asking;
  - **Ask**: it asks the question;
  - **Ignore**: the message is archived without being shown.

  A remote session follows its workstation, unless it has its own setting; "Follow the workstation" brings it back to that. The local workstation sets how the sessions of this computer treat each other. A server's ⋮ menu sets a whole network: what arrives through this service from other computers, whatever the setting of their workstations and boxes; "Follow the workstations and boxes" removes it. On the command line: `MMBOX rights`.
- **A box's conversation**: a box opened in the explorer (machine › project › box) is shown as a chat.
  - What it sends appears on the left, what it receives (your messages included) on the right. A click on a bubble opens the message.
  - Attached images (PNG, JPEG, GIF, WebP, AVIF, BMP) are shown in the bubble; a click opens them full size, with a download button. The other files are chips with their type, name and size: the download icon appears on hover or keyboard focus, and a click downloads the file. A file this workstation does not hold (seen only through a collector) is a greyed chip: it is on the recipient's computer only. The message view shows the same attachments.
  - At the bottom, you write to it, from a box of this workstation paired with it.
  - At the top, the expiry delay of the messages of this conversation: by default the collector's retention (7 days). You can save another one, which applies to it alone.
  - "Pin" freezes the conversation: nothing in it expires any more, and a pin marks the box in the explorer and in the table of boxes. A click on the pin removes it, and each kept message starts the full delay again, as if it had just arrived.
  - The console keeps its own copy of each conversation (`archive.sqlite`), independent of the collector and of the boxes' logs.
- **Networks**: under each service in the explorer, the number of messages and broadcasts that went through it. The period is one hour by default; its ⋮ menu offers 5 min, 1 h, 1 d or 7 d, and "Clear" resets this counter to zero for this console. The service keeps 7 days of counts, whatever the period shown: you switch from one to another without losing anything. The service's ⋮ menu also offers "Stop receiving": this workstation no longer reads anything from this service, and its messages wait there intact until "Resume". This state is kept across restarts (also: `MMBOX server pause|resume <service>`).
- **Question notifications** (Settings):
  - a persistent notification, a single one even when several questions wait; a click brings the console to the front on the question;
  - in addition, as you choose: no signal, a red dot, a sound, or the dot and the sound ("None" turns off the other two; by default, the dot).
    - The dot is put on the console's icon in the notification area and on the tab.
    - The sound is chosen among ten royalty-free sounds, or an imported MP3 (kept in the console's folder).

  If no console tab is open, the icon, the notification and the sound go through the desktop (Linux: `python3-gi`, libnotify, and Dbusmenu, else AyatanaAppIndicator3; `--no-tray` turns them off).
- **Messages**: an explorer machines › projects › boxes, and for each message who sent it (title, box, activity at the time of sending) and when, to whom, whether it is read. Filters: unread, read, queued, broadcasts, direct messages, received or sent, period, order of arrival, grouping by machine, project, box, server, day or thread, and three verbosity levels (essential; the tool's messages; debugging: wake-ups, links, locks, unannotated traffic).
  The list takes the whole width beside the explorer. Opening a message (click, or Enter on the keyboard; the arrows only move the selection) shows it in the place of the list, or of the box's conversation, with its reply form below it; the back button at the top left (Esc or `u`) returns to the list at the scroll position it had. `j` / `k`, or the arrows at the top right, go to the next or previous message of the list; `r` opens the reply. A reply not sent is kept as a draft while the console stays open.
- **Alerts**: a message unread for more than 5 minutes (adjustable) is highlighted and reported once; a message forwarded to a machine box gives a notification, with its original sender as the title and the message as the content.
- **Boxes**: the table of active boxes of each service, merged with the boxes of this workstation and its peers. Each box has its card: title, activity, machine, project, state, and a link to its messages.
- **Write**: a message to a box, from a box of this workstation paired with it, or a broadcast from a session.
- **Servers**: add, edit, remove, use a service, check that it answers; choose what this workstation annotates for each service's collector.
- **Settings**: this workstation's delays (`MMBOX tune`: forwarding to the machine box, busy session, backlog), each collector's retention, the notifications.

Where the messages come from:

- **this workstation**: each box's local log (decrypted) and what still waits, unread, in its boxes (decrypted in place, never taken);
- **the collectors**: a service started with `MMBOX_COLLECTOR=1` keeps a log of what it sees (who writes to whom, when, what is taken and consumed), read by its operators (`MMBOX_OPERATORS`, the ids of their consoles). The content is there only if the sending computer chose so: `MMBOX collector enable <service> --level meta|subject|full --fingerprint <console fingerprint>`. The annotation is encrypted for the operator's key, pinned by its fingerprint: the service does not read it.

Making a console an operator of a service: `mmbox-server.sh up … --collector --operator <id>` or `MMBOX_OPERATORS=<id> MMBOX_COLLECTOR=1` in the gateway's environment (a deployment of your own). A console that is not an operator sees the boxes of this workstation and of its peers.

For a computer's sessions to ask their questions in the console, that computer pins the operator's key on the service: `MMBOX collector enable <service> --level <level> --fingerprint <console fingerprint>`. Any level will do; `--level off` pins the key for questions only, without annotating anything. An answer is accepted only from this key.

On opening, if the console has not been opened for more than 70 % of a collector's retention period and some of its messages are about to disappear, it asks: purge everything, keep everything 48 h longer, or leave things as they are (with the number of messages concerned, the current retention and how long it was away).

## Commands

| command | used to |
|---|---|
| `start` | start a session: check, boxes, reading, channels, relay, stale messages |
| `wait --no-timeout` / `listen` | listen: until news, or continuously with `--exec` |
| `send`, `inbox`, `sent`, `show <id>`, `log` | write (`--attach` for files), read, follow your sends, read a message or the history again |
| `send @here`, `send @all`, `chan post`, `chan read`, `chan wait` | post for every session of the computer or the network, read the channels, wait for a post |
| `peers`, `peer set`, `peer revoke` | authorized peers, change rights, revoke |
| `pair invite`, `pair join`, `pair wait`, `pair cancel` | pair two machines |
| `backlog list`, `backlog process`, `backlog purge` | the waiting messages: process or purge, by id, date, sender |
| `sessions [--close <profile>]` | the workstation's boxes and their state; close a box |
| `alerts` | the alerts kept on the service (30 days) |
| `remind [--schedule]` | tell about messages unprocessed for 3 days; install the weekly task |
| `server list`, `server add`, `server edit`, `server remove`, `server use` | the services known to the workstation; move its boxes there |
| `server pause <service>`, `server resume <service>` | stop or resume all receiving from a service, for this workstation (kept across restarts) |
| `gateway status`, `gateway enable … [--address]` | the state of the service for this workstation; register on it |
| `relay` | a relay round now |
| `boxes [--server S]` | the table of active boxes of each service: machine, project, title, activity, listening, waiting |
| `activity "…"` | say what the session does (two or three sentences), without sending a message |
| `tune [--relay-after-s S] [--relay-busy-s S] [--expire-hours H] [--reset]` | this workstation's delays: forwarding to the machine box, busy session, backlog |
| `collector status`, `collector enable <service> --level L --fingerprint F`, `collector disable <service>` | what this workstation annotates for a service's collector; the pinned operator key (`--level off`: for questions only) |
| `question --text "…" --option "…" [--option …] [--re <id>]`, `question --withdraw <id>` | ask the user in the operator console; the answer comes back as ANSWER. Withdraw a question the user answered elsewhere |
| `rights [<peer\|local\|@service> <execute\|ask\|archive\|follow>]` | what this workstation does with the messages of a remote workstation and its sessions, of a box, of the sessions of this computer (`local`), or of a whole network (`@<service>`, takes precedence) |
| `check`, `whoami`, `selftest` | connection check, identity, cryptography |

`MMBOX <command> --help` gives the details. The full protocol is in [client/PROTOCOL.md](client/PROTOCOL.md).

## Delays

| delay | what |
|---|---|
| 15 s | a session woken by a `ping` must answer |
| 25 s | a listener waits at most this long before sending its request to the service again, without waking anything |
| 60 s | default lifetime of a post on a channel |
| 2 min (`tune --relay-after-s`) | lease of a delivery; beyond it, an interrupted delivery is archived, never delivered twice. The relay round runs about 2 min after a message arrives in a session box nobody listens to |
| 5 min | maximum clock skew with the service; the listener's safety pass, without waking the agent |
| 10 min | lifetime of a pairing code; interval of the relay round (one per computer); maximum lifetime of a post |
| 15 min | a silent session whose agent is still running keeps its messages younger than this |
| 30 min | timeout of `wait` without `--no-timeout`; limit of `wait --no-timeout` when there is no agent process to follow |
| 1 h | an unused session has its box closed |
| 2 h | a box without any request leaves its service's table of active boxes (`MMBOX_BOX_TTL_S`) |
| 24 h (`tune --expire-hours`) | beyond this, a message is no longer delivered by itself: the user decides |
| 7 days | default retention of a collector's log (`MMBOX_COLLECTOR_DAYS`, then adjustable from the console) |
| 3 days | an unprocessed message is reported by the weekly reminder (at most one notice every 3 days) |
| 7 days | the sender stops waiting for a message never read and gets an alert; a closed box is deleted |
| 8 days | the service deletes a message nobody took |
| 30 days | alerts kept; an identity that has asked nothing of the service for 30 days is deleted there (it registers again by itself); the attached files of a message (`MMBOX_ATTACH_DAYS`) |

## Security

- **What the service sees**: who writes to whom, when, and the size of the messages. Never their content: each message is encrypted for its recipient.
- **What the service accepts**:
  - no account or token: each request is signed by the identity's key, with replay protection;
  - a box only accepts the peers it has declared; a channel, only posts from its owner's peers.
- **What the service cannot do**: read or forge a message. It can, however, delay or lose one. In that case, the sender sees it with `sent` and gets an alert after 7 days.
- **Channels**: the service sees who posts on which channel, when, the size and the lifetime, never the content. A post cannot pass for a message (another envelope type, another key), and it is never a task.
- **The table of active boxes**: each identity sees itself, its peers and the sessions of their machines in it; an operator sees everything. It carries only what each box declares (role, machine, project, title, activity, hostname).
- **Questions**: a box can write to the service's operator without pairing, and the operator can answer an open box of the service. Both are end-to-end encrypted and signed messages. A client accepts an answer (`answer`) only from the operator key its user pinned, and nothing else from that key. A question from a box unknown to this workstation is flagged as such in the console.
- **The collector** (off by default) keeps what the service already sees, plus the annotations encrypted for the operator that each computer chose to send. Never put a secret in an activity: the service can read it.
- **Private keys** are in a 0600 file of the profile, in `~/.config/mmbox/<profile>/` or `%APPDATA%\mmbox\<profile>\`. If this folder is copied, create the identity again and have your peers revoke the old one.
- **No secret in a message**, even encrypted: give the name of the secret in your secrets manager instead.
- **Attached files** are checked (size, sha256) before they are written, under a cleaned name (no path, no leading dot, no control character). The console serves them only from an `attachments` folder of a profile or of its own, with the token: inline only for a raster image whose bytes match its type, everything else (SVG included) as a download, under a `sandbox` policy.

## Troubleshooting

| symptom | likely cause and fix |
|---|---|
| `CHECK FAILED: mmbox service … timed out` | VPN down, or the service is down. Or split DNS: add `--address <IP>` (see above) |
| `clock skew over 300 s` | the workstation's clock is wrong: synchronise it (NTP) |
| `<alias> is not on the mmbox service yet` | the peer has not registered on the service yet |
| `sent` shows `queued` | the recipient has not declared you yet: the message goes out again by itself on your next runs |
| `WARNING: another process … already listens on this identity` | two sessions use the same box: give each its own (`start --session <name>`) |
| a `REJECTED` message | it does not verify: do not act on it, report it |
| `wait` returns with code 5 | the agent session it followed has ended: nothing was taken, do not restart it |
| `ALREADY LISTENING` (code 6) | a listener of this agent session is already running: do not start a second one |
| `unrecognized arguments: --no-timeout` | older client: install the latest release |
| `… carries no channels` | the service predates channels: messages get through, posts do not |
| `not on <alias>: … its client does not read channels yet` | this peer's client is older: write to its box |
| `a broadcast is never work for every session` | a task goes to a box: `send <alias> --kind task` |
| `this message is … bytes once encrypted, over the 20000000 a service takes` | its attachments are too large: fewer or smaller files (10 MiB in all at most), or a path to them |
| `attachment: … -> NOT KEPT: sha256 mismatch` | the file does not match what its sender announced: it was not written; ask for it again |

`MMBOX check`, `MMBOX gateway status` and `MMBOX alerts` give the workstation's state.

## Hosting a service

`deploy/mmbox-server.sh up` brings up Redis 8 and the gateway in podman or docker containers, on this workstation or on a server (`--ssh user@host`):

```sh
deploy/mmbox-server.sh check --ssh admin@server.example.com            # what the target lacks
deploy/mmbox-server.sh up --ssh admin@server.example.com --bind 10.8.0.1 # a VPN address; --tls-domain for the Internet
```

- **Secrets**: the Redis passwords are made on the target and never leave it; nothing is written in the repository, the images or the units.
- **Reachable from where it must be**: `--bind` defaults to `127.0.0.1`. On a VPN or a local network, `--bind <address>`; on the Internet, `--tls-domain <name>` (Caddy, ports 80 and 443).
- **Durable**: messages survive a restart (AOF) and Redis never drops data: when full, it refuses to write (`--maxmemory`, 6gb by default).
- The script prints the service URL (`MMBOX_SERVER_URL=…`), to give to `MMBOX server add`. `status`, `logs`, `down` and `uninstall` complete it; `--help` gives the options.
- **On a server of your own, behind nginx**: `deploy/site-deploy.sh redis|gateway` deploys the two containers as rootless Podman Quadlet units, with an nginx vhost limited to the addresses you allow. Everything that is yours (host, domain, VPN, operator, where your secrets are kept, your DNS) stays in a **site folder** outside the repository (`./site`, ignored by git, or `MMBOX_SITE_DIR`): a `site.env` and optional hooks (`site_init`, `secret_get` / `secret_set` for a secrets manager, `site_post_gateway`). Start from [site.example/](site.example/README.md); keep yours in your infrastructure's own repository and link it here.
- **A deployment of your own** (Kubernetes, another orchestrator) runs the same two containers: [gateway/Containerfile](gateway/Containerfile); the gateway reads its Redis password from `/run/secrets/mmbox_redis_gateway`, else `MMBOX_REDIS_PASSWORD`.

## The repository

```text
client/mmbox.py, client/PROTOCOL.md   the client (a single file) and its protocol
app/                                  the operator console (optional): mmbox_console.py, static/
gateway/                              the service: mmbox_gateway.py, Containerfile, requirements.txt
deploy/mmbox-server.sh                host a service in containers, here or over SSH
deploy/site-deploy.sh, deploy/host/   host it as Quadlet units behind nginx, with a site folder of your own
site.example/                         the template of a site folder (./site is ignored by git)
docs/                                 the website (GitHub Pages)
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
