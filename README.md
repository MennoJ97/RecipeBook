# Tarif Kutusu · Recipe Box

A small bilingual (Turkish/English) recipe archive. Paste a link to an Instagram reel or post,
a TikTok, a YouTube video or a recipe website, and the server reads the recipe from the
caption or page and saves it. Recipes have favorites (♥), categories (lunch & dinner,
breakfast, snacks, dessert, plus an Inbox for unsorted ones), tags, search, and a
"What should I cook?" button that suggests something, with favorites suggested more often.

## Run it

```bash
git clone https://github.com/MennoJ97/RecipeBook.git
cd RecipeBook
python server.py
```

Then open http://localhost:8080. The server needs Python 3.10+ and nothing else.

The recipe archive itself is `data/recipes.json` and is committed in this repo, so it starts
with the ~100 recipes collected so far. The server rewrites that file whenever a recipe is
added, edited or liked; commit it now and then to keep a history.

To use it from a phone on the same Wi-Fi, listen on all interfaces and set a password:

```bash
RECIPEBOOK_PASSWORD=choose-something python server.py --host 0.0.0.0
```

(On Windows PowerShell: `$env:RECIPEBOOK_PASSWORD="choose-something"; python server.py --host 0.0.0.0`.)
Then open `http://<this-computer's-IP>:8080` on the phone; the browser asks for the password
(any user name works). Do not expose it to the internet without that password.

With Docker:

```bash
docker build -t recipebook .
docker run -d -p 8080:8080 -v ./data:/data -e RECIPEBOOK_PASSWORD=choose-something recipebook
```

## Better imports with Claude (optional)

Without an API key, an imported caption is machine-translated (Google Translate's free endpoint,
then MyMemory as a fallback) and the recipe goes to the Inbox, where you pick its title and
category with Edit.

With Claude, each import is rewritten into a clean "Ingredients / Method" recipe in both
languages, with a title, category and tags, and lands directly in the right category:

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... python server.py
```

The server log says at startup whether Claude is enabled. Each import is one Claude Opus 5.5
request, typically a few cents.

## Where things are

| Path | What |
|---|---|
| `server.py` | HTTP server and JSON API (`/api/recipes`, `/api/import`, `/api/translate`, `/api/export`) |
| `importer.py` | Reads recipe text from a link. Try a link with `python importer.py <url>` |
| `processing.py` | Turns that text into a bilingual recipe (Claude, or translation fallback) |
| `static/index.html` | The page |
| `data/recipes.json` | All recipes. A copy is kept per day in `data/backups/`; "Download backup" on the page exports it too |

## Limits

- Instagram is read through its public embed page, so private posts, deleted posts and some
  carousels return no caption. Use "Add by hand": paste the link, press "Show post", copy the
  caption into "Original caption" and press "Copy to TR + EN".
- A reel whose recipe is only spoken in the video (not written in the caption) has no text to
  import; the same manual route applies.
- Instagram can change its embed page at any time. If imports start failing for every post,
  `importer.py` (`from_instagram`) is the place to look.
