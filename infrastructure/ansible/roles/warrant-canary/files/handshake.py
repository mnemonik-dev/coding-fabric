#!/usr/bin/env python3
"""MCP handshake check for the seller service (deploy-seller step 11).

Usage: handshake.py URL LABEL ENV_FILE
Credentials are read from MCP_AUTH_USER / MCP_AUTH_PASS in ENV_FILE (seller.env),
so they never appear on a command line or in the process environment.

1. initialize with basic auth and Accept: application/json, text/event-stream
   -> 200, mcp-session-id header, SSE data: line holding the label and the
   limits sentences.
2. notifications/initialized -> 202.
3. tools/list with the session id -> 200; the three deal tool descriptions
   start with the label.
4. initialize without auth -> 401.
"""
import base64
import json
import ssl
import sys
import urllib.error
import urllib.request

DEAL_TOOLS = ("propose_deployment", "deploy_with_escrow", "submit_acceptance")
LIMITS = ("One deal at a time.", "has not been exercised with money")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the credential must only go to the seller endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


OPENER = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def post(url, body, auth=None, session=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with OPENER.open(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode(errors="replace")


def sse_json(text):
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


def main(argv):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    url, label, env_file = argv[1], argv[2], argv[3]
    creds = {}
    with open(env_file) as fh:
        for line in fh:
            key, _, value = line.rstrip("\n").partition("=")
            creds[key] = value
    auth = f"{creds.get('MCP_AUTH_USER', '')}:{creds.get('MCP_AUTH_PASS', '')}"
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "warrant-canary-handshake", "version": "1"}}}
    problems = []
    status, headers, body = post(url, init, auth=auth)
    lower = {k.lower(): v for k, v in headers.items()}
    session = lower.get("mcp-session-id")
    if status != 200 or not session:
        print(f"initialize: status {status}, session id {'present' if session else 'missing'}", file=sys.stderr)
        return 1
    instructions = sse_json(body).get("result", {}).get("instructions", "")
    if label not in instructions:
        problems.append("initialize instructions lack the label")
    problems += [f"initialize instructions lack {s!r}" for s in LIMITS if s not in instructions]
    status, _, _ = post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, auth=auth, session=session)
    if status != 202:
        problems.append(f"notifications/initialized returned {status}, expected 202")
    status, _, body = post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, auth=auth, session=session)
    if status != 200:
        problems.append(f"tools/list returned {status}")
    else:
        tools = {t["name"]: t.get("description", "") for t in sse_json(body).get("result", {}).get("tools", [])}
        problems += [f"{name} description does not start with the label"
                     for name in DEAL_TOOLS if not tools.get(name, "").startswith(label)]
    status, _, _ = post(url, init)
    if status != 401:
        problems.append(f"initialize without auth returned {status}, expected 401")
    if problems:
        print("HANDSHAKE FAILED: " + "; ".join(problems), file=sys.stderr)
        return 1
    print("handshake ok: initialize 200 with session, initialized 202, tools/list 200 with labels, no auth 401")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
