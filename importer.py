"""Turn a pasted link into recipe text.

Supported sources:
  * Instagram posts/reels  - caption via the public embed page (no login needed)
  * TikTok videos          - caption via TikTok's public oEmbed endpoint
  * YouTube videos/shorts  - video description
  * Recipe websites        - schema.org Recipe data (JSON-LD), falling back to
                             the page's title/description

CLI, for testing a link without the server:
    python importer.py https://www.instagram.com/reel/XXXX/
"""
import html
import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 25

INSTAGRAM_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)")
YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})")


class ImportError_(Exception):
    """A link could not be turned into recipe text; the message is shown to the user."""


# ---------- safe fetching ----------

def _check_host(url):
    """Refuse non-http(s) URLs and hosts on the local network (this runs on a home server)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImportError_("Only http(s) links are supported.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror:
        raise ImportError_(f"Could not find the website {parts.hostname}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImportError_("Links to the local network are not allowed.")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects)


def fetch(url, headers=None):
    _check_host(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8,nl;q=0.7",
        **(headers or {}),
    })
    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            data = resp.read(MAX_BYTES + 1)
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raise ImportError_(f"The website answered with an error ({e.code}).")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise ImportError_(f"Could not reach the website ({getattr(e, 'reason', e)}).")
    return data[:MAX_BYTES].decode(charset, "replace")


def _strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</(p|li|div|h\d)>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _meta(page, name):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(name), page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(name), page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


# ---------- sources ----------

def instagram_code(url):
    m = INSTAGRAM_RE.search(url or "")
    return m.group(1) if m else None


def from_instagram(url, code):
    for kind in ("p", "reel"):
        page = fetch(f"https://www.instagram.com/{kind}/{code}/embed/captioned/", headers={
            # Instagram only renders the embed when the request looks like an iframe load.
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Referer": "https://example.com/",
        })
        m = re.search(r'<div class="Caption">(.*?)(?:<div class="CaptionComments|</div>)', page, re.S)
        if m:
            block = m.group(1)
            author = re.search(r'class="CaptionUsername"[^>]*>([^<]+)</a>', block)
            block = re.sub(r'<a class="CaptionUsername".*?</a>', "", block, count=1, flags=re.S)
            return {"platform": "instagram", "link": f"https://www.instagram.com/{kind}/{code}/",
                    "author": author.group(1) if author else "", "title": "", "text": _strip_tags(block)}
    raise ImportError_("Instagram did not return a caption. The post may be private, deleted or a "
                       "carousel without text. You can still paste the caption by hand.")


def from_tiktok(url):
    data = json.loads(fetch("https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url, safe="")))
    if not data.get("title"):
        raise ImportError_("TikTok did not return a caption for this video.")
    return {"platform": "tiktok", "link": url, "author": data.get("author_unique_id") or data.get("author_name", ""),
            "title": "", "text": data["title"]}


def from_youtube(url, video_id):
    page = fetch(f"https://www.youtube.com/watch?v={video_id}")
    m = re.search(r'"shortDescription":("(?:[^"\\]|\\.)*")', page)
    if not m:
        raise ImportError_("YouTube did not return a description for this video.")
    author = re.search(r'"ownerChannelName":("(?:[^"\\]|\\.)*")', page)
    return {"platform": "youtube", "link": f"https://www.youtube.com/watch?v={video_id}",
            "author": json.loads(author.group(1)) if author else "",
            "title": _meta(page, "og:title") or _meta(page, "title"),
            "text": json.loads(m.group(1))}


def _find_recipe(node):
    if isinstance(node, list):
        for item in node:
            found = _find_recipe(item)
            if found:
                return found
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Recipe" in types:
            return node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found = _find_recipe(node[key])
                if found:
                    return found
    return None


def _instructions(node):
    if isinstance(node, str):
        return [_strip_tags(node)]
    if isinstance(node, list):
        return [line for item in node for line in _instructions(item)]
    if isinstance(node, dict):
        if node.get("@type") == "HowToSection":
            return [f"{node.get('name', '')}:"] + _instructions(node.get("itemListElement", []))
        return [_strip_tags(node.get("text") or node.get("name") or "")]
    return []


def from_website(url):
    page = fetch(url)
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            recipe = _find_recipe(json.loads(block.strip()))
        except json.JSONDecodeError:
            continue
        if recipe:
            ingredients = [_strip_tags(str(i)) for i in recipe.get("recipeIngredient") or []]
            steps = [s for s in _instructions(recipe.get("recipeInstructions") or []) if s]
            parts = [_strip_tags(recipe.get("description") or "")]
            if ingredients:
                parts.append("Ingredients:\n" + "\n".join("- " + i for i in ingredients))
            if steps:
                parts.append("Method:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)))
            author = recipe.get("author")
            if isinstance(author, list):
                author = author[0] if author else ""
            if isinstance(author, dict):
                author = author.get("name", "")
            return {"platform": "web", "link": url, "author": author or urllib.parse.urlsplit(url).hostname,
                    "title": _strip_tags(recipe.get("name") or ""), "text": "\n\n".join(p for p in parts if p)}
    description = _meta(page, "og:description") or _meta(page, "description")
    title = _meta(page, "og:title") or _strip_tags((re.search(r"<title>(.*?)</title>", page, re.S | re.I) or [None, ""])[1])
    if not description and not title:
        raise ImportError_("No recipe found on this page.")
    return {"platform": "web", "link": url, "author": urllib.parse.urlsplit(url).hostname,
            "title": title, "text": description}


def fetch_source(url):
    """Return {platform, link, author, title, text} for a pasted link."""
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    code = instagram_code(url)
    if code:
        return from_instagram(url, code)
    host = urllib.parse.urlsplit(url).hostname or ""
    if host.endswith("tiktok.com"):
        return from_tiktok(url)
    yt = YOUTUBE_RE.search(url)
    if yt:
        return from_youtube(url, yt.group(1))
    return from_website(url)


def canonical_key(url):
    """Identity of a link, used to spot recipes that were already imported."""
    code = instagram_code(url)
    if code:
        return "ig:" + code
    yt = YOUTUBE_RE.search(url or "")
    if yt:
        return "yt:" + yt.group(1)
    parts = urllib.parse.urlsplit((url or "").strip())
    return (parts.hostname or "").removeprefix("www.") + parts.path.rstrip("/")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        try:
            print(json.dumps(fetch_source(arg), ensure_ascii=False, indent=1))
        except ImportError_ as e:
            print(f"{arg}: {e}")
