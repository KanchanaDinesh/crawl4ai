"""Company facts from a company's own site: social links, people, employees, contacts, GitHub numbers.

Fake sites and a fake GitHub API on localhost; no internet. LinkedIn/Facebook are never fetched,
only their links are kept.
"""

import asyncio
import json

from techintel import company as extract
from techintel.cli import run_stage
from techintel.stages.company import github_login
from test_pipeline_e2e import Site, page

JSON_LD = {"@context": "https://schema.org", "@graph": [
    {"@type": "WebSite", "name": "Acme"},
    {"@type": "Organization", "name": "Acme AI", "legalName": "Acme AI (Pvt) Ltd", "foundingDate": "2024",
     "founder": [{"@type": "Person", "name": "Nimal Perera"}],
     "numberOfEmployees": {"@type": "QuantitativeValue", "minValue": 11, "maxValue": 50},
     "address": {"streetAddress": "12 Galle Road", "addressLocality": "Colombo", "addressCountry": "LK"},
     "contactPoint": {"email": "hello@acme.lk", "telephone": "+94 11 234 5678"},
     "sameAs": ["https://www.facebook.com/acmeai", "https://www.instagram.com/acme.ai"]}]}

HOME = page("Acme AI | Machine learning for logistics",
            "<h1>Machine learning for logistics</h1>"
            "<a href='/about'>About</a> <a href='/team'>Team</a> <a href='/contact'>Contact</a>"
            "<a href='https://www.linkedin.com/shareArticle?url=x'>Share</a>"
            "<a href='https://www.linkedin.com/company/acme-ai/'>LinkedIn</a>"
            "<a href='https://twitter.com/intent/tweet?text=hi'>Tweet</a> <a href='https://x.com/acmeai'>X</a>"
            "<a href='https://github.com/acme-ai'>GitHub</a> Trusted by 10,000 people."
            "<blockquote>Great tool! Mary Brown, CEO of OtherCo</blockquote>",
            head=f"<script type='application/ld+json'>{json.dumps(JSON_LD)}</script>")
TEAM = page("Our team", "<h2>Meet the team</h2><div><h3>Kasun Silva</h3><p>Co-founder &amp; CEO</p></div>"
            "<div><h3>Dilini Fernando</h3><p>CTO</p></div>"
            "<a href='https://www.linkedin.com/in/kasun-silva-123/'>in</a> We are a team of 25 engineers.")
CONTACT = page("Contact", "Write to <a href='mailto:sales@acme.lk'>sales@acme.lk</a> or call "
               "<a href='tel:+94112345678'>us</a>. <img src='logo@2x.png'> Chairman: Ruwan Jayasuriya.")


def test_social_links_keep_the_companys_own_accounts():
    social, people = extract.social_links([
        {"href": "https://www.linkedin.com/feed/"}, {"href": "https://www.linkedin.com/company/acme/?trk=x"},
        {"href": "https://www.facebook.com/sharer/sharer.php?u=x"}, {"href": "https://facebook.com/acme"},
        {"href": "https://www.linkedin.com/in/jane-doe"}, {"href": "https://github.com/acme/repo"},
        {"href": "https://www.youtube.com/"}])
    assert social == {"linkedin": "https://www.linkedin.com/company/acme/", "facebook": "https://facebook.com/acme",
                      "github": "https://github.com/acme/repo"}
    assert people == ["https://www.linkedin.com/in/jane-doe"]
    assert github_login("https://github.com/acme/repo") == "acme" and github_login("https://github.com/orgs/x") == "x"
    assert github_login("https://github.com/sponsors") is None


def test_organization_data_people_contacts_and_employees():
    org = extract.organization(HOME)
    assert org["legal_name"] == "Acme AI (Pvt) Ltd" and org["founders"] == ["Nimal Perera"]
    assert org["employees"] == "11-50" and org["address"] == "12 Galle Road, Colombo, LK"
    assert org["email"] == "hello@acme.lk" and len(org["same_as"]) == 2
    assert extract.organization(page("x", "no data")) == {}
    assert {"name": "Kasun Silva", "role": "Co-founder & CEO"} in extract.people(TEAM)
    assert {"name": "Dilini Fernando", "role": "CTO"} in extract.people(TEAM)
    assert extract.people(CONTACT) == [{"name": "Ruwan Jayasuriya", "role": "Chairman"}]
    assert extract.people(page("x", "<h2>Our Team</h2><p>Founder</p>")) == []
    assert extract.contacts(CONTACT, "or mail info@acme.lk, see logo@2x.png") == {
        "emails": ["sales@acme.lk", "info@acme.lk"], "phones": ["+94112345678"]}
    assert extract.contacts(page("x", "<input placeholder='sarah@acme.com'>"), "sarah@acme.com") == {
        "emails": [], "phones": []}
    assert extract.employee_hint("We are a team of 25 engineers.") == "team of 25"
    assert extract.employee_hint("Over 120 employees in 3 offices") == "Over 120 employees"
    assert extract.employee_hint("Trusted by 10,000 people since 2019") is None


def test_company_stage_reads_the_site_and_github(settings, store):
    github_ok = True

    def user(path):
        return json.dumps({"login": "acme-ai", "type": "Organization", "public_repos": 14, "followers": 230,
                           "created_at": "2024-02-01T00:00:00Z"}) if github_ok else None

    github = Site({"/users/acme-ai": user})
    acme = Site({"/": HOME, "/about": page("About", "About Acme"), "/team": TEAM, "/contact": CONTACT})
    blocked = Site({"/robots.txt": "User-agent: *\nDisallow: /", "/": HOME})
    settings.raw["company"]["github_api"] = f"http://{github.domain}/users/{{login}}"
    for s in (acme, blocked):
        store.append("qualifications", [{"domain": s.domain, "url": f"http://{s.domain}/", "status": "qualified",
                                         "checked_at": "2026-11-03T00:00:00Z"}])

    summary = asyncio.run(run_stage("company", settings, store))
    assert summary["ok"] == 1 and summary["not_allowed"] == 1 and summary["github_numbers"] == 1
    row = store.latest("companies")[acme.domain]
    assert row["name"] == "Acme AI" and row["employees"] == "11-50" and row["founded"] == "2024"
    assert row["social"] == {"linkedin": "https://www.linkedin.com/company/acme-ai/", "x": "https://x.com/acmeai",
                             "github": "https://github.com/acme-ai", "facebook": "https://www.facebook.com/acmeai",
                             "instagram": "https://www.instagram.com/acme.ai"}
    assert {p["name"] for p in row["people"]} == {"Kasun Silva", "Dilini Fernando", "Ruwan Jayasuriya", "Nimal Perera"}
    assert row["emails"][:2] == ["hello@acme.lk", "sales@acme.lk"] and row["github"]["public_repos"] == 14
    assert row["linkedin_people"] == ["https://www.linkedin.com/in/kasun-silva-123"]
    view = store.con.execute("select linkedin, employees, github_repos from v_companies where domain = ?",
                             [acme.domain]).fetchone()
    assert view == ("https://www.linkedin.com/company/acme-ai/", "11-50", 14)
    assert asyncio.run(run_stage("company", settings, store)) == {"checked": 0}       # nothing due again yet


def test_github_refusal_is_not_saved_and_filled_in_later(settings, store, monkeypatch):
    from techintel.stages import company as stage
    refuse = {"on": True}

    async def github(session, api, login):
        if refuse["on"]:
            raise stage.GitHubRefused("GitHub's API said HTTP 403 (hourly limit reached)")
        return {"login": login, "public_repos": 3}

    monkeypatch.setattr(stage, "github_account", github)
    acme = Site({"/": HOME})
    store.append("qualifications", [{"domain": acme.domain, "url": f"http://{acme.domain}/", "status": "qualified"}])
    summary = asyncio.run(run_stage("company", settings, store))
    assert summary["ok"] == 1 and "hourly limit" in summary["github_refused"]
    assert store.latest("companies")[acme.domain].get("github") is None          # not saved as "no GitHub"
    refuse["on"] = False
    asyncio.run(run_stage("company", settings, store))                          # the next run fills it in
    assert store.latest("companies")[acme.domain]["github"]["public_repos"] == 3
