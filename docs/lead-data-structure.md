# Lead Data Structure

One row per discovered company. Stored as CSV with a header row, UTF-8 encoded.

Allowed values come from `config/target-market.yaml` (company types, size) and `config/discovery.yaml` (countries, sources).

## Fields

| # | Column | Required | Format | Allowed values / notes |
|---|--------|----------|--------|------------------------|
| 1 | `company_name` | Yes | Text | Official company name |
| 2 | `website` | Yes | URL | Company homepage, e.g. `https://example.com` |
| 3 | `country` | Yes | Text | One of `discovery.countries` |
| 4 | `company_type` | Yes | Text | One of `ideal_prospect.company_types`. See [Company type](#company-type) |
| 5 | `employee_count` | No | Whole number | Leave empty if unknown. Must be 5–100 inclusive if known |
| 6 | `description` | No | Text | Short summary of what the company does |
| 7 | `services_offered` | No | List | Separated by semicolons, e.g. `AI Integration; SaaS Development` |
| 8 | `discovery_source` | Yes | Text | One of `discovery.sources[].name` |
| 9 | `source_url` | Yes | URL | Page where the company was found |
| 10 | `discovery_date` | Yes | Date | ISO 8601, `YYYY-MM-DD` |
| 11 | `lead_status` | Yes | Text | One of the statuses in [Lead status](#lead-status). Starts as `new` |

## Company type

`company_type` must be one of the three standard types:

- `Software Development Agency`
- `AI Consultancy`
- `Automation Consultancy`

The four discovery search categories are search terms, not stored values. Map each result to a standard type:

| Search category | Stored `company_type` |
|-----------------|-----------------------|
| Software Development Agencies | Software Development Agency |
| Custom Software Development Companies | Software Development Agency |
| AI Development Consultancies | AI Consultancy |
| AI Automation Agencies | Automation Consultancy |

## Lead status

| Status | Meaning |
|--------|---------|
| `new` | Discovered, not yet reviewed |
| `qualified` | Matches the target market and shows priority signals |
| `disqualified` | Out of scope, e.g. wrong size, freelancer, recruitment agency or non-tech business |
| `contacted` | Outreach has started |

## CSV header

```csv
company_name,website,country,company_type,employee_count,description,services_offered,discovery_source,source_url,discovery_date,lead_status
```

## Example row

```csv
Example Digital GmbH,https://example.com,Germany,Software Development Agency,35,"Berlin agency building web and mobile apps for startups.",Web Application Development; Mobile App Development,Exa Search,https://example.com/about,2026-11-19,new
```

## CSV rules

- Wrap a value in double quotes if it contains a comma, a quote or a line break.
- Write a double quote inside a value as `""`.
- Leave optional fields empty. Do not write `N/A` or `unknown`.
