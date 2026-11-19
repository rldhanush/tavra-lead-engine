"""Offline check for scripts/discover_leads.py using a fake Exa client. No API calls.

Run: python tests/test_discover_leads.py
"""

import contextlib
import csv
import importlib.util
import io
import os
import tempfile
import types
from pathlib import Path

from exa_py.api import (
    CompanyEntity, EntityCompanyProperties, Result,
    EntityCompanyPropertiesHeadquarters as HQ,
    EntityCompanyPropertiesWorkforce as Workforce,
)

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("discover_leads", ROOT / "scripts" / "discover_leads.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)
HEADER = ",".join(dl.COLUMNS)


def company(url, name=None, employees=None, hq=None, title="Some Co | Home", desc=None):
    entities = None
    if name:
        props = EntityCompanyProperties(
            name=name,
            description=desc or f"{name} offers AI Integration and SaaS Development.",
            workforce=Workforce(employees) if employees is not None else None,
            headquarters=HQ(country=hq) if hq else None,
        )
        entities = [CompanyEntity("id", "company", 1, props)]
    return Result(url=url, id=url, title=title,
                  highlights=['We do Custom Software Development, "fast"'], entities=entities)


RESULTS = [
    company("https://www.alpha.de/about", "Alpha GmbH", 30, "DE"),             # new, Germany
    company("https://beta.co.uk/", "Beta Ltd", None, "United Kingdom"),        # new, UK, no size
    company("https://existing.de/team", "Existing GmbH", 20, "Germany"),       # duplicate of CSV row
    company("https://alpha.de/contact", "Alpha GmbH", 30, "DE"),               # duplicate within run
    company("https://uk.clutch.co/agencies", "Clutch", 50, "GB"),              # directory
    company("https://nohq.de/", "No HQ GmbH", 10),                             # HQ unknown
    company("https://plain.co.uk/"),                                           # no Exa company data
    company("https://usco.com/", "US Co", 20, "United States"),                # wrong country
    company("https://big.de/", "Big AG", 5000, "Germany"),                     # too large
    company("https://gamma.de/", "Gamma GmbH", 40, "Germany",                  # competitor, kept + flagged
            desc="Gamma provides Staff-Augmentation and dedicated teams."),
]
GERMAN = [f"{q} Deutschland" for q in [
    "Softwareagentur", "Softwareentwicklung Agentur", "Individualsoftware Entwicklung",
    "KI-Beratung", "KI-Entwicklung", "KI-Automatisierung Agentur", "Prozessautomatisierung Beratung"]]
ENGLISH_UK = [f"{c} in United Kingdom" for c in ["Software Development Agencies",
              "Custom Software Development Companies", "AI Development Consultancies", "AI Automation Agencies"]]


class FakeExa:
    calls = []

    def __init__(self, api_key):
        pass

    def search(self, query, **kwargs):
        FakeExa.calls.append((query, kwargs))
        assert kwargs["num_results"] == 5 and kwargs["category"] == "company"
        assert "clutch.co" in kwargs["exclude_domains"]
        if query == "KI-Beratung Deutschland":
            raise RuntimeError("simulated 500 from Exa")
        if query == "KI-Automatisierung Agentur Deutschland":
            return types.SimpleNamespace(results=[])
        if query == "Softwareagentur Deutschland":
            return types.SimpleNamespace(results=RESULTS)
        return types.SimpleNamespace(results=RESULTS[:3])  # only duplicates afterwards


class FreshExa:
    """Every query returns 5 new valid German companies, to hit the 20-company cap."""
    calls = 0

    def __init__(self, api_key):
        pass

    def search(self, query, **kwargs):
        assert kwargs["num_results"] == 5
        FreshExa.calls += 1
        return types.SimpleNamespace(results=[
            company(f"https://co{FreshExa.calls}x{i}.de/", f"Co {FreshExa.calls}-{i}", 10, "DE") for i in range(5)])


def run_quiet():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        dl.main()
    return out.getvalue()


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def main():
    os.environ["EXA_API_KEY"] = "dummy"
    dl.Exa = FakeExa

    # Config: four categories, each mapped to a target-market industry.
    discovery, market = dl.load_config()
    mapping = {c["name"]: c["company_type"] for c in discovery["search_categories"]}
    assert mapping == {
        "Software Development Agencies": "Software Development Agency",
        "Custom Software Development Companies": "Software Development Agency",
        "AI Development Consultancies": "AI Consultancy",
        "AI Automation Agencies": "Automation Consultancy",
    }, mapping
    assert set(mapping.values()) <= set(market["industries"])

    # Query generation: German for Germany, English categories for the UK.
    searches = dl.build_queries(discovery)
    assert [q for c, q, _ in searches if c == "Germany"] == GERMAN, searches
    assert [q for c, q, _ in searches if c == "United Kingdom"] == ENGLISH_UK, searches
    types_de = {q: t for c, q, t in searches if c == "Germany"}
    assert types_de["KI-Beratung Deutschland"] == "AI Consultancy"
    assert types_de["Prozessautomatisierung Beratung Deutschland"] == "Automation Consultancy"
    assert {t for _, _, t in searches} == set(market["industries"])
    print("Query generation OK:", len(searches), "queries")

    # Competitor matching: case-insensitive, hyphens as spaces, no false hit.
    kw = discovery["competitor_review_keywords"]
    assert dl.competitor_matches("We offer TEAM-AUGMENTATION.", kw) == ["Team augmentation"]
    assert dl.competitor_matches("Our offshore delivery center in Pune", kw) == ["Offshore delivery center"]
    assert dl.competitor_matches("We build custom software for clients.", kw) == []
    assert dl.competitor_matches("a global network of 500 AI and Machine Learning experts", kw) == ["Machine Learning experts"]
    assert dl.competitor_matches("with a delivery office in Mathura, India", kw) == ["Delivery office"]
    assert dl.competitor_matches("our Development Center in India and offshore engineering team", kw) == [
        "Development center in India", "Offshore engineering team"]

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "leads.csv"
        dl.OUTPUT = out
        existing = (HEADER + "\n" + 'Existing GmbH,https://www.existing.de,Germany,Software Development Agency,'
                    '20,"Kept, untouched",,Exa Search,https://www.existing.de,2026-11-01,qualified\n')
        out.write_text(existing, encoding="utf-8")

        print("=== Run 1 ===")
        log = run_quiet()
        print(log)
        text = out.read_text(encoding="utf-8")
        rows = read_rows(out)
        assert text.startswith(existing), "existing rows changed"
        assert rows[0] == dl.COLUMNS and all(len(r) == 11 for r in rows)
        leads = [dict(zip(rows[0], r)) for r in rows[1:]]
        assert leads[0]["lead_status"] == "qualified"
        new = leads[1:]
        assert [l["company_name"] for l in new] == ["Alpha GmbH", "Beta Ltd", "Gamma GmbH"], new
        assert [l["country"] for l in new] == ["Germany", "United Kingdom", "Germany"]
        assert [l["employee_count"] for l in new] == ["30", "", "40"]
        assert new[2]["lead_status"] == "new", "competitor must be kept, not excluded"
        assert "Competitor review:       1" in log
        assert "Gamma GmbH (https://gamma.de): Staff augmentation" in log
        assert all(l["lead_status"] == "new" and l["discovery_source"] == "Exa Search" for l in new)
        assert all(l["company_type"] in market["industries"] for l in new)
        assert new[0]["website"] == "https://www.alpha.de"
        assert [q for q, _ in FakeExa.calls] == GERMAN + ENGLISH_UK

        print("\n=== Run 2 (same results: everything already saved) ===")
        dl.main()
        assert out.read_text(encoding="utf-8") == text, "second run changed the file"

        print("\n=== Hand-edited file: Excel BOM, no trailing newline ===")
        edited = b"\xef\xbb\xbf" + existing.rstrip("\n").encode("utf-8")
        out.write_bytes(edited)
        dl.main()
        assert out.read_bytes().startswith(edited + b"\r\n"), "existing last row not preserved"
        rows = read_rows(out)
        assert len(rows) == 5 and all(len(r) == 11 for r in rows), rows
        assert rows[1][-1] == "qualified" and rows[2][0] == "Alpha GmbH"

        print("\n=== 20-company cap, 5 results per query ===")
        out.write_text(existing, encoding="utf-8")
        dl.Exa = FreshExa
        log = run_quiet()
        dl.Exa = FakeExa
        rows = read_rows(out)
        assert out.read_text(encoding="utf-8").startswith(existing)
        assert len(rows) == 2 + 20, len(rows)
        assert FreshExa.calls == 4, FreshExa.calls   # stops searching once 20 are found
        assert "New unique companies:    20" in log
        print(f"{len(rows) - 2} appended after {FreshExa.calls} queries")

        print("\n=== Unexpected header is left untouched ===")
        bad = "name,url\nX,https://x.de\n"
        out.write_text(bad, encoding="utf-8")
        try:
            dl.main()
            raise AssertionError("expected exit on bad header")
        except SystemExit as exit_:
            print(exit_)
        assert out.read_text(encoding="utf-8") == bad

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
