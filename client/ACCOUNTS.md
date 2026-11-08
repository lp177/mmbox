# Accounts, devices and the operator (design, not implemented yet)

Status: design of 2026-11-08 from lp177's decisions; Authentik deployed (deploy/host/authentik.sh), the rest to build. Replaces the single operator key (`MMBOX_OPERATORS`, the key
pinned by `collector enable --fingerprint`, `mmbox_console.py --export-key`) once built. Builds on PROTOCOL.md and
BUTLER.md.

## Decisions

- Sign-in through the browser, with **Discord or Google** (both offered), through a self-hosted **Authentik**.
- The daemons trust an account's **root key**, pinned once per computer.
- A **new device is approved by a device already in the account**; the service alone can never add one.
- Several users: each account has its own boxes, services and rights.

## Why a root key and approved devices

The service authenticates people (who signs in), but it must never be able to make someone an operator: an operator
commands every daemon (starting agents without prompts: running code). So the trust that matters stays in keys the
service does not hold:

- **Root key** (Ed25519): made on the account's first device, kept in that system's keyring or DPAPI, plus a
  **recovery export** made at creation (sealed with a passphrase, kept by the user offline). Its fingerprint is the
  account's identity: `account id = "a" + base32(sha256(root sign_pub))[:12]`.
- **Device keys**: each console (and later each CLI or IDE of the user) makes its own key pair; it never leaves its
  device. A device is in the account when the **device chain** says so.
- **Device chain**: an append-only list of signed links, each naming the previous one by its hash:
  `{seq, prev, ts, op: "add" | "revoke" | "role", device: {id, sign_pub, box_pub, label, host}, by: <device id or
  "root">, sig}`. The first link adds the first device, signed by the root key. Any device of the chain may sign the
  next link (approving a new device, revoking a lost one); the root key may always (recovery). The service keeps the
  chain (`/v1/account/chain`) and checks each new link (signature, `prev`, a signer that is in the chain and not
  revoked) but cannot write one: it holds no key of it.
- **Daemons** pin the account's root fingerprint (`mmbox.py daemon operator --account <fingerprint>`, asked at install,
  as the operator fingerprint is today). An `op` is accepted when its signer is a device of a chain that starts at the
  pinned root, is not revoked, and holds the operator role for that computer. The daemon fetches the chain from the
  service (cached, refreshed each hour and when an unknown signer appears) and verifies every link itself.
- **Withheld revocations**: a service could hide a revocation link. Each device re-signs the chain head with a fresh
  timestamp every day (`op: "head"`); a daemon refuses a chain whose newest head is older than 7 days (the service
  then shows a stale chain, it cannot fake a fresh one).

## Sign-in and approval of a new device

1. The console (or `mmbox.py login`) opens the browser at Authentik's authorization endpoint (OIDC, public client,
   PKCE), `redirect_uri=http://127.0.0.1:<console port>/oauth/callback` (Authentik accepts loopback redirects by
   regular expression). The user picks Discord or Google; the password manager, the provider's two-factor
   authentication and passkeys all apply there.
2. Back in the console: it exchanges the code for an ID token (PKCE, no client secret) and gives it to the service
   (`/v1/account/login`), which checks it against Authentik's keys (JWKS) and finds the account bound to that identity
   (`sub`, one or several: Discord and Google may both be bound to one account).
3. **First device of a new account**: the console makes the root key and its device key, signs the first link,
   writes the recovery export (it asks where and the passphrase), and the service binds the identity to the account.
4. **Another device**: the service knows the account; the new console posts a join request (its device keys, label,
   host, a 6-digit code it shows). Every console of the account that is open gets it in its tray and its header:
   "Approve <label> on <host>, code 123456?". Approving signs the `add` link; the new console then belongs. No device
   open: the recovery export (root key) can sign it.
5. The service gives each signed-in device a session (a token bound to its device key, renewed by signed requests); the
   end-to-end keys never depend on it.

## Roles and several users

- An account owns the boxes its devices made (their machine boxes and sessions), the services it registered, its
  peers and pairings. Its consoles see only that.
- Per service: `admin` (deploys it, invites accounts, sets roles), `operator` (questions, collector, ops to the daemons
  of computers that pinned its root), `member` (its boxes only). `MMBOX_OPERATORS` becomes `MMBOX_ADMIN_ACCOUNTS`
  (root fingerprints) for the first admin; the rest is set from the console.
- Who may create an account: an invitation from an admin of the service (an invitation link valid 24 h), or an
  allowlist of Discord or Google identities in Authentik; never anyone who signs in.

## Authentik

- Deployed beside a gateway (Podman Quadlets in the site folder, like the gateway: server, worker, PostgreSQL, Redis),
  at its own domain, its secrets in Infisical (database password, secret key, the Discord and Google OAuth client
  secrets).
- Sources: Google (OAuth client made in Google Cloud Console), Discord (application in the Discord Developer Portal),
  each with Authentik's callback URL; an enrollment flow limited to invited or allowlisted identities.
- One OIDC provider and application "mmbox": public client, PKCE, scopes `openid profile email`, loopback redirect
  URIs, short ID tokens; the gateway reads its JWKS.

## Order of work

1. The device chain and the root key, in the client and the console (made, approved, revoked, verified), and the
   daemon trusting a pinned root: on the bench, without Authentik (a local test login).
2. The service's accounts: chain storage and checks, sessions, account ownership of boxes, roles; the console's
   approvals (tray, header, dialog).
3. Authentik deployed (site folder, Infisical), the Discord and Google sources, the "mmbox" provider; the browser
   sign-in in the console and `mmbox.py login`.
4. The move: the current operator key becomes the first device of lp177's account; `MMBOX_OPERATORS` and the pinned
   operator fingerprints are replaced by the account's root; the export/import commands go.
