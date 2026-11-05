# The user platform: plan

_Written 2026-11-06 with Kanchana. Status: **phase 1 built (2026-11-06)**; the grid choice waits for Kanchana to try both. Builds on the PostgreSQL move
(`docs/postgres-migration.md`, finished)._

## What it is for

An internal web app, for about 10 people, on top of the data the collectors gather. Each person signs in and
can, depending on what the administrator gave them:

- explore any dataset in a fast grid (filter, sort, search, choose columns), Airtable style;
- **edit** data inline or many rows at once, with every change recorded (who, when, old value, new value);
- make their **own tables**, lists, tags and links between records, and save their own **views**;
- build **charts** and **dashboards**, **export** (CSV, Excel), and produce **PDF reports**;
- ask an **AI assistant** for a dataset in plain words ("Sri Lankan companies in the news this month, with
  their LinkedIn"); it writes the query, the system runs it.

The collectors keep running all the time; users always see new data as it arrives.

## Decisions taken

| Topic | Decision | Why |
|---|---|---|
| Language | **Python only** (no React, no Node.js build) | One language and codebase; PDF reports and charts are Python anyway; ~10 internal users |
| Web framework | **Django** | Logins, users, groups, permissions, sessions, CSRF, forms, admin, migrations built in; change history add-ons |
| Interactivity | **HTMX** (+ small plain JS where a library needs it) | Filters, paging, forms update part of a page without React; logic stays in Python |
| Grid | **AG Grid Community** (MIT) first; **Tabulator** (MIT) as the alternative, chosen by a test (see "The grid") | Airtable-like editing and selection; free for production use |
| Charts | **Plotly** (Python builds the figure, plotly.js draws it) | Rich interactive charts; the same figure exports to PNG/SVG for PDFs |
| PDF reports | **WeasyPrint** (HTML + CSS → PDF) | Reports are templates like the screens; preview on screen, download as PDF |
| Excel export | **openpyxl** on the server | No paid grid licence needed |
| Database | **PostgreSQL** (database `crawl4ai`), fixed core columns + **JSONB** for flexible fields | Already in place; joins across datasets; flexible like a document store |
| Catalog | **Built automatically** from the tables and the fields inside JSONB | New sources and fields appear without code |
| Edits vs collectors | **Rule A: the newest change wins** (configurable per field) | Data stays current; a correction isn't undone by the source repeating the same old value |
| AI assistant | **Sees the catalog only, never the data**; writes SQL; the server runs it read-only | Fewer tokens, privacy, no prompt injection from scraped text |

## Tools

| Part | Tool | Licence |
|---|---|---|
| Web app | Django (current LTS when we start) | BSD |
| Partial page updates | HTMX, django-htmx | BSD / MIT |
| Grid | AG Grid Community (v36.x when checked) or Tabulator (v6.5 when checked) | MIT |
| Charts | Plotly (Python) + plotly.js | MIT |
| PDF | WeasyPrint | BSD |
| Excel | openpyxl | MIT |
| Change history | django-simple-history (model tables) + our own audit log (generic datasets) | BSD |
| Login protection | django-axes (lockout after failed logins); later django-otp (two-step login) | MIT / Unlicense |
| Passwords | Argon2 (`django[argon2]`) | |
| Database driver | psycopg 3 (already used) | LGPL |
| AI | Anthropic API, Claude Opus 5.5 (`claude-opus-5-5`), via the official `anthropic` package | |
| Running in production | gunicorn behind Caddy or nginx with HTTPS (on the server) | |
| Tests | pytest + pytest-django, the existing throwaway PostgreSQL fixture | |

Versions are pinned in `pyproject.toml` / `uv.lock` when each part is added; JavaScript libraries are pinned files
in `webapp/static/vendor/` (no CDN needed in production).

## The grid

We will use grids heavily, so the choice matters, and it should be easy to change later.

**Checked on 2026-11-06 (from each project's own pages):**

| Grid | Licence | Free for our use | Range selection + copy/paste | Inline editing | Excel export | Notes |
|---|---|---|---|---|---|---|
| **AG Grid Community** | MIT | Yes ("including production use") | **No** (Enterprise) | Yes | **No** (Enterprise; we do it in Python) | Largest feature set, best performance; grouping, pivot, server-side row model are Enterprise |
| AG Grid Enterprise | Commercial, per developer | No | Yes | Yes | Yes | Only if we need it later |
| **Tabulator** | MIT | Yes | **Yes** (`selectableRange`, clipboard paste in "range" mode) | Yes (input, number, list with multiselect, date, checkbox, ...) | Yes (needs the SheetJS file) | Undo/redo built in; plain JS |
| RevoGrid (core) | MIT | Yes | Yes (copy/paste from Excel in core) | Yes | No (Pro) | Web component; Pro is paid |
| SlickGrid-Universal | MIT | Yes | Not confirmed | Yes | Yes | Plain-JS version exists |
| jspreadsheet CE | MIT | Yes | Copy/paste yes | Yes | | More a spreadsheet than a data grid |
| Handsontable | Commercial | **No** (non-commercial use only) | Yes | Yes | | Excluded |

**Decision:** start with **AG Grid Community**, and settle it with a short test in phase 1: build the same grid
(the companies list, 130,000 rows, with editing, multi-select, bulk edit, filters, saved views) with AG Grid
Community and with Tabulator, and judge:

1. speed with 100,000+ rows (server-side paging and scrolling);
2. editing feel: inline edit, multi-select, bulk edit, and copy/paste from Excel (free in Tabulator only);
3. custom cells: chips for tags and links, a record picker for links;
4. how much of our own code each needs.

**Whichever wins, it sits behind our own small wrapper** (`webapp/static/js/datagrid.js`) with one interface:
load a dataset by name, show its fields from the catalog, save an edit, bulk edit, save a view. The pages and
the server never talk to the grid library directly, so switching grids later (or buying AG Grid Enterprise)
changes one file.

## Phase 1 results (2026-11-06)

Built in `webapp/` (Django 5.2 LTS, django-htmx, django-axes, Argon2). Database: login `crawl4ai_app` owns schema
`app` (19 tables after `python -m webapp migrate`) and can read, not write, the collected data; the reader login
can't see `app`. Run it: `uv run python -m webapp runserver 8060` → http://localhost:8060.

- **Sign-in:** Argon2 passwords (10+ characters, common ones refused), 12-hour sessions, CSRF on every form;
  5 wrong passwords for a user name from one address lock it for 15 minutes (django-axes).
- **Roles and menus:** built-in Administrator, Editor, Analyst, Viewer (descriptions in the app); the administrator
  makes more and ticks which role may open which menu on one page. Menus of later phases already exist (marked
  "phase N"), so rights can be set up front. Every page checks its right on the server; the Administrator role
  always keeps the administration menus; an administrator can't deactivate themselves or drop their own role;
  built-in roles and roles with members can't be deleted; users are deactivated, never deleted.
- **Audit log:** sign-ins (and failures, lockouts), sign-outs, user and role changes, rights changes, grid test
  edits; a database trigger refuses UPDATE, DELETE and TRUNCATE on it; screen with filters by person, action, date.
- **First administrator:** `uv run python -m webapp createadmin` (asks for the password).
- Tests: `tests/test_webapp.py` (16) on pytest-django + the throwaway PostgreSQL.

### The grid test

`/grid-test/`: the 132,742 companies in both grids, loaded 100 rows at a time from PostgreSQL (≈110–130 ms per
page, almost all of it the row count; to be made faster in phase 2), with search, filters, sorting, inline
editing, multi-row selection with bulk edit, and a multiple-choice "Tags" field shown as chips. Test edits go to
their own table (never the collected data) and the audit log.

| | AG Grid Community 36.2 | Tabulator 6.7 |
|---|---|---|
| Speed (132,742 rows, paged) | smooth | smooth |
| Inline editing | yes | yes (double-click) |
| Select rows for bulk edit | checkboxes, shift-click | select a block of cells; its rows count as selected |
| **Select a block of cells, copy, paste (also from Excel)** | **no** (Enterprise only) | **yes, free**; pasting into read-only columns is undone, each pasted cell is saved and audited |
| Multiple-choice editor | had to write our own (Rich Select is Enterprise) | built in (list with multiselect) |
| Undo | yes | yes |
| Dark mode | follows the system | its stylesheet is light only (to theme) |
| Our code needed | less | more: paste handling, a fixed pixel height (found and fixed: "100%" made it load every page) |

**Leaning:** Tabulator, because spreadsheet-style selection and copy/paste are central to an Airtable-like
workspace and are free there; AG Grid has more polish but needs the paid edition for exactly those features.
**Kanchana decides after trying both** (then `datagrid.js` keeps only the chosen engine).

## How it fits together

```
 Collectors (Python, unchanged)              Django web app (Python)                      Browser
 news, tech, markets, vehicles,  ──writes──►  ┌─────────────────────────────┐  HTML+HTMX  ┌──────────────┐
 companies, scrapers                           │ accounts: users, roles, menus│ ──────────► │ pages, grid  │
        │                                      │ catalog: datasets + fields   │ ◄────────── │ (AG Grid),   │
        ▼                                      │ data: rows, edits, history   │  edits,     │ Plotly charts│
 ┌─────────────────────────────────────┐      │ views, charts, dashboards    │  filters    └──────────────┘
 │ PostgreSQL database crawl4ai          │ ◄──► │ reports (WeasyPrint)         │
 │ news, tech, markets, vehicles,        │      │ assistant (AI, catalog only) │ ──► Anthropic API
 │ companies, scrapers  (collected data) │      │ audit log                    │     (catalog + question only)
 │ app       (users, roles, views, ...)  │      └─────────────────────────────┘
 │ userdata  (users' own tables)         │
 │ audit     (who did what)              │
 └─────────────────────────────────────┘
```

## Data

### Schemas

| Schema | Holds | Written by |
|---|---|---|
| `news`, `tech`, `markets`, `vehicles`, `companies`, `scrapers` | collected data (as now) | collectors; the app only for user edits to editable fields |
| `app` | Django's own tables: users, roles, permissions, menus, catalog, saved views, charts, dashboards, report definitions | the app |
| `userdata` | users' own tables, link tables, tags | the app |
| `audit` | the audit log and field history | the app (and database triggers as a safety net) |

### Flexible records

- Core fields stay real columns (fast, typed); everything else is JSONB, so a new field needs no table change.
- A generic table `records(source, kind, key, collected_at, data JSONB)` lets a new collector start before its
  table is designed. A field used often gets an index, or becomes a real column with one statement.

### Edits and the collectors (rule A)

- Edits are made **in the main tables** (one table, one truth). Every change is kept in `audit.field_history`
  (dataset, record, field, old value, new value, who, when, source: user or collector).
- `audit.field_source` remembers, per edited field, the **last value the source gave**. When a collector brings a
  value: if it equals the last source value, the user's correction stays; if the source really changed it, the
  new value is saved (rule A) and the user's edit stays in the history, one click to restore.
- Per field the catalog can say instead: **user wins** (rule B) or **flag for review** (rule C).
- Collectors use one shared helper for this (in `warehouse/`), so each store doesn't implement it itself.
- **Hiding instead of deleting:** a hidden record stays hidden even when the collector sees it again.
- **Two people editing the same cell:** a save carries the value the user saw; if it changed meanwhile, the
  save stops and shows the new value.

### Database logins

| Login | Can |
|---|---|
| `crawl4ai` (collectors) | write the collected-data schemas |
| `crawl4ai_app` (Django) | read everything it shows; write `app`, `userdata`, `audit`; **update only the editable columns** of collected tables (PostgreSQL column grants, from the catalog) |
| `crawl4ai_reader` | read only (exports, reports, psql) |
| `crawl4ai_agent` | read only the data schemas and views, 30 s limit; never `app`, `audit`, users |

So a bug in the app can't damage the collected data beyond the editable fields, and the AI's queries can't
touch anything but data.

## The catalog

- A job after each collector run (and on demand) scans every dataset: real columns from PostgreSQL, fields
  inside JSONB (type, how often filled), value lists for fields with few values, row counts.
- Admins add labels, descriptions, links between datasets ("companies.domain = tech.candidates.key"), which
  fields are editable, the edit rule per field, and which roles see which fields.
- Everything that shows data (grid, filters, charts, exports, reports, AI) reads the catalog, so a **new
  dataset or field needs no new code**: one catalog entry, then grant it to roles.

## Users, roles and menu access

### Roles

Django groups, created and edited by the administrator in the app:

| Built-in role | Typical rights |
|---|---|
| **Administrator** | everything: users, roles, menus, catalog, audit log, settings |
| **Editor** | view, edit, create own tables, charts, dashboards, export, AI assistant |
| **Analyst** | view, charts, dashboards, reports, export, AI assistant; no editing collected data |
| **Viewer** | view and filter; export only if granted |

The administrator can make more roles (e.g. "Vehicles team") with any mix of rights. A user can have several
roles; their rights add up.

### What a right covers

Rights are given to a role on two kinds of things, each with actions:

| Thing | Examples | Actions |
|---|---|---|
| **Menu / module** | Dashboard, Datasets, My tables, Charts, Dashboards, Reports, AI assistant, Admin | open |
| **Dataset** | news, tech, markets, vehicles, companies, each user dataset | view, edit, hide, export, use in AI |

Later, if needed: **fields** a role can't see (e.g. emails, phones) and **row rules** (e.g. "Vehicles team sees
only vehicles"), both from the catalog.

### How it's enforced

1. **Menus:** the menu is built from the user's rights; items they can't open aren't shown.
2. **Every page and every data request** checks the right on the server (a view decorator / mixin), whatever
   the menu shows. Hiding a menu item alone is never the protection.
3. **The database logins** above, as the last line.

### Admin screens

- **Users:** invite, set roles, deactivate (never delete: their history stays), reset password, see last login.
- **Roles:** a grid of menus and datasets × actions with checkboxes.
- **Catalog:** datasets, fields, labels, editable fields, edit rules, links.
- **Audit log:** filter by user, dataset, action, date; undo an edit.

### Security

- Passwords stored with Argon2; lockout after repeated failed logins (django-axes); sessions in secure cookies;
  CSRF protection on every form and HTMX request; two-step login (django-otp) before going on the server.
- Every user action that changes or exports data is in the audit log: logins, edits, bulk edits, hides,
  imports, exports, shares, AI queries, report runs. Page views aren't logged (Kanchana's choice can change this).

## Features

1. **Explore:** any dataset in the grid: server-side paging, filters, sorting, column choice, quick search;
   every view has its own link.
2. **Edit:** inline cells; select rows, then bulk edit ("set status", "add tag"); a record's panel shows each
   field's source and history.
3. **Airtable-like fields:** single and multiple choice (tags as chips), links to records in other tables
   (with a picker), lookups and rollups (a linked record's field, counts and sums), URL, email, money, date, yes/no.
4. **Saved views:** filters, sorting, columns, grouping, private or shared.
5. **Own tables:** new tables in `userdata`, CSV/Excel import, "save this view as a table".
6. **Charts and dashboards:** a chart builder over any dataset or view (field, grouping, measure, chart type);
   the server turns it into safe SQL from catalog fields; dashboards arrange charts.
7. **Exports:** CSV and Excel of any view, logged, with a row limit.
8. **PDF reports:** report templates (HTML + CSS) with tables and Plotly charts, previewed on screen; on demand
   or scheduled (a `run_all.sh` step), e.g. a weekly market summary.
9. **AI assistant:** from the catalog only (with value lists and links), writes SQL; the server checks it (one
   read-only statement, allowed schemas) and runs it as `crawl4ai_agent`; the AI gets back only the row count or
   the error; the user sees the preview and the query in plain words; "save as dataset" makes a live query.
   Daily limit per user.
10. **Live updates:** after a collector run, open pages show "new data available".

## From today's code to Django

| Today | In the Django app |
|---|---|
| Collectors (`worldnews`, `techintel`, `markets`, `vehicles`, `companies`, `scrapers`), `run_all.sh`, schedules | **Unchanged.** They keep writing to PostgreSQL; only the shared rule-A helper is added |
| `warehouse/postgres.py` (connections, setup, migrate, backup) | Kept; Django's settings read the same `CRAWL4AI_*` settings; Django's own tables in schema `app` |
| `dashboard/queries.py` (the SQL behind each tab) | **Reused as-is** by Django views: the functions take a connection and return dicts |
| `dashboard/server.py` (aiohttp) + `static/app.js` | Replaced by Django pages: templates + HTMX + Plotly; retired when every tab exists in Django |
| `briefing` | Unchanged; its output can also be shown in the app |
| Tests | Same suite; pytest-django added; the throwaway PostgreSQL fixture also serves Django |

**Layout:**

```
webapp/                    the Django project (python -m webapp / manage.py)
  settings.py, urls.py
  accounts/                users, roles, menus, rights, admin screens, login protection
  catalog/                 dataset registry, automatic scan, field settings, links
  data/                    dataset pages, grid data, inline and bulk edit, history, rule A, hide
  views_saved/             saved grid views
  userdata/                own tables, imports, tags, links
  charts/                  chart builder, dashboards (Plotly)
  reports/                 report templates, PDF (WeasyPrint), schedules
  assistant/               AI assistant (catalog only)
  audit/                   audit log, undo
  insights/                today's dashboard tabs (World News, Tech, Markets, Vehicles, Companies)
  templates/, static/js/datagrid.js, static/vendor/ (pinned htmx, plotly, grid files)
```

The current dashboard keeps running at http://localhost:8050 until the Django app has every tab; the Django
app runs next to it during the build (e.g. port 8060).

## Phases

| # | Phase | Done when |
|---|---|---|
| 1 ✅ | **Foundation + grid test:** Django project in `webapp/`, schema `app`, login, users, roles, menu rights, admin screens, audit log; the AG Grid vs Tabulator test; the `datagrid.js` wrapper | An admin creates a user and a role; the user signs in and sees only the menus granted; grid chosen with the test's results written down |
| 2 | **Catalog + explore:** automatic catalog, dataset pages with the grid for every dataset, saved views, CSV/Excel export | Every dataset is browsable with filters and paging over 130,000 rows; a new collector table appears after a catalog scan |
| 3 | **Today's dashboard in Django:** the five tabs as Django pages (reusing `queries.py`, charts in Plotly); retire the aiohttp dashboard | Every tab shows the same numbers as today; `python -m dashboard` is replaced |
| 4 | **Editing:** inline and bulk edit, history, hide, rule A in the collectors, conflict check, column grants | An edit survives the next collector run unless the source really changed; every edit is in the history and can be undone |
| 5 | **Airtable-like:** choice and multi-choice fields, links, lookups, rollups, own tables, imports | A user links vehicle ads to a dealer table they made and sees the ad count per dealer |
| 6 | **Charts, dashboards, reports:** chart builder, dashboards, PDF reports, scheduled reports | A user builds a dashboard; a weekly PDF arrives in `output/reports/` |
| 7 | **AI assistant:** catalog-only SQL, preview, save as live dataset, limits, logging | "Sri Lankan companies in the news this month with LinkedIn" gives a correct, saved, updating dataset |
| 8 | **Server:** deployment with HTTPS, two-step login, backups there, monitoring | The team uses it from their own computers |

Each phase ends with tests passing, a real check on the Mac, an update to STATUS.md and a push to `main`.

## Open questions

1. Which datasets and fields may users edit at first? (Suggested: companies and vehicles; news and prices
   read-only.)
2. Extra roles beyond Administrator / Editor / Analyst / Viewer?
3. Log page views too, or only changes, exports and logins (suggested)?
4. Build on the Mac first and move to the server in phase 8 (suggested), or start on the server?
5. AI assistant: may it see the column names of users' private tables, or only shared datasets?

## Risks

- **The Mac's drive is almost full** (PostgreSQL keeps its data there): the server move matters for growth.
- **Grid limits:** AG Grid Community lacks range copy/paste and Excel export; covered by Python export and bulk
  edit, or by Tabulator if the test favours it.
- **Rule A needs care in every collector**; the shared helper and tests keep it the same everywhere.
- **AI cost** grows with use; per-user daily limits and measuring the first weeks.
