"""Çok Lekker server: serves the page and stores recipes in data/recipes.json.

    python server.py                      # http://127.0.0.1:8080, this computer only
    python server.py --host 0.0.0.0       # reachable from phones on the home network

Set RECIPEBOOK_PASSWORD to require a password (HTTP basic auth, any user name).
"""
import argparse
import base64
import hmac
import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import importer
import processing

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PAGE = STATIC / "index.html"
# What the installable app needs next to the page: manifest, service worker, icons.
ASSETS = {"/manifest.webmanifest": "application/manifest+json", "/sw.js": "text/javascript; charset=utf-8"}
ASSETS |= {f"/icons/{p.name}": "image/png" for p in (STATIC / "icons").glob("*.png")}
DATA_DIR = Path(os.environ.get("RECIPEBOOK_DATA", ROOT / "data"))
DB_FILE = DATA_DIR / "recipes.json"
BACKUP_DIR = DATA_DIR / "backups"
PASSWORD = os.environ.get("RECIPEBOOK_PASSWORD", "")

EDITABLE = {"category", "tags", "link", "caption", "formula", "liked", "machine", "origLang"} | {
    field + lang.capitalize() for field in ("title", "text", "note") for lang in processing.LANGS}
CATEGORIES = set(processing.CATEGORIES) | {"inbox"}

log = logging.getLogger("recipebook")


class Store:
    """The recipe list in one JSON file, written atomically, with a daily backup."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        if path.exists():
            self.recipes = json.loads(path.read_text(encoding="utf-8"))["recipes"]
        else:
            self.recipes = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            BACKUP_DIR.mkdir(exist_ok=True)
            backup = BACKUP_DIR / f"recipes-{date.today().isoformat()}.json"
            if not backup.exists():
                shutil.copy2(self.path, backup)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"recipes": self.recipes}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def all(self):
        with self.lock:
            return list(self.recipes)

    def get(self, rid):
        return next((r for r in self.recipes if r["id"] == rid), None)

    def find_link(self, link):
        key = importer.canonical_key(link)
        return next((r for r in self.recipes if r.get("link") and importer.canonical_key(r["link"]) == key), None)

    def add(self, fields):
        with self.lock:
            recipe = {"id": uuid.uuid4().hex[:12], "liked": False, "addedAt": int(time.time()), **clean(fields)}
            self.recipes.append(recipe)
            self._save()
            return recipe

    def update(self, rid, fields):
        with self.lock:
            recipe = self.get(rid)
            if recipe:
                recipe.update(clean(fields))
                self._save()
            return recipe

    def delete(self, rid):
        with self.lock:
            before = len(self.recipes)
            self.recipes = [r for r in self.recipes if r["id"] != rid]
            if len(self.recipes) != before:
                self._save()
                return True
            return False


def clean(fields):
    """Keep only known fields with sane types."""
    out = {}
    for key, value in fields.items():
        if key in ("platform", "author", "hasRecipe", "importedWith"):
            out[key] = value
        elif key not in EDITABLE:
            continue
        elif key == "tags":
            out[key] = [str(t).strip() for t in value if str(t).strip()] if isinstance(value, list) else []
        elif key in ("formula", "liked"):
            out[key] = bool(value)
        elif key == "machine":
            out[key] = {k: bool(v) for k, v in value.items() if k in processing.LANGS} if isinstance(value, dict) else {}
        elif key == "category":
            out[key] = value if value in CATEGORIES else "inbox"
        else:
            out[key] = str(value or "").strip()
    return out


def has_text(recipe):
    """False for a link that was saved without its caption, which a new import may fill in."""
    return bool(str(recipe.get("caption") or "").strip()
                or any(str(recipe.get("text" + lang.capitalize()) or "").strip() for lang in processing.LANGS))


store = None


class Handler(BaseHTTPRequestHandler):
    server_version = "RecipeBox/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # ----- helpers -----

    def _authorized(self):
        if not PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                if hmac.compare_digest(given, PASSWORD):
                    return True
            except Exception:
                pass
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Cok Lekker", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _is_json(self):
        # Another site can make the browser post here with the login cookie attached,
        # but only as a form or text/plain body: a JSON body needs a CORS preflight,
        # which this server never answers. So writes must be JSON.
        return self.headers.get_content_type() == "application/json"

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _recipe_id(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        return parts[2] if len(parts) == 3 and parts[:2] == ["api", "recipes"] else None

    # ----- routes -----

    def do_GET(self):
        if not self._authorized():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path in ASSETS:
            self._send(HTTPStatus.OK, (STATIC / path.lstrip("/")).read_bytes(), ASSETS[path])
        elif path == "/api/recipes":
            self._send(HTTPStatus.OK, {"recipes": store.all(), "ai": processing.ai_name()})
        elif path == "/api/export":
            name = f"tarif-kutusu-{date.today().isoformat()}.json"
            self._send(HTTPStatus.OK, json.dumps({"recipes": store.all()}, ensure_ascii=False, indent=1).encode("utf-8"),
                       extra={"Content-Disposition": f'attachment; filename="{name}"'})
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_HEAD(self):
        self.send_response(HTTPStatus.OK if self.path.split("?")[0] in ("/", "/index.html") else HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not self._authorized():
            return
        if not self._is_json():
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
        path = self.path.split("?")[0]
        try:
            body = self._body()
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")

        if path == "/api/import":
            # "text" is the recipe pasted by hand, for websites that do not let us read them.
            url, text = str(body.get("url") or "").strip(), str(body.get("text") or "").strip()
            if not url and not text:
                return self._error(HTTPStatus.BAD_REQUEST, "No link given.")
            existing = store.find_link(url) if url else None
            if existing and has_text(existing):
                return self._send(HTTPStatus.OK, {"recipe": existing, "duplicate": True})
            try:
                source = importer.from_text(url, text) if text else importer.fetch_source(url)
            except importer.ImportError_ as e:
                return self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
            existing = existing or (store.find_link(source["link"]) if source["link"] else None)
            if existing and has_text(existing):
                return self._send(HTTPStatus.OK, {"recipe": existing, "duplicate": True})
            fields = processing.build_recipe(source)
            if existing:
                # A link saved earlier without its caption: fill in what is missing, keep what was typed.
                fill = {k: v for k, v in fields.items()
                        if not existing.get(k) or (k == "category" and existing[k] == "inbox")}
                fill["machine"] = {**fields.get("machine", {}), **existing.get("machine", {})}
                return self._send(HTTPStatus.OK, {"recipe": store.update(existing["id"], fill)})
            return self._send(HTTPStatus.CREATED, {"recipe": store.add(fields)})

        if path == "/api/recipes":
            return self._send(HTTPStatus.CREATED, {"recipe": store.add(body)})

        if path == "/api/translate":
            text, src, dst = str(body.get("text") or ""), body.get("from", "auto"), body.get("to", "en")
            if src not in (*processing.LANGS, "auto") or dst not in processing.LANGS:
                return self._error(HTTPStatus.BAD_REQUEST, "unsupported language")
            try:
                return self._send(HTTPStatus.OK, {"text": processing.translate(text, src, dst)})
            except RuntimeError as e:
                return self._error(HTTPStatus.BAD_GATEWAY, str(e))

        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PUT(self):
        if not self._authorized():
            return
        if not self._is_json():
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
        rid = self._recipe_id()
        if not rid:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        try:
            body = self._body()
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
        recipe = store.update(rid, body)
        if recipe:
            self._send(HTTPStatus.OK, {"recipe": recipe})
        else:
            self._error(HTTPStatus.NOT_FOUND, "recipe not found")

    def do_DELETE(self):
        if not self._authorized():
            return
        rid = self._recipe_id()
        if rid and store.delete(rid):
            self._send(HTTPStatus.OK, {"deleted": rid})
        else:
            self._error(HTTPStatus.NOT_FOUND, "recipe not found")


def main():
    global store
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("RECIPEBOOK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RECIPEBOOK_PORT", "8080")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    store = Store(DB_FILE)
    log.info("Çok Lekker: %d recipes, imports via %s", len(store.recipes),
             processing.ai_name() or "basic translation (no model configured)")
    processing.check_llm()
    if args.host not in ("127.0.0.1", "localhost") and not PASSWORD:
        log.warning("Listening on %s without RECIPEBOOK_PASSWORD: anyone on the network can edit recipes.", args.host)
    log.info("Open http://%s:%d", "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host, args.port)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
