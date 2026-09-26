#!/usr/bin/env python3
"""
M2.12 News Ingest — build-time snapshot fetcher (NO backend service).

Fetches the configured open-licence RSS/Atom feeds, applies the full M2.12 NewsItem
contract, and writes ONE json file the frontend imports. There is no server, no
endpoint and no runtime fetch: the browser never talks to a feed. Re-run this to
refresh.

WHY a script and not a service (human decision 2026-09-27): the backend is deferred, but
real news was wanted on the Overview now. A committed snapshot gives authentic content
with no service to run — at the cost of staleness between runs, which the UI states.

Spec: docs/specs/m2.12_news_ingest.spec.md
Contract mirror: frontend/app/mock_news_data.ts
Output: frontend/app/news_snapshot.json

WHY only open-licence sources: a public RSS feed is not automatically redistributable.
Tier-2 (copyrighted) publishers are configured with redistribute_text=False and are
therefore NEVER fetched here — the contract keeps their slot so the UI can name them, and
the omission happens at this boundary rather than in a serialiser.

Usage:
  python scripts/verify_m2.12_news_snapshot.py                # write the snapshot
  python scripts/verify_m2.12_news_snapshot.py --stdout       # print, write nothing
  python scripts/verify_m2.12_news_snapshot.py --per-source 8
Exit: 0 = wrote a snapshot (a source may still have failed, that is reported);
      1 = every source failed, so nothing could be written.
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "frontend" / "app" / "news_snapshot.generated.ts"

# Stable namespace so item_id is reproducible across runs and machines.
NEWS_NS = uuid.UUID("6f2c9d54-0f1e-5c7a-9a3b-1d4e8f7a2b60")

USER_AGENT = "QUANTRADER-news/0.1 (research build; +https://localhost)"
MAX_BYTES = 4_000_000          # a feed body over this is a fetch error, not a buffer risk
DEFAULT_PER_SOURCE = 8
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)   # feed timestamps may lead our clock slightly
SUMMARY_CHARS = 280


@dataclass(frozen=True)
class Source:
    source_id: str
    publisher: str
    feed_url: str
    category: str
    # Fail closed: a source is only quoted when this is explicitly True.
    redistribute_text: bool = False


# Tier 1 only. Tier-2 publishers are deliberately absent — see the module docstring.
SOURCES: tuple[Source, ...] = (
    Source("ecb", "European Central Bank", "https://www.ecb.europa.eu/rss/press.html", "monetary_policy", True),
    Source("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "monetary_policy", True),
    Source("boe", "Bank of England", "https://www.bankofengland.co.uk/rss/news", "monetary_policy", True),
    Source("cftc", "CFTC", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "regulation", True),
    Source("sec", "SEC", "https://www.sec.gov/news/pressreleases.rss", "regulation", True),
    Source("bis", "BIS", "https://www.bis.org/doclist/all_pressrels.rss", "macro", True),
    Source("arxiv", "arXiv (q-fin)", "https://rss.arxiv.org/rss/q-fin", "research", True),
    Source("arxiv_econ", "arXiv (econ.GN)", "https://rss.arxiv.org/rss/econ.GN", "research", True),
)


class NewsFetchError(Exception):
    """Typed: the feed could not be retrieved. Source is marked failed, never empty."""


class NewsParseError(Exception):
    """Typed: the body was not a feed. Never reported as 'no news'."""


@dataclass
class SourceResult:
    source_id: str
    health: str = "ok"          # ok | failed
    reason: str | None = None
    items: list[dict] = field(default_factory=list)


def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle.

    WHY: the stock Windows Python here has no usable issuer chain, so every TLS fetch
    failed with CERTIFICATE_VERIFY_FAILED. That is a machine problem, not a feed problem,
    and it must not be mistaken for 'this publisher is down'.
    """
    try:
        import certifi  # noqa: PLC0415 - optional dependency, resolved at runtime

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise NewsFetchError(f"refusing non-HTTPS url: {url[:40]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(request, context=_ssl_context(), timeout=20) as response:
            final = response.geturl()
            if not final.startswith("https://"):
                raise NewsFetchError("redirected to non-HTTPS")
            body = response.read(MAX_BYTES + 1)
    except NewsFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport fault is a fetch error
        raise NewsFetchError(f"{type(exc).__name__}: {exc}") from exc
    if len(body) > MAX_BYTES:
        raise NewsFetchError(f"body exceeds {MAX_BYTES} bytes")
    return body


def _local(tag: str) -> str:
    """Namespace-stripped tag name, so RSS and Atom can share one parser."""
    return tag.rsplit("}", 1)[-1].lower()


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _first(item: ET.Element, names: frozenset[str] | set[str] | tuple[str, ...]) -> ET.Element | None:
    for child in item:
        if _local(child.tag) in names:
            return child
    return None


def _parse_date(raw: str) -> datetime | None:
    """Return None rather than guessing. A missing date must not become 'now'."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) if parsed else None


def _first_image(item: ET.Element) -> str | None:
    """An https media enclosure, if the feed offers one. http is dropped, not downgraded."""
    for child in item:
        name = _local(child.tag)
        url = child.get("url") or child.get("href")
        if not url:
            continue
        if name in {"enclosure", "content", "thumbnail", "image"} and child.get("type", "").startswith("image") or name in {"thumbnail", "image"}:
            if url.startswith("https://"):
                return url
    return None


def _strip_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", " ", raw)


def _collect(source: Source, per_source: int, now: datetime) -> SourceResult:
    result = SourceResult(source_id=source.source_id)
    body = _fetch(source.feed_url)

    # A 200 that returns an HTML page is a parse failure, NOT an empty feed.
    head = body[:400].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise NewsParseError("body is HTML, not a feed")

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NewsParseError(f"xml parse error: {exc}") from exc

    entries = [node for node in root.iter() if _local(node.tag) in {"item", "entry"}]
    for entry in entries:
        if len(result.items) >= per_source:
            break
        item = _to_item(source, entry, now)
        if item is not None:
            result.items.append(item)

    # Deterministic order inside a source: newest first, id breaks ties.
    result.items.sort(key=lambda entry: (entry["published_at"], entry["item_id"]), reverse=True)
    return result


def _to_item(source: Source, entry: ET.Element, now: datetime) -> dict | None:
    title = _strip_html(_text(_first(entry, {"title"})))
    if not title:
        return None

    link_node = _first(entry, {"link"})
    url = ""
    if link_node is not None:
        url = (link_node.get("href") or _text(link_node)).strip()
    if not url.startswith("https://"):
        return None

    guid = _text(_first(entry, {"guid", "id"})) or url or f"{title}"
    published = _parse_date(
        _text(_first(entry, {"pubdate", "published", "updated", "date"}))
    )
    # A missing or unparseable date rejects the item. Substituting our own clock would
    # manufacture freshness and break PIT.
    if published is None:
        return None
    if published > now + CLOCK_SKEW_TOLERANCE:
        return None

    image_url = _first_image(entry)
    # image_alt is the publisher's own title, so it names the subject. An image without
    # it is unreadable for a screen reader, so the item is rejected rather than shipped
    # with a placeholder alt.
    image_alt = title if image_url else None

    summary_raw = _strip_html(_text(_first(entry, {"description", "summary", "content"})))
    truncated = len(summary_raw) > SUMMARY_CHARS
    if truncated:
        summary_raw = summary_raw[:SUMMARY_CHARS].rsplit(" ", 1)[0]

    return {
        "item_id": str(uuid.uuid5(NEWS_NS, f"{source.source_id}:{guid}")),
        "source_id": source.source_id,
        "publisher": source.publisher,
        "category": source.category,
        "title": title[:300],
        "url": url,
        "published_at": published.isoformat().replace("+00:00", "Z"),
        "availability_time": published.isoformat().replace("+00:00", "Z"),
        "reference_snapshot_id": None,
        "redistribute_text": source.redistribute_text,
        "summary": summary_raw or None,
        "summary_truncated": truncated,
        "image_url": image_url,
        "image_alt": image_alt,
    }


def build_snapshot(per_source: int, now: datetime) -> dict:
    results: list[SourceResult] = []
    for source in SOURCES:
        try:
            results.append(_collect(source, per_source, now))
        except (NewsFetchError, NewsParseError) as exc:
            failed = SourceResult(source_id=source.source_id, health="failed", reason=str(exc)[:160])
            results.append(failed)
            print(f"  FAILED {source.source_id:9} {exc}", file=sys.stderr)

    items = [item for result in results for item in result.items]
    # Total order, so two runs over the same feeds produce a byte-identical ordering.
    items.sort(key=lambda entry: (entry["published_at"], entry["item_id"]), reverse=True)

    health = {result.source_id: result.health for result in results}
    ok = sum(1 for result in results if result.health == "ok")
    if not items:
        status = "unavailable"
    elif ok == len(results):
        status = "fresh"
    elif ok == 0:
        status = "unavailable"
    else:
        status = "degraded"

    return {
        "status": status,
        "items": items,
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "source_health": health,
        "last_good_at": now.isoformat().replace("+00:00", "Z") if items else None,
        "generated_by": "verify_m2.12_news_snapshot.py",
        "note": "Build-time snapshot. No backend service: re-run the script to refresh.",
    }


def _write_module(snapshot: dict) -> None:
    """Emit a TYPED TypeScript module, not JSON.

    WHY not .json: the frontend tsconfig has no resolveJsonModule, and more importantly a
    typed module means `tsc` validates the generated payload. A malformed snapshot then
    fails the BUILD rather than rendering half-formed news at a reader.
    """
    body = json.dumps(snapshot, indent=2, ensure_ascii=False)
    module = f"""// GENERATED FILE - DO NOT EDIT BY HAND.
// Source: financial_analyst_app/scripts/verify_m2.12_news_snapshot.py
// Regenerate:  python financial_analyst_app/scripts/verify_m2.12_news_snapshot.py
//
// Real headlines from open-licence institutional feeds. There is no backend service and
// the browser fetches nothing: this file IS the feed, until the script is re-run.
import type {{ NewsSnapshot }} from "./mock_news_data";

export const NEWS_SNAPSHOT: NewsSnapshot = {body};
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(module, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="M2.12 build-time news snapshot")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--per-source", type=int, default=DEFAULT_PER_SOURCE)
    args = parser.parse_args()

    socket.setdefaulttimeout(20)
    now = datetime.now(timezone.utc)
    snapshot = build_snapshot(args.per_source, now)

    if args.stdout:
        json.dump(snapshot, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        _write_module(snapshot)

    counts: dict[str, int] = {}
    for item in snapshot["items"]:
        counts[item["source_id"]] = counts.get(item["source_id"], 0) + 1
    print(f"status={snapshot['status']} items={len(snapshot['items'])} per_source={counts}")
    if not snapshot["items"]:
        print("no items from any source — the snapshot would be empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
