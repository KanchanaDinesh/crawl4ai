# The user platform: plan

_Written 2026-11-06 with Kanchana. Status: **phases 1 to 6 built (2026-11-06); grid: Tabulator; phase 7 is next.** Builds on the PostgreSQL move
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
| Grid | **Tabulator** (MIT), chosen by Kanchana after the grid test (see "The grid") | Airtable-like editing; selecting a block of cells and copy/paste are free |
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
| Grid | Tabulator 6.7 | MIT |
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

**Decision (Kanchana, 2026-11-06): Tabulator.** Selecting a block of cells is needed, and in AG Grid it's in the paid
edition. The table above is kept for the record; AG Grid is no longer in the project.

**Searchable multiple-choice list:** `DataGrid.pick` in `datagrid.js`, our own because Tabulator's list editor can't
search and multi-select at the same time. Search box on top, ticks, chosen values as removable chips, keyboard
(arrows, Enter ticks, Esc cancels, Tab or clicking away saves). Every multiple-choice field and the bulk edit bar use it.
**Users can add new choices** by typing them (Kanchana's decision): shared with everyone, one per name in any
capitals, no commas, at most 40 characters, each addition in the audit log.

**The grid sits behind our own small wrapper** (`webapp/static/js/datagrid.js`) with one interface:
load a dataset by name, show its fields from the catalog, save an edit, bulk edit, save a view. The pages and
the server never talk to the grid library directly, so switching grids later changes one file.

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

**Chosen: Tabulator** (Kanchana, 2026-11-06). `datagrid.js` keeps only Tabulator, and it now follows the app's
dark and light themes.

## Phase 2 results (2026-11-06)

**Catalog** (`webapp/catalog/`): `python -m webapp catalog_scan`, the **Scan now** button, and run_all step `catalog`
look at every table and view in the data schemas: 38 datasets, 653 fields, 6 seconds on the real database. Fields are
real columns and keys found inside JSONB columns, with type, how full, and a value list when there are at most 300
different values (looking at no more than 100,000 rows, so a value that only appears later can be missing from a
list until it's in the sample). What the scan finds updates by itself; names, descriptions and switches an
administrator set are kept. What disappears is marked gone, never deleted.

**Rights:** see and export per dataset and role, on the Dataset rights page. Known datasets start with Editor,
Analyst and Viewer seeing them, and Editor and Analyst exporting; internal ones (cursors, run logs, raw JSON tables)
start switched off; **a new unknown table stays with administrators until they grant it**.

**Datasets** (`webapp/explore/`): one page and one set of endpoints for every dataset. Filters under each heading:
text "contains", numbers (`>10`, `5..20`), dates (`7d`, `2026-11-01`, `>2026-11-01`, ranges), and the searchable tick
list for fields with few values. Search box, sort by several columns, move and resize columns, column chooser.
Rows load 100 at a time; the first page of 132,742 companies takes ≈50–100 ms, and the count isn't repeated while
scrolling. Requests name fields only by their catalog path: anything else is refused, values are always parameters,
and each query stops after 30 seconds with a message.

**Saved views:** columns, order, widths, sorting, filters and search under a name, private or shared, each with its own
link. Rights are checked again on opening, so sharing a view gives nobody a dataset they couldn't see.

**Exports:** CSV and Excel (openpyxl) of what's on screen, at most 200,000 rows, each one in the audit log. Text that
would run as a formula in Excel (starting with = + - @) is made plain text.

Known limits, for later: paging uses offsets, so very deep pages over millions of rows will slow down (keyset paging
when the GLEIF file comes); links between datasets aren't in the catalog yet (phase 5/7).

## Phase 3 results (2026-11-06)

**The dashboard is in Django** (`webapp/insights/`), at `/dashboard/news/`, `tech/`, `markets/`, `vehicles/`, `companies/`.
The aiohttp server and its 1,061-line `app.js` are gone; `python -m dashboard` only says where it went. `queries.py`
moved here unchanged (no Django in it, so the tools' `show` commands use it too).

**How a page works:** Python shapes the data, templates draw it, and every filter is in the address. A changed filter
asks HTMX for the rows only, and the address follows (`HX-Push-Url`); the count above a list comes in a header
(`HX-Trigger`). The period reloads the page, because counts and charts change too. `static/js/insights.js` (about 140
lines) does only what a page can't: draw charts, the side panel, clicks that set a filter, refresh, full screen.

**Charts:** Plotly for everything over time (`charts.py` builds plain figure dicts, plotly.js 4.1.2 basic bundle draws
them). **The plotly Python package isn't needed yet**, since a figure is just JSON; phase 6 can add it for the chart
builder. Ranked lists stay plain bars (labels are readable, and a click can filter), and sparklines plain SVG (dozens per
table). Colours follow the theme through `token:<name>`; the 8 series colours were checked with a palette validator in
both themes, and a line keeps its colour when another is removed (the old page repainted them).

**Same numbers:** compared with the old API on the real data, tab by tab with filters, sorting, paging and side panels:
291 checks identical. Two more differ only by the first point of a chart, which falls just before the 100-day
cut, the same point the old page cut in the browser.

**What's different on purpose:**
- **Rights:** a tab opens only if the role may see its main dataset (`common.TABS`). Otherwise the dashboard would be a
  way around the catalog's rights. The Markets tab's news panels need the World News tab.
- **Links from collected pages** must be http(s): 10 people read this, and a page's "website" could be `javascript:`.
- **Compare choices** are kept per user in the session, not per browser. Times are Sri Lanka time (`TIME_ZONE`), not
  each browser's: one setting for the team.
- If PostgreSQL is down, a page says what to do but no longer shows the last data it had (sign-in needs the database anyway).

Known limits: a tab's rights follow one dataset, not every table it reads. A role given only `news.news_items` also sees
source health and run history on that tab.

## Phase 4 results (2026-11-06)

**People can correct collected data, and a collector doesn't undo it.** Corrections go in the main tables, so the grid, the
dashboard, exports and the tools' `show` commands all see them. `warehouse/edits.py` is the part the web app and the
collectors share; `webapp/explore/editing.py` checks and saves values.

**What can be edited** (open question 1, the suggested answer): companies (name, country, website, industry; a website
brings its domain) and vehicle ads (title, price, location, year, mileage). News, prices and tech intel stay read-only.
`edits.TARGETS` is the ceiling: PostgreSQL grants the web app these columns and nothing else, and the catalog can offer
only these. Within that, an administrator switches fields on and sets the rule (Catalog → a dataset), and gives roles
Edit and Hide (Dataset rights). Editors start with both; Analysts and Viewers with neither. Editing needs a real
column: keys inside JSONB (`extra`) aren't editable yet, because collectors replace `extra` as a whole.

**Rule A in the collectors:** `audit.field_source` keeps the last value the source gave, per corrected field.
`CompanyStore.upsert_companies` and `VehicleStore.save_report` call `edits.protect_staged` / `protect_rows` before saving:

| The source says | Rule A (default) | Rule B | Rule C |
|---|---|---|---|
| the same as before, or nothing | correction stays | correction stays | correction stays |
| something new | **new value saved**; the correction stays in the history, one click to put back | correction stays | correction stays, the new value waits for a person |
| what the person said | the correction is no longer needed | same | same |

Vehicles keep one row per run, so a correction goes on the latest row and is carried to each new row. The site's own
report (the JSON file) isn't changed. A race is harmless: if an edit lands just as a run saves, the next run restores it.

**In the grid:** columns marked ✎: double-click, or paste (typing replaces the value; a website's link is an arrow, so a
double-click edits). A save carries the value the person saw: if it changed meanwhile, nothing is saved and they see the
new value. Select rows to set one value on all of them (all or none, at most 5,000; no "value I saw" here, since the
person chose to set them all) or to hide them. Corrected cells are underlined; a cell waiting for review is tinted.

**History:** `audit.field_history` has every change: who, when, old, new, why. A database trigger refuses changing or
removing it, even for its owner. Select one row → **History** shows what's corrected and every change with **Put back**, and
the rule C decision. **Changes** lists a dataset's latest changes and hidden rows. Collector changes appear only when they
replaced someone's correction (otherwise this would be millions of rows). Edits, bulk edits, hides and put-backs are in
the audit log too.

**Hiding:** `audit.hidden_records`; nothing is deleted, the collector keeps collecting, and the row stays hidden. The views
(`v_companies`, `v_vehicle_ads_latest`, `v_vehicle_price_changes`) leave hidden rows out, so the dashboard, CLIs and briefing
follow; the grid and exports also filter the plain tables. Shown again from **Changes**.

**Checked on a temporary copy of the real database** (deleted afterwards; the real history is empty), with real runs:
a corrected company stayed corrected through a real CSE collection (its last-seen moved, its name didn't); when the source
changed a website, the new value won and the domain followed; corrected ads stayed corrected through a real vehicles run;
hidden ads were collected again and stayed hidden. As the web app's login, updating `source` or deleting history is refused.

Known limits, for later: a corrected price counts in "price changes", as a real change would; the first cell of a grid
is selected on opening, so the selection bar shows "1 row selected" straight away; a role can be given Edit per dataset,
not per column.

## Phase 5 results (2026-11-06)

**The plan's test works:** make a Dealers table (or import it), add a **Dealer** link on Vehicle ads, pick dealers in the grid or
for many ads at once, and add a rollup on Dealers: each dealer shows its ad count and total asking price, live. It holds when the
vehicles collector runs again and a price changes.

**Own tables** (`webapp/userdata/`, menu **My tables**): private to their maker until shared with a role (See and export, or
Change). Make one, import a CSV or Excel file (columns and types shown before anything is saved), or press **Save as table** on any
dataset to copy what's on screen (needs the export right; at most 50,000 rows; a copy, it doesn't follow later changes).

**Own fields on anything whose rows have a key**, collected data included: a Status or Tags column on companies, beside the
collected values, where no collector touches them. Types: text, number, money, date, yes/no, one choice, several choices (people can
add a choice by typing it, shared with everyone; "Tag" and "tag" are one), web address, email.

**Links, lookups and rollups:** a link goes to rows of another table, and that table gets a column showing what links to each row;
either side can change it. "One linked row only" moves a row rather than doubling it. A lookup shows a field of the linked rows; a
rollup counts them, or adds up a number field (sum, average, smallest, largest). Rights follow: a link, lookup or rollup into a
dataset someone may not see isn't in their columns at all.

**How it's built, and why it needs little code:** a field is one catalog row, and every kind ends as an SQL expression
(`query.value_expr`), so the grid, filters, sorting, saved views, exports and later charts work with all of them without knowing the
kind. Values are JSONB, as the plan said, so adding a field changes no table: `userdata.records` (own tables' rows; each table
has a view, `userdata.t_xxx`), `userdata.extras` (own fields beside collected rows), `userdata.links`. The web app's login owns
schema `userdata` and made its tables, so **on a new server run `warehouse setup` before `webapp migrate`** (it says so if not).
Own-table rows and fields use the same history, Put back, conflict check and bulk edit as corrections. Removing a row or a field
keeps its data.

**Speed on the real data:** with a choice on 606 of the 132,742 companies, the first page takes 0.4 s, filtering by it 0.8 s and
sorting by it 0.4 s. Checked on a temporary copy of the database (deleted afterwards), with a real vehicles run.

**Found on the way:** on the web app's connection a JSON list arrives as text, and the grid couldn't draw it, which left **the whole
grid blank**. This had been wrong since phase 2 for any dataset with a list column on screen (Tech site profiles, Company websites,
Tech companies). Fixed for the grid and exports (`query.shown_value`), with a test.

Known limits, for later: a link shows a collected row's name as it was when linked (own tables' names stay current); own fields belong to
one dataset, so a Status on Companies isn't on Company records; anyone who can see a dataset sees its own fields; an Excel file's
first sheet only; a removed field or table has no "bring back" button yet (its data is kept).

## Phase 6 results (2026-11-06)

**The plan's test works:** build charts, put them on a dashboard, make a weekly report of it, and `./run_all.sh reports` writes the PDF
to `output/reports/`. Running it again the same day makes nothing, because nothing is due. On the real data's copy: 4 A4 pages, 64 KB, 1.5 s.

**Charts** (`webapp/charts/`, menu Charts): choose a dataset, what to group by, an optional split, what to work out (count, sum,
average, smallest, largest) and filters; a live preview draws it before anything is saved. Columns, bars, line, stacked area, doughnut,
one number, table. The **Chart** button on a dataset's page starts one with that grid's filters. A chart stores the choices, never SQL or
numbers: it's worked out when opened, for the person looking, from the same `query.value_expr` as the grid, so own fields, links,
rollups and hidden rows all behave, and a chart whose data someone may not see says so instead of showing. A list counts once
per item. At most 8 series (the palette's limit) and the biggest groups, with the rest added into Other where that makes sense (not for averages).

**Dashboards:** charts in a grid, each loading on its own; Arrange adds, orders, widens and removes. Private until shared.

**Reports** (`webapp/reports/`, menu Reports): a dashboard as a PDF: on demand or daily, weekly or monthly, with an opening text and the
numbers under each chart. WeasyPrint, as planned. **Charts in PDFs are drawn by `charts/svg.py`, not plotly.js**, because a PDF can't
run a script, and the Plotly route to static pictures needs a browser on the server. They use the same colours and numbers. A
report fetches nothing from outside (`no_fetching`), so text from collected pages can't make the server go anywhere.

**Schedule:** run_all step `reports` (in the default STEPS), `python -m webapp run_reports`. Because Kanchana runs things by hand, a report
is made at the first run after it's due: a missed one happens at the next chance, and the first one straight away. It runs with its
owner's rights; a person who has left gets none. A failure is kept on the report's page, the step fails, and it tries again next time.

**A PDF holds what its maker could see:** someone else can download it only if they may see all of its data.

**Needs Pango** (`brew install pango`, already on the Mac; `apt install libpango-1.0-0 libpangoft2-1.0-0` on a server). The app says so if
it's missing. Homebrew's folder is added when WeasyPrint loads (`making._weasyprint`), because setting it in the environment doesn't reach
every way a program gets started here.

Known limits, for later: nothing is emailed yet (the PDFs wait in `output/reports/` and on the report's page); a chart can't be copied into your
own, only shared; no custom report layouts beyond the dashboard's order; charts can't compare two datasets (a link and a rollup can do part
of that); the plotly Python package still isn't needed.

## How it fits together

```
 Collectors (Python, unchanged)              Django web app (Python)                      Browser
 news, tech, markets, vehicles,  ──writes──►  ┌─────────────────────────────┐  HTML+HTMX  ┌──────────────┐
 companies, scrapers                           │ accounts: users, roles, menus│ ──────────► │ pages, grid  │
        │                                      │ catalog: datasets + fields   │ ◄────────── │ (Tabulator),│
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
| `userdata` | people's own tables' rows, own fields beside collected rows, links | the app (it owns the schema); the reader can read it |
| `audit` | field history, what the source last said, hidden records (the sign-in and admin audit log is in `app`) | the app and collectors (and a database trigger as a safety net) |

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
| `dashboard/queries.py` (the SQL behind each tab) | **Moved as-is** to `webapp/insights/queries.py`: the functions take a connection and return dicts |
| `dashboard/server.py` (aiohttp) + `static/app.js` | **Retired (phase 3):** replaced by Django pages: templates + HTMX + Plotly |
| `briefing` | Unchanged; its output can also be shown in the app |
| Tests | Same suite; pytest-django added; the throwaway PostgreSQL fixture also serves Django |

**Layout:**

```
webapp/                    the Django project (python -m webapp / manage.py)
  settings.py, urls.py
  accounts/                users, roles, menus, rights, admin screens, login protection
  catalog/                 dataset registry, automatic scan, field settings, dataset rights
  explore/                 dataset pages, grid data, saved views, exports (phase 4 adds editing, history, rule A, hide)
  userdata/                own tables, own fields, imports, links
  charts/                  chart builder, dashboards (Plotly)
  reports/                 report templates, PDF (WeasyPrint), schedules
  assistant/               AI assistant (catalog only)
  audit/                   audit log, undo
  insights/                today's dashboard tabs (World News, Tech, Markets, Vehicles, Companies)
  templates/, static/js/datagrid.js, static/vendor/ (pinned htmx, plotly, grid files)
```

The old dashboard on http://localhost:8050 ran next to the Django app until every tab existed; since phase 3 there's
only the web app (port 8060).

## Phases

| # | Phase | Done when |
|---|---|---|
| 1 ✅ | **Foundation + grid test:** Django project in `webapp/`, schema `app`, login, users, roles, menu rights, admin screens, audit log; the AG Grid vs Tabulator test; the `datagrid.js` wrapper | An admin creates a user and a role; the user signs in and sees only the menus granted; grid chosen with the test's results written down |
| 2 ✅ | **Catalog + explore:** automatic catalog, dataset pages with the grid for every dataset, saved views, CSV/Excel export | Every dataset is browsable with filters and paging over 130,000 rows; a new collector table appears after a catalog scan |
| 3 ✅ | **Today's dashboard in Django:** the five tabs as Django pages (reusing `queries.py`, charts in Plotly); retire the aiohttp dashboard | Every tab shows the same numbers as today; `python -m dashboard` is replaced |
| 4 ✅ | **Editing:** inline and bulk edit, history, hide, rule A in the collectors, conflict check, column grants | An edit survives the next collector run unless the source really changed; every edit is in the history and can be undone |
| 5 ✅ | **Airtable-like:** choice and multi-choice fields, links, lookups, rollups, own tables, imports | A user links vehicle ads to a dealer table they made and sees the ad count per dealer |
| 6 ✅ | **Charts, dashboards, reports:** chart builder, dashboards, PDF reports, scheduled reports | A user builds a dashboard; a weekly PDF arrives in `output/reports/` |
| 7 | **AI assistant:** catalog-only SQL, preview, save as live dataset, limits, logging | "Sri Lankan companies in the news this month with LinkedIn" gives a correct, saved, updating dataset |
| 8 | **Server:** deployment with HTTPS, two-step login, backups there, monitoring | The team uses it from their own computers |

Each phase ends with tests passing, a real check on the Mac, an update to STATUS.md and a push to `main`.

## Open questions

1. **Answered in phase 4, with the suggestion below; Kanchana can change it.** Which datasets and fields may users edit at first? (Suggested: companies and vehicles; news and prices
   read-only.)
2. Extra roles beyond Administrator / Editor / Analyst / Viewer?
3. Log page views too, or only changes, exports and logins (suggested)?
4. Build on the Mac first and move to the server in phase 8 (suggested), or start on the server?
5. AI assistant: may it see the column names of users' private tables, or only shared datasets?

## Risks

- **The Mac's drive is almost full** (PostgreSQL keeps its data there): the server move matters for growth.
- **Rule A needs care in every collector**; the shared helper and tests keep it the same everywhere.
- **AI cost** grows with use; per-user daily limits and measuring the first weeks.
