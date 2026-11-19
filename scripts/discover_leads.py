"""Discover agency leads with Exa Search and append them to data/leads/leads.csv.

Run: python scripts/discover_leads.py
"""

import csv
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from exa_py import Exa

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "leads" / "leads.csv"
SOURCE = "Exa Search"
RESULTS_PER_QUERY = 5
MAX_NEW_COMPANIES = 20

# Documented order, docs/lead-data-structure.md.
COLUMNS = [
    "company_name", "website", "country", "company_type", "employee_count",
    "description", "services_offered", "discovery_source", "source_url",
    "discovery_date", "lead_status",
]

# Exa may return headquarters country as a name or ISO code.
COUNTRY_ALIASES = {
    "Germany": {"germany", "de", "deu", "deutschland"},
    "United Kingdom": {"united kingdom", "uk", "gb", "gbr", "great britain",
                       "england", "scotland", "wales", "northern ireland"},
}


def load_config():
    """Read both configs and fail fast if they don't match what the script expects."""
    discovery = yaml.safe_load((ROOT / "config" / "discovery.yaml").read_text())["discovery"]
    market = yaml.safe_load((ROOT / "config" / "target-market.yaml").read_text())["target_market"]

    fields = [f["name"] for f in discovery["result_fields"]]
    if fields != COLUMNS:
        sys.exit(f"discovery.result_fields {fields} does not match the documented columns {COLUMNS}")
    for category in discovery["search_categories"]:
        if not category.get("name") or category.get("company_type") not in market["industries"]:
            sys.exit(f"Invalid search category {category}: company_type must be one of {market['industries']}")
    for country in discovery["countries"]:
        if country not in COUNTRY_ALIASES:
            sys.exit(f"No headquarters aliases defined for country '{country}'")
    for country, queries in (discovery.get("localized_queries") or {}).items():
        if country not in discovery["countries"]:
            sys.exit(f"localized_queries has '{country}', which is not in discovery.countries")
        for q in queries:
            if not q.get("query") or q.get("company_type") not in market["industries"]:
                sys.exit(f"Invalid localized query {q}: company_type must be one of {market['industries']}")
    return discovery, market


def build_queries(discovery):
    """(country, query, company_type) for every search: native-language where configured, else English."""
    localized = discovery.get("localized_queries") or {}
    searches = []
    for country in discovery["countries"]:
        if country in localized:
            searches += [(country, q["query"], q["company_type"]) for q in localized[country]]
        else:
            searches += [(country, f"{c['name']} in {country}", c["company_type"])
                         for c in discovery["search_categories"]]
    return searches


def competitor_matches(text, keywords):
    """Competitor keywords found in text, case-insensitive, hyphens treated as spaces."""
    def norm(s):
        return " ".join(s.lower().replace("-", " ").split())
    text = norm(text)
    return [k for k in keywords if norm(k) in text]


def domain_of(url):
    host = urlparse(url if "//" in url else f"//{url}").netloc.lower().split(":")[0]
    return host.removeprefix("www.")


def is_excluded_domain(domain, excluded):
    return any(domain == d or domain.endswith("." + d) for d in excluded)


def load_existing():
    """Return (existing domains, row count). Refuse to touch a file with an unexpected header."""
    if not OUTPUT.exists() or OUTPUT.stat().st_size == 0:
        return set(), 0
    # utf-8-sig: files saved by Excel start with a byte-order mark.
    with OUTPUT.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            sys.exit(f"{OUTPUT} header {reader.fieldnames} does not match {COLUMNS}; not modifying it.")
        rows = list(reader)
    return {domain_of(row["website"]) for row in rows if row["website"]}, len(rows)


def company_entity(result):
    """Exa's structured company data for a result, if it returned any."""
    for entity in result.entities or []:
        if getattr(entity, "type", None) == "company":
            return entity.properties
    return None


def verified_country(props, allowed):
    """The allowed country Exa reports as headquarters, or None if unknown/elsewhere."""
    hq = props.headquarters.country if props and props.headquarters else None
    if not hq:
        return None
    for country in allowed:
        if hq.strip().lower() in COUNTRY_ALIASES[country]:
            return country
    return None


def to_lead(result, company_type, discovery, market, today):
    """Return (row, None) for a valid lead, or (None, reason) if it is excluded."""
    props = company_entity(result)

    # Country comes only from Exa's headquarters data, never from the query.
    country = verified_country(props, discovery["countries"])
    if not country:
        return None, "headquarters not verified as Germany/United Kingdom"

    employees = props.workforce.total if props.workforce else None
    if employees is not None:
        size = discovery["company_size"]
        if not isinstance(employees, int) or not size["min_employees"] <= employees <= size["max_employees"]:
            return None, "employee count outside target range"

    name = props.name or (result.title or "").split("|")[0].split(" - ")[0].strip()
    if not name:
        return None, "no company name"

    highlights = " ".join(result.highlights or []).strip()
    description = props.description or highlights
    # ponytail: keyword match against target_services; misses synonyms, add an extraction step if that matters.
    page_text = f"{description} {highlights}".lower()
    services = [s for s in market["target_services"] if s.lower() in page_text]
    parsed = urlparse(result.url)

    return {
        "company_name": name,
        "website": f"{parsed.scheme}://{parsed.netloc}",
        "country": country,
        "company_type": company_type,
        "employee_count": employees if employees is not None else "",
        "description": " ".join(description.split()),
        "services_offered": "; ".join(services),
        "discovery_source": SOURCE,
        "source_url": result.url,
        "discovery_date": today,
        "lead_status": "new",
    }, None


def main():
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        sys.exit("EXA_API_KEY is not set. Copy .env.example to .env and add your Exa API key.")

    discovery, market = load_config()
    excluded_domains = discovery.get("excluded_domains", [])
    competitor_keywords = discovery.get("competitor_review_keywords", [])
    known, existing_count = load_existing()
    exa = Exa(api_key)
    today = date.today().isoformat()
    new_leads = []
    competitor_review = []  # (company_name, website, matched keywords)
    stats = Counter()
    exclusions = Counter()

    # ponytail: countries run in config order, so an early country can use the whole 20-company cap.
    for country, query, company_type in build_queries(discovery):
        if len(new_leads) >= MAX_NEW_COMPANIES:
            break
        stats["queries"] += 1
        try:
            response = exa.search(
                query,
                num_results=RESULTS_PER_QUERY,
                category="company",
                exclude_domains=excluded_domains,
                contents={"highlights": True},
            )
        except Exception as error:  # Network, auth, quota or API errors.
            stats["failed"] += 1
            print(f"[error] '{query}': {error}")
            continue

        results = response.results or []
        stats["results"] += len(results)
        if not results:
            print(f"[empty] '{query}': no results")
            continue

        added = 0
        for result in results:
            if len(new_leads) >= MAX_NEW_COMPANIES:
                break
            domain = domain_of(result.url)
            if not domain:
                exclusions["unparseable URL"] += 1
            elif is_excluded_domain(domain, excluded_domains):
                exclusions["directory / listing site"] += 1
            elif domain in known:
                stats["duplicates"] += 1
            else:
                lead, reason = to_lead(result, company_type, discovery, market, today)
                if reason:
                    exclusions[reason] += 1
                else:
                    known.add(domain)
                    new_leads.append(lead)
                    added += 1
                    text = f"{lead['description']} {' '.join(result.highlights or [])}"
                    matches = competitor_matches(text, competitor_keywords)
                    if matches:
                        competitor_review.append((lead["company_name"], lead["website"], matches))
        print(f"[ok] '{query}': {len(results)} results, {added} new companies")

    if new_leads:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        write_header = not OUTPUT.exists() or OUTPUT.stat().st_size == 0
        # A hand-edited file may lack a final newline; without one the first new row joins the last old row.
        needs_newline = not write_header and not OUTPUT.read_bytes().endswith(b"\n")
        with OUTPUT.open("a", newline="", encoding="utf-8") as f:
            if needs_newline:
                f.write("\r\n")
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_leads)

    print("\n--- Summary ---")
    print(f"Search queries executed: {stats['queries']} ({stats['failed']} failed)")
    print(f"Results returned:        {stats['results']}")
    print(f"New unique companies:    {len(new_leads)}")
    print(f"Duplicates skipped:      {stats['duplicates']}")
    print(f"Companies excluded:      {sum(exclusions.values())}")
    for reason, count in exclusions.most_common():
        print(f"  - {reason}: {count}")
    print(f"Competitor review:       {len(competitor_review)} (saved as 'new'; review manually)")
    for name, website, matches in competitor_review:
        print(f"  - {name} ({website}): {', '.join(matches)}")
    print(f"Records appended:        {len(new_leads)}")
    print(f"Total records saved:     {existing_count + len(new_leads)} in {OUTPUT}")


if __name__ == "__main__":
    main()
