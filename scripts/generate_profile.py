#!/usr/bin/env python3
"""Generate locally owned SVG graphics for the GitHub profile README.

Uses GitHub GraphQL in Actions. With no token, it falls back to the public
contribution calendar and REST API so the graphics can also be previewed locally.
Standard library only.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOGIN = os.getenv("GH_LOGIN", "carterlasalle")
TOKEN = os.getenv("GITHUB_TOKEN")
OUT = Path(os.getenv("OUT_DIR", "."))
WIDTH = 620
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")

QUERY = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      contributionCalendar{
        weeks{contributionDays{contributionCount date weekday}}
      }
    }
    repositories(first:100,ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC){
      nodes{
        languages(first:12,orderBy:{field:SIZE,direction:DESC}){
          edges{size node{name}}
        }
      }
    }
  }
}
"""


def request(url: str, *, body: dict | None = None) -> dict | str:
    data = json.dumps(body).encode() if body else None
    headers = {"User-Agent": f"{LOGIN}-profile-readme"}
    if TOKEN:
        headers["Authorization"] = f"bearer {TOKEN}"
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw.lstrip().startswith(("{", "[")) else raw


def date_window() -> tuple[date, date]:
    end = datetime.now(timezone.utc).date()
    return end - timedelta(days=364), end


def fetch_graphql() -> tuple[dict[str, int], list[tuple[str, int]], list[tuple[str, int]]]:
    start, end = date_window()
    payload = request(
        "https://api.github.com/graphql",
        body={
            "query": QUERY,
            "variables": {
                "login": LOGIN,
                "from": f"{start.isoformat()}T00:00:00Z",
                "to": f"{end.isoformat()}T23:59:59Z",
            },
        },
    )
    if not isinstance(payload, dict) or payload.get("errors"):
        raise RuntimeError(f"GraphQL failed: {payload}")
    user = payload["data"]["user"]
    days = {}
    for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for item in week["contributionDays"]:
            days[item["date"]] = item["contributionCount"]

    by_bytes: Counter[str] = Counter()
    by_repos: Counter[str] = Counter()
    for repo in user["repositories"]["nodes"]:
        edges = repo["languages"]["edges"]
        for edge in edges:
            by_bytes[edge["node"]["name"]] += edge["size"]
        if edges:
            by_repos[edges[0]["node"]["name"]] += 1
    return days, by_bytes.most_common(5), by_repos.most_common(5)


def public_days(year: int) -> dict[str, int]:
    page = request(
        f"https://github.com/users/{LOGIN}/contributions"
        f"?from={year}-01-01&to={year}-12-31"
    )
    assert isinstance(page, str)
    pattern = re.compile(
        r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*class="ContributionCalendar-day"'
        r'.*?<tool-tip[^>]*>(No contributions|(\d+) contributions?) on ',
        re.S,
    )
    return {
        day: 0 if label.startswith("No ") else int(count)
        for day, label, count in pattern.findall(page)
    }


def fetch_public() -> tuple[dict[str, int], list[tuple[str, int]], list[tuple[str, int]]]:
    start, end = date_window()
    days = {}
    for year in range(start.year, end.year + 1):
        days.update(public_days(year))

    repos = request(f"https://api.github.com/users/{LOGIN}/repos?per_page=100&type=owner")
    assert isinstance(repos, list)
    by_size: Counter[str] = Counter()
    by_repos: Counter[str] = Counter()
    for repo in repos:
        language = repo.get("language")
        if language and not repo.get("fork"):
            by_size[language] += int(repo.get("size") or 0)
            by_repos[language] += 1
    return days, by_size.most_common(5), by_repos.most_common(5)


def normalized(days: dict[str, int]) -> list[tuple[date, int]]:
    start, end = date_window()
    output = []
    current = start
    while current <= end:
        output.append((current, int(days.get(current.isoformat(), 0))))
        current += timedelta(days=1)
    return output


def streaks(days: list[tuple[date, int]]) -> tuple[dict, dict]:
    best = {"length": 0, "start": None, "end": None}
    run = 0
    run_start = None
    for day, count in days:
        if count:
            run_start = run_start or day
            run += 1
            if run > best["length"]:
                best = {"length": run, "start": run_start, "end": day}
        else:
            run, run_start = 0, None

    tail = days[:-1] if days and not days[-1][1] else days
    current = {"length": 0, "start": None, "end": None}
    for day, count in reversed(tail):
        if not count:
            break
        current["length"] += 1
        current["start"] = day
        current["end"] = current["end"] or day
    return current, best


def theme() -> str:
    return """
<style>
.ink{fill:#424a53}.data{fill:#6e7681}.dim{fill:#8c959f}
.rule{stroke:#d8dee4}.line{stroke:#6e7681}.wash{fill:#6e7681;opacity:.13}
@media(prefers-color-scheme:dark){
  .ink{fill:#f0f6fc}.data{fill:#c9d1d9}.dim{fill:#8b949e}
  .rule{stroke:#30363d}.line{stroke:#c9d1d9}.wash{fill:#c9d1d9;opacity:.16}
}
</style>"""


def root(height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="{MONO}">{theme()}'
    )


def text(x, y, value, size=11, cls="data", anchor=None, extra="") -> str:
    anchor_attr = f' text-anchor="{anchor}"' if anchor else ""
    return (
        f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}"'
        f'{anchor_attr}{extra}>{html.escape(str(value))}</text>'
    )


def fade(delay: float) -> str:
    return (
        f'<animate attributeName="opacity" from="0" to="1" begin="{delay:.2f}s" '
        'dur=".4s" fill="freeze"/>'
    )


def write(name: str, content: str) -> bool:
    path = OUT / name
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def draw_heading(label: str) -> str:
    line_start = min(210, len(label) * 9.7 + 20)
    return (
        root(26)
        + text(0, 18, label, 16, "ink", extra=' font-weight="700"')
        + f'<line x1="{line_start:.0f}" y1="12.5" x2="{WIDTH}" y2="12.5" '
          'class="rule" stroke-width="1"/>'
        + "</svg>"
    )


def draw_stats(days: list[tuple[date, int]]) -> str:
    total = sum(count for _, count in days)
    active = sum(bool(count) for _, count in days)
    buckets = [days[i:i + 7] for i in range(0, len(days), 7)]
    weekly = [sum(count for _, count in week) for week in buckets]
    best_week = max(weekly, default=0)
    peak = max(weekly, default=1) or 1
    base, top = 138, 86
    step = WIDTH / max(1, len(weekly) - 1)
    points = [(i * step, base - value / peak * (base - top)) for i, value in enumerate(weekly)]
    line = " ".join(f"L{x:.1f},{y:.1f}" for x, y in points[1:])
    area = f'M0,{base} L{points[0][0]:.1f},{points[0][1]:.1f} {line} L{WIDTH},{base} Z'
    path = f'M{points[0][0]:.1f},{points[0][1]:.1f} {line}'
    return (
        root(148)
        + f'<g opacity="0">{fade(.08)}'
        + text(0, 50, total, 52, "ink", extra=' font-weight="700"')
        + text(0, 72, "contributions in the last year", 12, "dim")
        + "</g>"
        + f'<g opacity="0">{fade(.25)}'
        + text(WIDTH, 30, active, 19, "ink", "end", ' font-weight="700"')
        + text(WIDTH, 47, "active days", 11, "dim", "end")
        + text(WIDTH, 70, best_week, 19, "ink", "end", ' font-weight="700"')
        + text(WIDTH, 87, "best week", 11, "dim", "end")
        + "</g>"
        + '<clipPath id="reveal"><rect x="0" y="80" width="0" height="64">'
          '<animate attributeName="width" from="0" to="620" begin=".45s" dur="1.25s" fill="freeze"/>'
          "</rect></clipPath>"
        + '<g clip-path="url(#reveal)">'
        + f'<path d="{area}" class="wash"/>'
        + f'<path d="{path}" fill="none" class="line" stroke-width="2" '
          'stroke-linejoin="round" stroke-linecap="round"/>'
        + "</g></svg>"
    )


def draw_streak(days: list[tuple[date, int]]) -> str:
    current, longest = streaks(days)

    def span(item: dict) -> str:
        if not item["length"]:
            return "—"
        return f'{MONTHS[item["start"].month - 1]} {item["start"].day} – ' \
               f'{MONTHS[item["end"].month - 1]} {item["end"].day}'

    blocks = []
    for index, (item, label) in enumerate(((current, "current streak"), (longest, "longest streak"))):
        x = 34 if index == 0 else 344
        blocks.append(
            f'<g opacity="0">{fade(.12 + index * .14)}'
            + text(x, 44, item["length"], 34, "ink", extra=' font-weight="700"')
            + text(x, 64, label, 11, "data")
            + text(x, 80, span(item), 10, "dim")
            + "</g>"
        )
    return (
        root(96)
        + '<line x1="310" y1="16" x2="310" y2="80" class="rule"/>'
        + "".join(blocks)
        + "</svg>"
    )


def draw_languages(by_size: list[tuple[str, int]], by_repos: list[tuple[str, int]]) -> str:
    rows = max(len(by_size), len(by_repos), 1)
    height = 34 + rows * 22
    parts = [root(height)]
    for group_index, (x, label, values, suffix) in enumerate((
        (34, "BY BYTES", by_size, "%"),
        (344, "BY REPOS", by_repos, ""),
    )):
        parts.append(text(x, 12, label, 9, "dim", extra=' letter-spacing="1.3"'))
        maximum = max((value for _, value in values), default=1)
        total = sum(value for _, value in values) or 1
        for row, (name, value) in enumerate(values):
            y = 26 + row * 22
            shown = f"{value / total * 100:.0f}%" if suffix else str(value)
            width = 116 * value / maximum
            parts.append(text(x, y + 8, name.lower()[:12], 11, "ink"))
            parts.append(
                f'<rect x="{x + 88}" y="{y}" width="{width:.1f}" height="7" '
                f'rx="3.5" class="data" opacity="0">{fade(.25 + row * .06)}</rect>'
            )
            parts.append(text(x + 248, y + 8, shown, 11, "dim", "end"))
    parts.append("</svg>")
    return "".join(parts)


def draw_year(days: list[tuple[date, int]]) -> str:
    by_weekday: dict[int, list[str]] = defaultdict(list)
    ramp = " :+#@"

    def symbol(count: int) -> str:
        if count == 0:
            return " "
        if count <= 2:
            return ":"
        if count <= 5:
            return "+"
        if count <= 9:
            return "#"
        return "@"

    for day, count in days:
        by_weekday[day.weekday()].append(symbol(count) * 2)

    active = sum(bool(count) for _, count in days)
    parts = [
        root(148),
        text(34, 16, "THE YEAR", 9, "dim", extra=' letter-spacing="1.3"'),
        text(34, 32, f"{active} of {len(days)} days had a contribution", 11, "dim"),
        text(614, 32, "less  : + # @  more", 9, "dim", "end"),
    ]
    for weekday in range(7):
        line = "".join(by_weekday[weekday])
        y = 52 + weekday * 11
        safe = html.escape(line)
        parts.append(
            f'<text xml:space="preserve" x="34" y="{y}" class="data" font-size="9.2" '
            f'opacity="0">{safe}{fade(.25 + weekday * .07)}</text>'
        )
    for weekday, label in ((0, "mon"), (2, "wed"), (4, "fri")):
        parts.append(text(27, 52 + weekday * 11, label, 9, "dim", "end"))

    start = days[0][0]
    last_month = None
    last_x = -100
    for index, (day, _) in enumerate(days[::7]):
        x = 34 + index * (552 / 52)
        if day.month != last_month and x - last_x > 34:
            parts.append(text(f"{x:.1f}", 143, MONTHS[day.month - 1], 9, "dim"))
            last_x = x
        last_month = day.month
    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    raw_days, by_size, by_repos = fetch_graphql() if TOKEN else fetch_public()
    days = normalized(raw_days)
    files = {
        "stats.svg": draw_stats(days),
        "streak.svg": draw_streak(days),
        "langs.svg": draw_languages(by_size, by_repos),
        "year.svg": draw_year(days),
    }
    for slug, label in (
        ("about", "about"),
        ("stack", "stack"),
        ("projects", "selected work"),
        ("stats", "stats"),
        ("principles", "working principles"),
        ("about-this-page", "about this page"),
    ):
        files[f"hd-{slug}.svg"] = draw_heading(label)

    changed = [name for name, content in files.items() if write(name, content)]
    print(f"{sum(count for _, count in days)} contributions; updated {len(changed)} files")


if __name__ == "__main__":
    main()
