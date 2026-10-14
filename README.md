# Çok Lekker

*Çok lekker* is Turkish *çok* (very) with Dutch *lekker* (tasty): "very tasty", mixed the way
Dutch-Turkish speakers mix the two languages.

A small recipe archive in Turkish, English and Dutch. Paste a link to an Instagram reel or post,
a TikTok, a YouTube video or a recipe website, and the server reads the recipe from the
caption or page and saves it. Recipes have favorites (♥), categories (lunch & dinner,
breakfast, snacks, dessert, plus a Counter (the `inbox` category) for unsorted ones), tags, search, and a
"What’s cooking?" button that suggests something, with favorites suggested more often.

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

## Better imports with a language model (optional)

Without one, an imported caption is machine-translated (Google Translate's free endpoint,
then MyMemory as a fallback) and the recipe goes to the Counter, where you pick its title and
category with Edit.

With a model, each import is rewritten into a clean "Ingredients / Method" recipe in all three
languages, with a title, category and tags, and lands directly in the right category.

**Free: Google Gemini.** Create a key at https://aistudio.google.com/apikey (no card needed)
and start the server with it. The free tier allows far more imports a day than anyone adds
recipes; Google may use free-tier prompts to improve its models, which for public captions
is usually fine.

```bash
GEMINI_API_KEY=... python server.py
```

**Any other OpenAI-compatible service** works the same way, for example Groq's free tier or a
local Ollama. `LLM_MODEL` can list several models, comma-separated, tried in order; on its own it
replaces Gemini's default of `gemini-flash-lite-latest,gemini-flash-latest` (Flash-Lite first,
because the full Flash model is often too busy on the free tier).

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1 LLM_API_KEY=gsk_... LLM_MODEL=llama-3.3-70b-versatile python server.py
LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen3:8b python server.py
```

**Claude (paid)** is used first when configured: each import is one Claude Opus 5.5 request,
typically a few cents.

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... python server.py
```

The server log says at startup which model imports use and, for Gemini and the like, whether
the key and model were accepted. If a model fails on an import (a used-up free quota, say),
that recipe falls back to the translated Counter import, so imports never stop working.

## Adding a language to existing recipes

Recipes saved before Dutch was added have no Dutch text. `backfill.py` translates them through
the running server's API (so it can run while the server is in use), one model request per
recipe, and marks the results as machine translations until someone edits them:

```bash
python backfill.py --dry-run --limit 3   # preview a few
python backfill.py                       # fill in every recipe that lacks Dutch
docker compose exec recipebook python backfill.py   # the same inside the deployed container
```

Without it, opening a recipe in Dutch offers "Automatisch vertalen" for that one recipe.

## Where things are

| Path | What |
|---|---|
| `server.py` | HTTP server and JSON API (`/api/recipes`, `/api/import`, `/api/translate`, `/api/export`) |
| `importer.py` | Reads recipe text from a link. Try a link with `python importer.py <url>` |
| `processing.py` | Turns that text into a Turkish/English/Dutch recipe (Claude, Gemini or another model, or translation fallback) |
| `backfill.py` | Translates existing recipes into a newly added language |
| `static/index.html` | The page |
| `data/recipes.json` | All recipes. A copy is kept per day in `data/backups/`; "Download backup" on the page exports it too |

## Limits

- Instagram is read through its public embed page, so private posts, deleted posts and some
  carousels return no caption. Use "Add by hand": paste the link, press "Show post", copy the
  caption into "Original caption" and press "Copy to TR + EN + NL".
- A reel whose recipe is only spoken in the video (not written in the caption) has no text to
  import; the same manual route applies.
- Instagram can change its embed page at any time. If imports start failing for every post,
  `importer.py` (`from_instagram`) is the place to look.
