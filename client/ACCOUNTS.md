# Accounts, devices and the operator (design, not implemented yet)

Status: design of 2026-11-08 from lp177's decisions. No SSO: an identity provider (Authentik, tried that day and
removed) protected nothing that matters here and was slow; the authentication is the keys. Replaces the single
operator key (`MMBOX_OPERATORS`, the key pinned by `collector enable --fingerprint`, `mmbox_console.py --export-key`)
once built. Builds on PROTOCOL.md and BUTLER.md.

## Decisions

- No SSO and no password on the service: what makes an operator is a key, never a sign-in.
- The daemons trust an account's **root key**, pinned once per computer.
- A **new device is approved by a device already in the account**; the service alone can never add one.
- Several users, later: each account has its own boxes, services and rights; a new account by invitation only.

## Keys

The service must never be able to make someone an operator: an operator commands every daemon (starting agents
without prompts: running code). So the trust stays in keys the service does not hold.

- **Device keys**: each console (and later each CLI or IDE of the user) makes its own key pair; it never leaves its
  device, kept in the system's keyring (Secret Service) or DPAPI, else a file only its user reads (as the console's
  operator key is today). Using it takes the device and its user's session.
- **Root key** (Ed25519): made on the account's first device, kept there in the same store, plus a **recovery export**
  made at creation (sealed with a passphrase: scrypt and ChaCha20-Poly1305, as `--export-key` today), kept by the user
  offline. Its fingerprint is the account's identity: `account id = "a" + base32(sha256(root sign_pub))[:12]`.
- **Device chain**: an append-only list of signed links, each naming the previous one by its hash:
  `{seq, prev, ts, op: "add" | "revoke" | "head", device: {id, sign_pub, box_pub, label, host}, by: <device id or
  "root">, sig}`. The first link adds the first device, signed by the root key. Any device of the chain may sign the
  next link (approving a new device, revoking a lost one); the root key may always (recovery). The service keeps the
  chain (`/v1/account/chain`) and checks each new link (signature, `prev`, a signer in the chain and not revoked) but
  cannot write one: it holds no key of it.
- **Daemons** pin the account's root fingerprint (`mmbox.py daemon operator --account <fingerprint>`, asked at
  install). An `op` is accepted when its signer is a device of a chain that starts at the pinned root and is not
  revoked. The daemon fetches the chain from the service (cached, refreshed each hour and when an unknown signer
  appears) and verifies every link itself.
- **Withheld revocations**: a service could hide a revocation link. Each device re-signs the chain head every day
  (`op: "head"`); a daemon refuses a chain whose newest head is older than 7 days.

## A new device

1. The new console (or `mmbox.py account join <account id>`) makes its device key and posts a join request to the
   service (its public keys, label, host) with a 6-digit code it shows.
2. Every console of the account that is open gets it in its tray and its header: "Approve <label> on <host>, code
   123456?". The user checks the code matches and approves: that console signs the `add` link; the new device belongs.
3. No device of the account at hand: the recovery export (root key) signs it (`account recover <file>`).
4. Lost device: any other device revokes it (`revoke` link), or the root key.

Optional later: a passkey or a hardware key (WebAuthn) on the device, asked before a sensitive op (a session started
on another computer, a device approved).

## Several users (later)

- An account owns the boxes its devices made (their machine boxes and sessions), the services it registered, its
  peers and pairings; its consoles see only that.
- Per service: `admin` (deploys it, invites, sets roles), `operator` (questions, collector, ops to the daemons of
  computers that pinned its root), `member` (its boxes only). `MMBOX_ADMIN_ACCOUNTS` (root fingerprints) names the
  first admin; the rest is set from the console.
- A new account only by invitation: a one-use invitation signed by an admin's device, valid 24 h, carried by the
  admin to the new user; the service registers an account only with one.

## Order of work

1. The device chain and the root key, in the client and the console (made, approved, revoked, verified), and the
   daemon trusting a pinned root: on the bench.
2. The service: chain storage and checks (`/v1/account/*`), join requests; the console's approvals (tray, header,
   dialog), the daily head.
3. The move: the current operator key becomes the first device of lp177's account, its root made then (with its
   recovery export); `MMBOX_OPERATORS` and the pinned operator fingerprints are replaced by the account's root; the
   export/import commands go.
4. Later: several users (invitations, ownership, roles), WebAuthn.

## As built: step 1 (2026-11-08)

- `client/mmbox_account.py`: links, `verify` (every link, the pinned root, no revoked signer, a revoked device never
  back, stale chain refused), `newer` (a cache only moves forward: no fork, no going back), a selftest run by
  `mmbox.py selftest`.
- Console: `--account-create RECOVERY_FILE` (root key in the keyring or DPAPI as `root`, beside the device's own key
  `identity`; its recovery export sealed with a passphrase of 16 characters at least), `--account-join FILE` (a request
  and its 6-digit code), `--account-approve FILE --code N` (the code must match), `--account-complete FILE`,
  `--account-revoke DEVICE`, `--account-head` (also signed daily by the running console), `--account-show`,
  `--account-chain FILE`. Every `op` carries the account's chain.
- Client and daemon: `mmbox.py daemon operator --account <root fingerprint> [--chain FILE]` pins an account
  (`accounts.json` in the mmbox folder); an `op` from a device of it is opened with the device's keys from the cached
  chain, the chain it carries updates the cache (forward only), then the sender must be in the fresh chain and not
  revoked. The pinned operator key keeps working beside it until the move (step 3).
- Tested on the bench: a computer pinning only the account obeys device A; B joins (wrong code refused, right code
  approved by A); B is obeyed once a device carried the new chain; after A revokes B and carries it, B is not.
- Until step 2: the daemons learn a new chain only from the ops of known devices (a new device is obeyed after a known
  device sent something; a revocation once a device sent something); two devices signing heads at once may fork (the
  daemon then keeps its chain): the service will hold the chain and order its links. A device sends ops only if the
  service lets it write to the daemon's box (today: operators of the service).
