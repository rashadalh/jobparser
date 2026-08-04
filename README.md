# Resume Job Matcher

Upload your resume and get back only the jobs you actually qualify for.

Most job boards show you everything that matches a keyword. This does the opposite. It
searches live postings, opens each one, reads the full job description, and compares it
against your resume. A job only reaches your feed if the description was genuinely
retrieved and your resume meets what it asks for. Every match comes with the quotes from
your resume that back it up, so you can see why it thinks you're a fit and disagree if
you're not.

Everything it rules out is still there in an audit panel, with the reasoning, so you can
tell the difference between "nothing matched" and "something went wrong."

## What you can do with it

- **Upload a resume** (PDF, DOCX, or plain text) and run a search.
- **Reuse a resume** you've already uploaded. Parsing is the slow part, so a saved resume
  starts searching immediately.
- **Steer the search.** Choose which locations to cover, whether to include nationwide
  results, how recent a posting has to be, and whether recruitment agency listings count.
- **Push back.** If a match is wrong, or a rejection is wrong, say so. Your feedback is
  distilled into a short list of notes the matcher applies to your next run. You can read
  that list, add to it, and delete anything you disagree with.
- **See what it cost.** Each run reports what it spent on model calls, in dollars.

## Getting started

You'll need:

- [uv](https://docs.astral.sh/uv/) for the Python side. It fetches Python 3.11 for you.
- [bun](https://bun.com/) for the web side. No separate Node install needed.
- An [OpenRouter](https://openrouter.ai/) API key, for the models that read resumes and
  job descriptions.
- An [Adzuna](https://developer.adzuna.com/) `app_id` and `app_key`, for finding jobs.

### With Docker (simplest)

```bash
cp .env.example packages/api/.env     # then fill in your three keys
docker compose up --build
```

Open http://localhost:3000.

### Running it directly

```bash
# API
cd packages/api
uv sync
uv run playwright install chromium     # needed for job pages that render with JavaScript
# on Linux, also: uv run playwright install-deps
cp ../../.env.example .env             # config.py reads packages/api/.env specifically
uv run uvicorn jdparser.server:app --port 8000

# web, in a second terminal
cd packages/web
bun install
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8000' > .env.local
bun run dev
```

Open http://localhost:3000.

There's also a command line version, handy for a quick check without the browser:

```bash
cd packages/api && uv run python -m jdparser path/to/resume.pdf
```

## Your first run

Upload a resume and give it a few minutes. It's opening and reading real job pages one by
one, so a run usually takes two to three minutes, and the page updates as it goes.

When it finishes you should see job cards with the company, the title, a link to the real
posting, and a quote from your resume for each requirement it counted as met. If a job
card looks wrong, "Not a fit?" tells the matcher why, and it'll remember for next time.

Underneath, the audit panel accounts for everything else: jobs whose description couldn't
be retrieved, jobs the judge wasn't confident enough about, and jobs filtered out before
evaluation as being in a different field. Nothing disappears silently.

Upload the same resume a second time and it will say it reused your saved profile,
skipping the slowest step.

## Tuning

Each model call is configurable through environment variables, if you want to spend more
on a better answer or less on a faster one. Set any of
`LLM_MODEL_<NODE>`, `LLM_TEMP_<NODE>`, `LLM_MAX_TOKENS_<NODE>`, or `LLM_REASONING_<NODE>`,
where `<NODE>` is one of `PROFILER`, `PLANNER`, `JD_PARSER`, `JUDGE`, `SCREENER`, or
`FEEDBACK`.

For example, `LLM_REASONING_JUDGE=high` makes the fit judge think harder about each job.
`max_tokens` is a ceiling rather than a reservation, so raising it costs nothing unless
the extra room actually gets used.

Search breadth is tunable too. `SEARCH_PLAN_MAX_QUERIES` controls how many distinct role
searches it runs, and `SCREEN_EVAL_CAP` caps how many jobs get the expensive full
evaluation. Both are listed with the rest in `.env.example`.

## Good to know

A few things work simply on purpose, and are worth knowing before you rely on them:

- **It's built for one person on one machine.** There are no accounts or logins, and
  everything is stored under a single local user.
- **Your data stays in files on disk.** Resumes, saved profiles, and run history are JSON
  files under `packages/api/data/`, not a database. Under Docker they live in a volume
  called `jdparser-data`, and `docker compose down -v` deletes them.
- **US listings only.** Adzuna supports other countries, but the country is currently
  fixed.
- **Results arrive by refreshing, not streaming.** The page checks for progress every two
  seconds and gives up after five minutes. A run that takes longer than that keeps going
  on the server; the browser just stops watching.
- **Saved resumes are never cleaned up automatically.** Delete the files yourself if you
  want them gone.
- **Restarting mid-run loses that run.** Progress lives in memory, so a run interrupted by
  a restart won't resume.

Two setup notes that trip people up:

- `uv run playwright install chromium` has to happen after `uv sync`. Without it, any job
  page that needs JavaScript fails to load.
- When running under Docker, `NEXT_PUBLIC_API_BASE` is baked into the browser bundle when
  the image is built. If the browser needs to reach the API somewhere other than
  `http://localhost:8000`, change it in `docker-compose.yml` and rebuild the web image.
  Changing an API key only needs a restart: `docker compose up -d --force-recreate api`.

## How it works

The browser talks to a FastAPI service, which runs a [LangGraph](https://langchain-ai.github.io/langgraph/)
pipeline: read the resume, build a profile of the candidate, plan searches, query Adzuna,
drop anything obviously off-field, then for every remaining job resolve its real URL,
fetch the page, extract the description, pull out the requirements, and judge the fit.
The final yes or no is plain code, not a model, so the same evidence always produces the
same answer.

The language work runs on Gemini 3.1 Flash Lite through OpenRouter.

## Digging deeper

- `docs/system-overview.html` is an illustrated walkthrough of the pipeline.
- `PLAN.md` covers what the project is trying to do, and `SPEC.md` is the detailed
  contract every component is built against.
- `docs/IMPLEMENTATION_*.md` explain each area in depth, and every package under
  `packages/api/jdparser/` has a README describing what lives there.
