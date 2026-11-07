# A site folder for mmbox

What makes a deployment yours (the host, the domain, who may reach it, your operator's console, where your secrets
are kept, your DNS) stays out of this repository, in a **site folder**. The scripts that do the work are the same for
everyone: `deploy/host/redis.sh` and `deploy/host/gateway.sh` (Redis 8 and the gateway as rootless Podman Quadlet
units, an nginx vhost in front). For containers without Quadlet or nginx, `deploy/mmbox-server.sh` does it in one
command instead.

## Make one

```sh
cp -r site.example site                     # ./site is ignored by git
mv site/site.env.example site/site.env      # fill it in
mv site/hooks.sh.example site/hooks.sh      # optional: secrets manager, DNS…
```

Keep it under version control **somewhere else**: a folder of your infrastructure's own repository, linked here
(`ln -s ~/infra/mmbox site`) or named by `MMBOX_SITE_DIR`. Nothing in it is a secret, but it describes your network.

## Deploy

```sh
deploy/site-deploy.sh redis      # once, then after a change of Redis' settings or of this repository's ACL
deploy/site-deploy.sh gateway    # after each update of the gateway (it builds from MMBOX_REPO / MMBOX_BRANCH)
deploy/site-deploy.sh --print gateway | less    # what would be sent to the host
```

Both are idempotent: an unchanged unit, image, ACL or vhost is left as it is, without a restart. A variable `MMBOX_*`
of your shell wins over `site.env` for one run (`MMBOX_BRANCH=test deploy/site-deploy.sh gateway`).

## What the host needs

Root over ssh (or `sudo` here), podman ≥ 4.7, `git`, `curl`, `openssl`, `jq`, a rootless user for the units
(`MMBOX_PODMAN_USER`, created once with lingering left to the script), and nginx with a certificate for
`MMBOX_DOMAIN` if you want the vhost.

## Hooks

`hooks.sh` may define, all optional, run on the host:

- `site_init`: before anything (load your secrets manager's environment, your DNS API's credentials).
- `secret_get NAME`, `secret_set NAME VALUE`, `secret_where`: where the two Redis passwords live. By default a
  root-only file on the host, `/etc/mmbox/secrets.env` (`MMBOX_SECRETS_FILE`).
- `site_post_gateway`: after the gateway and its vhost (a DNS record…).

The helpers `mmbox_log`, `mmbox_warn`, `mmbox_err`, `run_user` and `podu` (podman as the units' user) are there for
them.
