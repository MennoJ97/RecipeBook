"""Turn a pasted link into recipe text.

Supported sources:
  * Instagram posts/reels  - caption from the post page's preview tags (no login needed)
  * TikTok videos          - caption via TikTok's public oEmbed endpoint
  * YouTube videos/shorts  - video description
  * Recipe websites, blogs - schema.org Recipe data (JSON-LD), falling back to
                             the article text (or the page's description)
  * Pasted text            - for sites that block us (Allerhande's bot check, ...)

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
# Recipe sites: Cloudflare turns away a "Chrome" that isn't one, but lets a bot that says what it is through.
SITE_UA = "Mozilla/5.0 (compatible; RecipeBox/1.0; personal recipe archive)"
MAX_TEXT = 12000  # characters of article text handed on when a page has no recipe data
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 25

INSTAGRAM_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)")
YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})")


class ImportError_(Exception):
    """A link could not be turned into recipe text; the message is shown to the user.

    blocked: the website refused us, so pasting the recipe text by hand is the way in.
    """

    def __init__(self, message, status=None, blocked=False):
        super().__init__(message)
        self.status = status
        self.blocked = blocked


BLOCKED_MESSAGE = ("This website does not let Çok Lekker read its pages. Open the recipe, copy its text "
                   "and paste it here instead.")


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
        if e.code in (401, 403, 429):
            raise ImportError_(BLOCKED_MESSAGE, e.code, blocked=True)
        raise ImportError_(f"The website answered with an error ({e.code}).", e.code)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise ImportError_(f"Could not reach the website ({getattr(e, 'reason', e)}).")
    return data[:MAX_BYTES].decode(charset, "replace")


def _strip_tags(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</(p|li|div|h\d)>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _meta(page, name):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=(["\'])(.*?)\1' % re.escape(name), page, re.I | re.S) \
        or re.search(r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=["\']%s["\']' % re.escape(name), page, re.I | re.S)
    return html.unescape(m.group(2)).strip() if m else ""


# ---------- sources ----------

def instagram_code(url):
    m = INSTAGRAM_RE.search(url or "")
    return m.group(1) if m else None


CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
# og:description looks like '157 likes, 10 comments - user on April 10, 2026: "caption".' (wording varies by locale)
OG_CAPTION_RE = re.compile(r'^[^"]*?: "(.*)"\.?\s*$', re.S)
OG_AUTHOR_RE = re.compile(r"instagram\.com/([\w.]+)/(?:p|reel|tv)/")


def _instagram_og(code):
    """Caption from the post page's preview tags, which Instagram still serves to link-preview crawlers."""
    page = fetch(f"https://www.instagram.com/p/{code}/", headers={"User-Agent": CRAWLER_UA})
    m = OG_CAPTION_RE.match(_meta(page, "og:description"))
    if not m or not m.group(1).strip():
        return None
    og_url = _meta(page, "og:url")
    author = OG_AUTHOR_RE.search(og_url)
    kind = "reel" if "/reel/" in og_url else "p"
    return {"platform": "instagram", "link": f"https://www.instagram.com/{kind}/{code}/",
            "author": author.group(1) if author else "", "title": "", "text": m.group(1).strip()}


def from_instagram(url, code):
    found = _instagram_og(code)
    if found:
        return found
    # The embed page used to carry the caption; by October 2026 it showed "post may have been removed".
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


# Pages a bot check puts in front of the real one (Cloudflare, Akamai, DataDome, PerimeterX).
CHALLENGE_RE = re.compile(r"cf-chl-|<title>Just a moment|sec-if-cpt-container|<title>Access Denied</title>"
                          r"|captcha-delivery\.com|px-captcha", re.I)
NOISE_RE = re.compile(r"<(script|style|noscript|svg|template|iframe|nav|header|footer|aside|form|button|select)\b.*?</\1\s*>",
                      re.S | re.I)


def _site_page(url):
    try:
        page = fetch(url, headers={"User-Agent": SITE_UA})
    except ImportError_ as e:
        if not e.blocked:
            raise
        page = fetch(url)  # a few sites only turn away bots they don't know
    if "application/ld+json" not in page and CHALLENGE_RE.search(page[:50000]):
        raise ImportError_(BLOCKED_MESSAGE, blocked=True)
    return page


def _article_text(page):
    """Readable text of a blog post without recipe data: the <article> (or <main>), minus menus and scripts."""
    body = NOISE_RE.sub(" ", re.sub(r"<!--.*?-->", "", page, flags=re.S))
    for tag in ("article", "main", "body"):
        m = re.search(r"<%s\b[^>]*>(.*)</%s>" % (tag, tag), body, re.S | re.I)
        if m:
            body = m.group(1)
            break
    body = re.sub(r"<li\b[^>]*>", "\n- ", body, flags=re.I)
    body = re.sub(r"</(section|tr|h\d|ul|ol|figure|blockquote)>", "\n", body, flags=re.I)
    lines, seen = [], set()
    for line in _strip_tags(body).split("\n"):
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if line == "-" or (len(line) > 40 and line in seen):  # "read more" blocks repeat the intro
            continue
        seen.add(line)
        if not line and lines and lines[-1].startswith("- "):
            continue  # keep list items together
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    if len(text) > MAX_TEXT:
        text = text[:text.rfind("\n", 0, MAX_TEXT) + 1 or MAX_TEXT].rstrip()
    return text.strip()


def from_website(url):
    page = _site_page(url)
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
    # A blog without recipe data: hand on the post itself, the model picks the recipe out of it.
    description = _meta(page, "og:description") or _meta(page, "description")
    title = _meta(page, "og:title") or _strip_tags((re.search(r"<title>(.*?)</title>", page, re.S | re.I) or [None, ""])[1])
    article = _article_text(page)
    text = article if len(article) > max(200, len(description)) else description
    if not text and not title:
        raise ImportError_("No recipe found on this page.")
    return {"platform": "web", "link": url, "author": urllib.parse.urlsplit(url).hostname,
            "title": title, "text": text}


def from_text(url, text):
    """Recipe text the user pasted, for a website that does not let us read it."""
    url = (url or "").strip()
    if url and not re.match(r"https?://", url):
        url = "https://" + url
    host = urllib.parse.urlsplit(url).hostname or ""
    if instagram_code(url):
        platform = "instagram"
    elif host.endswith("tiktok.com"):
        platform = "tiktok"
    elif YOUTUBE_RE.search(url):
        platform = "youtube"
    else:
        platform = "web"
    return {"platform": platform, "link": url, "author": host.removeprefix("www.") if platform == "web" else "",
            "title": "", "text": text.strip()[:MAX_TEXT * 2]}


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
