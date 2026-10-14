"""Fill in a language that existing recipes are missing, e.g. Dutch after it was added.

Talks to the running server's API rather than editing recipes.json, because the server keeps
the recipes in memory and rewrites the whole file on every save. Uses the same models as
imports (GEMINI_API_KEY etc.), else the free translation services, and marks the new texts
as machine translations until someone edits them.

    python backfill.py                        # Dutch for every recipe that lacks it
    python backfill.py --dry-run --limit 3    # show what would change
    docker compose exec recipebook python backfill.py   # in the deployed container

Set RECIPEBOOK_PASSWORD when the server has one.
"""
import argparse
import base64
import json
import logging
import os
import sys
import time
import urllib.request

import processing

log = logging.getLogger("recipebook")


def api(base, method, path, body=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    password = os.environ.get("RECIPEBOOK_PASSWORD", "")
    if password:
        headers["Authorization"] = "Basic " + base64.b64encode(f"backfill:{password}".encode()).decode()
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def source_lang(recipe, dst):
    """The language to translate from: the original one when it has text, else the first that does."""
    for lang in (recipe.get("origLang"), *processing.LANGS):
        if lang in processing.LANGS and lang != dst and recipe.get("text" + lang.capitalize()):
            return lang
    for lang in (recipe.get("origLang"), *processing.LANGS):  # no text anywhere: go by the title
        if lang in processing.LANGS and lang != dst and recipe.get("title" + lang.capitalize()):
            return lang
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=f"http://127.0.0.1:{os.environ.get('RECIPEBOOK_PORT', '8080')}")
    parser.add_argument("--lang", default="nl", choices=processing.LANGS)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many recipes")
    parser.add_argument("--pause", type=float, default=4, help="seconds between recipes (free-tier quotas)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    dst, cap = args.lang, args.lang.capitalize()

    recipes = api(args.url, "GET", "/api/recipes")["recipes"]
    todo = [r for r in recipes if not r.get("title" + cap) and not r.get("text" + cap) and source_lang(r, dst)]
    if args.limit:
        todo = todo[:args.limit]
    log.info("%d of %d recipes need %s; translating with %s", len(todo), len(recipes),
             processing.LANG_NAMES[dst], processing.ai_name() or "the free translation services")

    done = failed = 0
    for i, r in enumerate(todo, 1):
        src = source_lang(r, dst)
        s = src.capitalize()
        fields = {"title": r.get("title" + s, ""), "text": r.get("text" + s, ""), "note": r.get("note" + s, "")}
        try:
            out, via = processing.translate_fields(fields, src, dst)
        except RuntimeError as e:
            failed += 1
            log.warning("[%d/%d] %s: %s", i, len(todo), fields["title"], e)
            continue
        update = {"title" + cap: out["title"], "text" + cap: out["text"], "note" + cap: out["note"],
                  "machine": {**(r.get("machine") or {}), dst: True}}
        log.info("[%d/%d] %s -> %s (%s from %s)", i, len(todo), fields["title"], out["title"], via, src)
        if args.dry_run:
            print(out["text"][:400] + ("…" if len(out["text"]) > 400 else ""), end="\n\n")
        else:
            api(args.url, "PUT", "/api/recipes/" + r["id"], update)
        done += 1
        if i < len(todo):
            time.sleep(args.pause)
    log.info("%s %d recipes, %d failed", "Would update" if args.dry_run else "Updated", done, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
