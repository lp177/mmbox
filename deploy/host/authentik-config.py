#!/usr/bin/env python3
"""Authentik's configuration for mmbox accounts (client/ACCOUNTS.md), through its API, idempotent: run by
deploy/host/authentik.sh after Authentik is ready, on the host (the API token never leaves it).

- the OIDC provider and application "mmbox": a public client (PKCE, no secret), redirects to the loopback only
  (http://127.0.0.1:<port>/oauth/callback: the console and `mmbox.py login`), scopes openid, email, profile;
- an enrollment flow on invitation only ("mmbox-enrollment"): nobody gets an account by signing in alone;
- the Discord and Google sources, each made once its client id and secret are in the environment
  (MMBOX_AUTH_DISCORD_CLIENT_ID / _SECRET, MMBOX_AUTH_GOOGLE_CLIENT_ID / _SECRET), shown on the sign-in page.

Environment: AK_URL (http://127.0.0.1:9110), AK_TOKEN (the admin's API token). Prints what it did, never a secret.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("AK_URL", "http://127.0.0.1:9110").rstrip("/") + "/api/v3"
TOKEN = os.environ["AK_TOKEN"]
REDIRECT = r"^http://127\.0\.0\.1:[0-9]{2,5}/oauth/callback$"


def api(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"API {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:600]}")


def one(path, **q):
    from urllib.parse import urlencode
    res = api("GET", f"{path}?{urlencode(q)}")["results"]
    return res[0] if res else None


def ensure(path, key, body, label):
    """The object whose `key` field equals body[key]: made, or updated to `body`."""
    cur = one(path, **{key: body[key]})
    if cur is None:
        obj = api("POST", path, body)
        print(f"{label}: made")
        return obj
    pk = cur.get("pk") or cur.get("slug")
    obj = api("PATCH", f"{path}{pk}/", body)
    print(f"{label}: up to date")
    return obj


def flow(slug):
    f = one("/flows/instances/", slug=slug)
    if not f:
        sys.exit(f"flow {slug} missing (a default of Authentik)")
    return f["pk"]


def main():
    scopes = [m["pk"] for m in api("GET", "/propertymappings/provider/scope/?page_size=100")["results"]
              if m.get("managed") in ("goauthentik.io/providers/oauth2/scope-openid", "goauthentik.io/providers/oauth2/scope-email",
                                      "goauthentik.io/providers/oauth2/scope-profile")]
    cert = one("/crypto/certificatekeypairs/", name="authentik Self-signed Certificate")
    provider = ensure("/providers/oauth2/", "name", {
        "name": "mmbox", "client_type": "public", "client_id": "mmbox",
        "authorization_flow": flow("default-provider-authorization-explicit-consent"),
        "invalidation_flow": flow("default-provider-invalidation-flow"),
        "redirect_uris": [{"matching_mode": "regex", "url": REDIRECT}],
        "property_mappings": scopes, "signing_key": cert["pk"] if cert else None,
        "sub_mode": "hashed_user_id", "include_claims_in_id_token": True,
        "access_code_validity": "minutes=1", "access_token_validity": "minutes=10", "refresh_token_validity": "days=30",
    }, "OIDC provider mmbox (public, PKCE, loopback redirects only)")
    ensure("/core/applications/", "slug", {"name": "mmbox", "slug": "mmbox", "provider": provider["pk"],
                                           "meta_description": "The mmbox console and client (accounts of the mailbox)"},
           "application mmbox")

    # enrollment on invitation only: the invitation stage refuses any enrollment without a valid invitation
    inv = ensure("/stages/invitation/stages/", "name", {"name": "mmbox-invitation", "continue_flow_without_invitation": False},
                 "invitation stage")
    write = ensure("/stages/user_write/", "name", {"name": "mmbox-enrollment-write", "user_creation_mode": "always_create",
                                                   "create_users_as_inactive": False}, "user write stage")
    login = ensure("/stages/user_login/", "name", {"name": "mmbox-enrollment-login"}, "user login stage")
    enroll = ensure("/flows/instances/", "slug", {"name": "mmbox enrollment (invitation only)", "slug": "mmbox-enrollment",
                                                  "title": "Join mmbox", "designation": "enrollment",
                                                  "authentication": "none"}, "enrollment flow mmbox-enrollment")
    have = {(b["stage"], b["order"]) for b in api("GET", f"/flows/bindings/?target={enroll['pk']}&page_size=50")["results"]}
    for order, st in ((10, inv), (20, write), (30, login)):
        if (st["pk"], order) not in have:
            api("POST", "/flows/bindings/", {"target": enroll["pk"], "stage": st["pk"], "order": order})
            print(f"  bound {st['name']} at {order}")

    # the sources, once their credentials are known; shown on the sign-in page (identification stage)
    sources = []
    for kind, title in (("discord", "Discord"), ("google", "Google")):
        cid = os.environ.get(f"MMBOX_AUTH_{kind.upper()}_CLIENT_ID")
        sec = os.environ.get(f"MMBOX_AUTH_{kind.upper()}_CLIENT_SECRET")
        if not cid or not sec:
            print(f"source {title}: waiting for MMBOX_AUTH_{kind.upper()}_CLIENT_ID and _SECRET in the site's store")
            continue
        src = ensure("/sources/oauth/", "slug", {
            "name": title, "slug": kind, "provider_type": kind, "consumer_key": cid, "consumer_secret": sec,
            "authentication_flow": flow("default-source-authentication"), "enrollment_flow": enroll["pk"],
            "user_matching_mode": "identifier", "policy_engine_mode": "any",
        }, f"source {title}")
        sources.append(src["pk"])
    ident = one("/stages/identification/", name="default-authentication-identification")
    if ident and sources:
        want = sorted(set(ident.get("sources") or []) | set(sources))
        if sorted(ident.get("sources") or []) != want:
            api("PATCH", f"/stages/identification/{ident['pk']}/", {"sources": want})
            print("sign-in page: Discord and Google buttons shown")
    disc = one("/providers/oauth2/", name="mmbox")
    print(f"issuer: {os.environ.get('AK_PUBLIC', '')}/application/o/mmbox/  client_id: mmbox  (provider {disc['pk']})")


if __name__ == "__main__":
    main()
