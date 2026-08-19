# Job Finder

Finds jobs for **any trade, in any country** — from a sentence, or from your CV —
and ranks them by your realistic chance of being shortlisted.

```bash
jobfinder daily --query "electrician jobs in Dubai"
```

You get a spreadsheet on your Desktop, best-first. It opens when the run finishes.

Everything runs on your machine. Your CV never leaves it except as text sent to
Anthropic's API under your own key, and your search terms go to the job boards
you have enabled — exactly as they would if you typed them into those sites.

---

## Quick start

Four steps. Takes about five minutes.

**1. Install**

```bash
git clone https://github.com/MajidAli2006/jobfinder.git
cd jobfinder
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
```

**2. Get one API key**

Go to **[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)**,
sign in, click **Create Key**, and copy it. It starts with `sk-ant-`.

This is the only key the tool actually needs.

**3. Put the key in a file called `.env`**

```bash
cp .env.example .env
```

Open `.env` in any text editor and paste your key after the `=`, with no quotes
and no spaces:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Save it. `.env` is git-ignored, so your key is never committed.

**4. Check it worked, then search**

```bash
.venv/bin/jobfinder setup
.venv/bin/jobfinder daily --query "warehouse jobs in Leeds"
```

> Tip: run `source .venv/bin/activate` once and you can drop the `.venv/bin/`
> prefix for the rest of your terminal session.

---

## Use it from Claude (MCP)

This tool is also an MCP server, so you can just ask Claude to search for you.

**Claude Code** — one command:

```bash
claude mcp add --scope user jobfinder -- /full/path/to/jobFinder/.venv/bin/jobfinder-mcp
```

Replace `/full/path/to/jobFinder` with wherever you cloned it. Run `pwd` inside
the folder to get it.

**Claude Desktop** — open `claude_desktop_config.json` and add:

```json
{
  "mcpServers": {
    "jobfinder": {
      "command": "/full/path/to/jobFinder/.venv/bin/jobfinder-mcp"
    }
  }
}
```

The config file lives at:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Restart Claude Desktop afterwards. **Cursor** and **Windsurf** use the same
`command` format in their own MCP settings.

Then just ask:

> *"Find me remote React contract work in Europe"*

Four tools are available: `check_setup` (confirm keys are working),
`preview_search` (see how a request was understood, before spending anything),
`find_jobs` (the full run — takes a few minutes and writes the spreadsheet), and
`list_platforms` (which job sites serve a country).

---

## API keys — what you need, and what you don't

**With no keys at all**, the tool still searches LinkedIn's public listings,
employer career boards (Greenhouse, Lever, Ashby, Workable, and others), ten
remote job boards, Hacker News "Who is hiring", and any regional board that
publishes standard job markup.

**With the Anthropic key** (step 2 above), it also understands free-text
requests, reads your CV, and judges eligibility and fit. Without it you can
still search, but you have to say what to look for in `candidate.local.json`
rather than in a sentence — see Troubleshooting.

Everything below is **optional**. Each one adds more job sites. Skip any of them
and the tool simply reports that source as unused — it never fails a run.

### Free keys, self-service

Sign up, copy the key, paste it into `.env`.

| Add to `.env` | Site | Where to get it |
|---|---|---|
| `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` | Adzuna (worldwide) | [developer.adzuna.com](https://developer.adzuna.com/) |
| `REED_API_KEY` | Reed (UK) | [reed.co.uk/developers](https://www.reed.co.uk/developers) |
| `JOOBLE_API_KEY` | Jooble (worldwide) | [jooble.org/api/about](https://jooble.org/api/about) |
| `CAREERJET_API_KEY` | Careerjet (worldwide) | [careerjet.com/partners/api](https://www.careerjet.com/partners/api/) |

### Reaching Indeed, Glassdoor, Bayt, Naukri and the rest

Those sites — plus Rozee and foundit — block direct requests with a CAPTCHA, but
all of them publish into Google's job index on purpose. So the way in is Google's
index, and several vendors sell licensed access to it.

**They all return the same listings**, because it's all Google's data. The choice
is price and free allowance, not coverage. Pick whichever you like and put its key
in `.env` exactly like the others — the tool uses whichever one it finds:

| Add to `.env` | Vendor | Where to get it | Notes |
|---|---|---|---|
| `SERPAPI_KEY` | SerpApi | [serpapi.com](https://serpapi.com/) | Free monthly allowance, paid beyond it |
| `SEARCHAPI_KEY` | SearchApi.io | [searchapi.io](https://www.searchapi.io/) | Same data, free allowance then paid |

**Use the variable that matches where you signed up.** The two are not
interchangeable: a SearchApi.io key in `SERPAPI_KEY` is rejected with
`401 Invalid API key`. SerpApi keys are 64 hex characters; SearchApi.io's are
shorter. If you get a rejection, check which site issued the key. Run
`jobfinder sources` and it will tell you which vendor it is using.

```
SERPAPI_KEY=your-key-here
```

Set only one. If both are present the first configured vendor is used, and
neither is required — without them the tool still runs, it simply skips those
sites and says so in the run summary.

If your country's main job board isn't in the free list above, this is the key
worth having: it reaches those sites in any country. Coverage does vary by
country and by how you word the search — Google's index has plenty for
"software engineer" in Pakistan and "full stack developer" in the UAE, and
nothing at all for some other combinations. An empty result is reported as
such, not as a broken key.

### Approval needed

`INDEED_PUBLISHER_ID`, `ZIPRECRUITER_API_KEY`, `SEEK_API_KEY`,
`STEPSTONE_API_KEY`, `BAYT_API_KEY`, `NAUKRI_API_KEY`, `ROZEE_API_KEY` — these
are partner programmes that must approve you first. Most people don't need them;
the SerpApi key reaches the same listings.

To see exactly which platforms serve your country and which keys they want:

```bash
jobfinder setup --region Nigeria
```

### Where to put keys

Any of these, whichever suits you:

1. A file you name yourself, via `JOBFINDER_ENV=/path/to/your.env`
2. `.env` in the folder you run the command from
3. `~/.jobfinder/.env` — a good choice if you want one set of keys for every project
4. `.env` in the project folder

**All of them are read, and they combine.** For a key set in more than one, the
one higher up this list wins; a key only the lower file has is still picked up.
So you can keep shared keys in `~/.jobfinder/.env` and per-project ones in the
project's `.env`.

Real environment variables beat every file, so `export ADZUNA_APP_ID=...` wins.
Note the reverse does not hold: unsetting a variable in your shell does *not*
hide a key that a `.env` file also defines. The format is one `KEY=value` per
line, no quotes:

```
ANTHROPIC_API_KEY=sk-ant-...
ADZUNA_APP_ID=12345678
ADZUNA_APP_KEY=abcdef...
```

---

## Everyday use

**Say what you want, in plain words.** No filters to configure:

```bash
jobfinder daily --query "plumber jobs in Lagos"
jobfinder daily --query "remote React contract, Europe"
jobfinder daily --query "part time warehouse work near Leeds"
jobfinder daily --query "graduate marketing internship, London"
```

**Or hand it your CV** and let it work out what you do:

```bash
jobfinder daily --cv ~/cv.pdf
jobfinder daily --cv ~/cv.pdf --query "only remote, minimum £45k"
```

The CV is read on your machine. Only the text is sent to Anthropic, to build
your search profile and score how well each advert fits.

**Useful flags:**

| Flag | What it does |
|---|---|
| `--days 7` | Only adverts posted in the last 7 days (default 30) |
| `--min-salary 60000` | Drop anything whose published pay is below this |
| `--require-salary` | Also drop adverts that publish no pay at all |
| `--quick` | A faster, shallower sweep — fewer detail fetches and fewer API calls |
| `--no-llm` | Rules only. No API calls, no cost |
| `--offline` | Run on bundled sample data — good for trying it out |
| `--no-open` | Don't open the spreadsheet when finished |
| `--output-dir PATH` | Write the reports somewhere else |
| `--region "USA, UK"` | Where you want to work. Read from your CV if omitted |
| `--deep` | A slower, more thorough sweep |
| `--no-verify` | Skip re-checking that each advert is still open |
| `--tier quick\|normal\|deep` | The same choice as `--quick`/`--deep`, named outright |
| `--sources a,b` | Restrict the run to named connectors — see `jobfinder sources` |
| `--small-only` | Only startups, scale-ups and mid-size firms |
| `--allow-low-rate-markets` | Keep roles scoped to markets that usually pay below your floor |
| `--no-prompt` | Never pause to ask for a missing key; skip those platforms |
| `-v`, `--verbose` / `-q`, `--quiet` | Show every step, or warnings and errors only. Available on every command |

Every flag above works for any country. `--region` accepts a country, a city,
a native name or a list — `"uae"`, `"Deutschland"`, `"Lagos"`, `"USA, UK"` all
resolve.

**On `--min-salary`:** an advert that publishes no salary is *kept*, flagged
"Pay not published", because it cannot be shown to be below your floor. Add
`--require-salary` if you would rather not see those at all. If your request
itself names a figure — `--query "electrician jobs, minimum $60k"` — adverts
with no published pay are moved to the Prospects sheet instead.

---

## What you get

A spreadsheet in `~/Desktop/job finder/`, with thirteen sheets: **Quick Apply**
(just the essentials), **Hot Leads**, **All Qualified Jobs**, then splits by
Full Time, Part Time, Contract, Freelance, Startups and Partnerships, plus
**Prospects** (eligibility unclear — worth asking), **Long Shots** (qualified,
but a low chance of a reply), **Companies & Contacts**, and a **Search Summary**
showing what was filtered and why.

The same data is written alongside it as `.csv`, `.json` and a browsable `.html` page.

**Match % is an estimate of being shortlisted, not keyword overlap.** Your CV fit
sets the ceiling; from there the estimate moves on what the advert reveals about
the contest. Every row shows its own arithmetic in the "Why this rank" column:

```
fit 87 × 1.05 = 91 — applicant count not published (-4%) · posted in the
last 24 hours (+3%) · scoped to United Kingdom, smaller pool (+6%) ·
applying straight into the employer's own system (+5%)
```

So a perfect match behind 200 applicants ranks below a good match nobody has
found yet — the honest answer about where your time goes.

---

## Other commands

```bash
jobfinder setup                 # which keys are set, which are missing
jobfinder setup --region India  # what serves a particular country
jobfinder sources               # every connector and its status
jobfinder sources --test        # live-check every configured key
jobfinder status                # what previous runs found
jobfinder platforms --region Kenya
jobfinder platforms --region Kenya --trade "solar installer"
jobfinder check --title "..." --description "..."   # why one advert passed or failed
```

`check` also takes `--company`, `--location` and `--url`, which let it judge the
employer, the eligibility and how you would apply rather than the wording alone.

To make one search your default so a bare `jobfinder daily` runs it, create
`candidate.local.json` in the project folder:

```json
{
  "home_country": "Nigeria",
  "default_search": {
    "label": "Electrical",
    "query": "electrician jobs in Lagos",
    "core_terms": ["electrician", "electrical"]
  }
}
```

It is git-ignored. Without it, a bare `jobfinder daily` asks what to look for
rather than guessing.

---

## Troubleshooting

**"I do not know what kind of work to look for"** — give it a `--query` or a
`--cv`. It won't invent a search for you.

**"A custom search needs the Claude judgement layer"** — a free-text `--query`
has to be read by the model before it can be searched, so this needs
`ANTHROPIC_API_KEY`. The run stops with exit code 1 and writes no report. Either
set the key, or state the search yourself in `candidate.local.json` as shown
below.

**No jobs found** — widen the window with `--days 30`, check your country is
spelled in full, and run `jobfinder setup --region <your country>` to see whether
the sites that serve you need a key you haven't set.

**"ANTHROPIC_API_KEY is not set"** — the `.env` file isn't where the tool is
looking, or the key has quotes around it. Run `jobfinder setup` to see what it
found. Remember the file must be named `.env`, not `env` or `.env.txt`.

**Nothing happens on Windows** — install with `pip install -e ".[all]"` rather
than running from source directly; Windows needs the bundled `tzdata` package.

**Want to see it work before setting up any keys?** A free-text `--query` needs
the Anthropic key, because something has to read your sentence and turn it into
a search. To run with no keys at all, hand it the search directly — put this in
`candidate.local.json` in the project folder:

```json
{
  "default_search": {
    "label": "Warehouse",
    "query": "warehouse operative",
    "core_terms": ["warehouse", "forklift"]
  }
}
```

then run it against the bundled sample adverts:

```bash
jobfinder daily --offline --no-llm
```

That writes a full spreadsheet without contacting anything.

---

## Development

```bash
.venv/bin/pip install -e ".[all,dev]"
.venv/bin/python -m pytest tests/ -q      # 661 tests, fully offline
.venv/bin/ruff check job_agent/ tests/
```

The tests need no keys and no network access.

---

## Privacy

Your CV file stays on your machine — it is read locally, and only the extracted
text is sent to Anthropic's API, under your own key, to build your search profile
and judge fit. Advert text goes to the same API for the same purpose, and nowhere
else.

Your search terms are sent to whichever job boards you have enabled, because that
is how searching them works — the same words you would type into those sites. With
no keys set, that means LinkedIn's public search and the open job boards. Run
`jobfinder sources` to see exactly which are active.

Nothing is sent to the author of this tool, and there is no telemetry. API keys
are read from `.env`, which is git-ignored, and are scrubbed out of logs and error
messages — a failed request that carries a key in its URL is redacted before it is
printed.
