#!/usr/bin/env python3
"""Call Jev (TypeSafe System One model) through OpenRouter's Decisions route.

Input: a JSON request {"state": ..., "questions": {...}} from a file argument or stdin.
Output: the raw Jev response plus a "_meta" block (wall-clock latency, cost, model, id).

The OpenRouter key comes from $OPENROUTER_API_KEY, $JEV_KEY_CMD or ~/.config/jev/key_cmd
(see README) and is never printed.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://openrouter.ai/api/alpha/decisions")
DEFAULT_MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")
RETRYABLE = {429, 502, 503, 524, 529}
try:
    KEY_CMD_TIMEOUT = float(os.environ.get("JEV_KEY_CMD_TIMEOUT", "5"))
except ValueError:
    KEY_CMD_TIMEOUT = 5.0


def api_key():
    """Resolve the OpenRouter key without ever storing it in a file this project owns.

    Order: $OPENROUTER_API_KEY, then $JEV_KEY_CMD (a command line that prints the key,
    run without a shell), then the command saved in $JEV_CONFIG_DIR/key_cmd
    (default ~/.config/jev/key_cmd). The key is never printed or logged.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    cmd = os.environ.get("JEV_KEY_CMD", "").strip()
    if not cmd:
        cfg = os.path.join(os.environ.get("JEV_CONFIG_DIR", os.path.join(HOME, ".config", "jev")), "key_cmd")
        try:
            cmd = open(cfg).read().strip()
        except OSError:
            cmd = ""
    if not cmd:
        sys.exit("jev: no OpenRouter key configured: set OPENROUTER_API_KEY or JEV_KEY_CMD, "
                 "or write a key command to ~/.config/jev/key_cmd")
    try:
        argv = [os.path.expandvars(os.path.expanduser(tok)) for tok in shlex.split(cmd)]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=KEY_CMD_TIMEOUT)
    except (OSError, ValueError) as e:
        sys.exit(f"jev: key command could not be run ({type(e).__name__})")
    key = out.stdout.strip()
    if out.returncode != 0 or not key:
        sys.exit(f"jev: key command failed (exit {out.returncode}) or printed nothing")
    return key


def call(payload, key, retries):
    body = json.dumps(payload).encode()
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "Claude Code - Jev skill",
        })
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, raw, time.perf_counter() - start, attempt
        except urllib.error.HTTPError as e:
            raw = e.read()
            elapsed = time.perf_counter() - start
            if e.code in RETRYABLE and attempt <= retries:
                wait = float(e.headers.get("retry-after") or 2 ** attempt)
                print(f"jev: HTTP {e.code}, retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return e.code, raw, elapsed, attempt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="?", help="JSON request file (default: stdin)")
    ap.add_argument("--model", default=None, help=f"default: {DEFAULT_MODEL}")
    ap.add_argument("--dry-run", action="store_true", help="print the payload that would be sent, send nothing")
    ap.add_argument("--retries", type=int, default=2, help="retries on 429/5xx (default 2)")
    ap.add_argument("--batch", metavar="ITEMS.json",
                    help="JSON array of states; asks the request's questions about each one in parallel")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests in --batch mode (default 8)")
    args = ap.parse_args()

    src = open(args.request) if args.request else sys.stdin
    payload = json.load(src)
    payload["model"] = args.model or payload.get("model") or DEFAULT_MODEL
    required = ("questions",) if args.batch else ("state", "questions")
    for field in required:
        if field not in payload:
            sys.exit(f"jev: request is missing '{field}'")

    if args.batch:
        with open(args.batch) as f:
            items = json.load(f)
        if not isinstance(items, list):
            sys.exit("jev: --batch file must be a JSON array of states")
        return run_batch(payload, items, args)

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    status, raw, elapsed, attempts = call(payload, api_key(), args.retries)
    text = raw.decode("utf-8", "replace")
    if status != 200:
        print(f"jev: HTTP {status} after {elapsed * 1000:.0f} ms (attempt {attempts})", file=sys.stderr)
        print(text)
        sys.exit(1)

    data = json.loads(text)
    usage = data.get("usage", {})
    data["_meta"] = {
        "elapsed_ms": round(elapsed * 1000),
        "attempts": attempts,
        "cost_usd": usage.get("cost"),
        "input_tokens": usage.get("input_tokens"),
        "model": data.get("model"),
        "provider": data.get("provider"),
        "id": data.get("id"),
    }
    print(json.dumps(data, indent=2, ensure_ascii=False))


def run_batch(template, items, args):
    """One request per item, same questions. Prints a JSON summary with per-item results."""
    from concurrent.futures import ThreadPoolExecutor

    payloads = [{**template, "state": item} for item in items]
    if args.dry_run:
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return

    key = api_key()

    def one(i):
        status, raw, elapsed, attempts = call(payloads[i], key, args.retries)
        text = raw.decode("utf-8", "replace")
        row = {"index": i, "status": status, "elapsed_ms": round(elapsed * 1000)}
        if status == 200:
            data = json.loads(text)
            row["answers"] = data.get("answers")
            row["cost_usd"] = data.get("usage", {}).get("cost")
            row["model"] = data.get("model")
        else:
            row["error"] = text
        return row

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(one, range(len(payloads))))
    wall = time.perf_counter() - start
    failed = [r for r in rows if r["status"] != 200]
    print(json.dumps({
        "results": rows,
        "_meta": {
            "items": len(rows),
            "failed": len(failed),
            "wall_ms": round(wall * 1000),
            "cost_usd": sum(r.get("cost_usd") or 0 for r in rows),
        },
    }, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
