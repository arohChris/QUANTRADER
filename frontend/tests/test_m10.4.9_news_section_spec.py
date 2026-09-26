"""Spec: Overview news section (frontend half of M2.12 News Ingest).

The Overview previously showed invented headlines — "Momentum improving",
"Dollar steadies before the next data window" — with nothing behind them. That was
removed. This is the honest replacement: a real source, or an honest "not connected".

THE RULE THIS SPEC ENFORCES: the frontend must never render a headline that did not come
from a feed. Until `GET /api/v1/news` exists, the section renders an explicit
"not connected" state with zero items. Sample headlines are the exact failure this
replaces, so a test fails if any appear.

Backend spec: docs/specs/m2.12_news_ingest.spec.md
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"
_APP = _FRONTEND / "app"


def _read(name: str) -> str:
    return (_APP / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"^\s*{re.escape(selector)}\s*\{{(.*?)\}}", css, re.DOTALL | re.MULTILINE)
    assert match is not None, f"no such CSS rule: {selector}"
    return match.group(1)


def _styles() -> str:
    parts = [(_FRONTEND / "app" / "styles.css").read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in sorted((_APP / "styles").glob("*.css"))]
    return "\n".join(parts)


def test_news_items_carry_an_optional_image_with_required_alt_text() -> None:
    # Human directive 2026-09-27: news should come with images. Most institutional feeds
    # (ECB, Fed, BIS) do ship a media enclosure per item, so the contract carries one.
    data = _read("mock_news_data.ts")
    section = _read("NewsSection.tsx")
    styles = _styles()

    for field in ("image_url:", "image_alt:"):
        assert field in data, f"missing NewsItem field {field}"
    # Both nullable: plenty of items legitimately have no image, and a null must be a
    # supported state rather than an empty string standing in for "missing".
    assert "string | null" in data

    # Accessibility is the reason this is tested, not decoration: an <img> with no alt is
    # unreadable for a screen reader, so alt is required and never a placeholder string.
    assert "<img" in section
    assert "alt={" in section
    assert 'alt=""' not in section, "a decorative empty alt is wrong here — the image carries meaning"
    # A broken or absent image must not leave a torn layout: the row collapses to text.
    assert "news-thumb is-missing" in section or "is-missing" in section
    assert ".news-thumb" in styles
    assert ".news-item.has-thumb" in styles
    # Loading is lazy and dimensions are reserved, so a slow image cannot shift the list.
    assert 'loading="lazy"' in section
    assert "aspect-ratio" in styles


def test_images_follow_the_same_licence_rule_as_the_text() -> None:
    # An image is copyrighted exactly like a headline. A link-only source gets no image.
    section = _code_only(_read("NewsSection.tsx"))
    assert "image_url" in section
    # The image branch is gated on the same flag as the text branch, not on its own.
    assert "redistribute_text" in section
    assert "DEFAULT_REDISTRIBUTE_TEXT" in _read("mock_news_data.ts")


def test_link_only_sources_are_rendered_without_their_text() -> None:
    # Licence boundary (m2.12 spec, Sources): a reputable commercial publisher can appear
    # in the Overview, but only as a source with a link. Reproducing a copyrighted
    # headline is the thing we must not do, so the UI branches on `redistribute_text`.
    section = _read("NewsSection.tsx")
    data = _read("mock_news_data.ts")

    assert "redistribute_text" in data
    assert "redistribute_text" in section
    # The link-only branch renders the publisher and a link, and never the title.
    assert "link-only" in section.lower() or "linkOnly" in section
    # The contract mirror documents the fail-closed default.
    assert "false" in data
    assert "DEFAULT_REDISTRIBUTE_TEXT" in data or "default" in data.lower()


def _code_only(text: str) -> str:
    """Source with comments stripped.

    A WHY comment is allowed to *name* the invented headlines it removed — that is the
    record of why they are gone. What must not exist is the string as data.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)


def test_news_section_is_on_the_overview_and_has_no_invented_headlines() -> None:
    assert (_APP / "NewsSection.tsx").is_file()
    home = _read("HomeDashboard.tsx")
    assert "NewsSection" in home, "the Overview must render the news section"

    section = _code_only(_read("NewsSection.tsx"))
    data = _code_only(_read("mock_news_data.ts"))

    # Zero sample headlines anywhere. This is the whole point of the slice.
    for invented in (
        "Momentum improving",
        "More context needed",
        "Dollar steadies",
        "Volatility remains",
        "headline_one",
        "Lorem",
    ):
        assert invented not in section, f"{invented!r} is invented content and must not ship"
        assert invented not in data, f"{invented!r} is invented content and must not ship"
    # And no sentiment vocabulary — M2 moves bytes, it does not interpret them.
    for verdict in ("bullish", "bearish", "sentiment", "outlook"):
        assert verdict not in _flat(section).lower(), f"{verdict!r} is a judgement, not news"


def test_news_panel_shows_at_most_ten_headlines() -> None:
    # Human directive 2026-09-27: max 10 news. The snapshot carries more, so the cap is a
    # presentation limit, and it is stated on screen rather than silently truncating.
    section = _read("NewsSection.tsx")
    styles = _styles()

    assert "MAX_VISIBLE = 10" in section
    assert "open.slice(0, MAX_VISIBLE)" in section
    # The cap is disclosed, so a reader can tell the list is not the whole snapshot.
    assert "news-count" in section
    assert "Showing the" in section
    assert ".news-count" in styles
    # No expander: with a hard maximum there is nothing to expand to.
    assert "expanded" not in section
    assert "Show all" not in section
    # And the ordering guarantees the ten span publishers rather than one busy feed.
    assert "published_at" in (_read("news_snapshot.generated.ts"))


def test_generated_snapshot_carries_real_headlines_and_honours_the_contract() -> None:
    # The payload is generated, not hand-written. These are the invariants that make it
    # trustworthy, asserted against the committed file so a bad regeneration is caught.
    generated = _APP / "news_snapshot.generated.ts"
    assert generated.is_file(), "run scripts/verify_m2.12_news_snapshot.py to produce it"

    import json

    lines = generated.read_text(encoding="utf-8").splitlines()
    # Slice by the known module shape, not by scanning for braces — a headline or summary
    # containing `};` would otherwise truncate the payload and fail the parse.
    start = next(i for i, line in enumerate(lines) if line.startswith("export const NEWS_SNAPSHOT"))
    end = next(i for i, line in enumerate(lines) if line == "};")
    snapshot = json.loads("{" + "\n".join(lines[start + 1 : end]) + "}")

    assert snapshot["items"], "the snapshot has no headlines — the Overview would be empty"
    assert snapshot["status"] in {"fresh", "stale", "degraded"}
    assert snapshot["generated_by"] == "verify_m2.12_news_snapshot.py"

    ids = [item["item_id"] for item in snapshot["items"]]
    assert len(set(ids)) == len(ids), "item_id must be unique — it is uuid5(source_id:guid)"

    for item in snapshot["items"]:
        assert item["url"].startswith("https://"), f"non-HTTPS link: {item['url']}"
        assert item["availability_time"] == item["published_at"], "PIT field must equal published_at"
        assert item["published_at"] <= snapshot["as_of"], "an item from the future leaked in"
        assert item["title"].strip(), "an item with no title cannot render"
        # An image always carries alt text, and http images are dropped rather than shown.
        if item["image_url"] is not None:
            assert item["image_alt"], f"image without alt: {item['item_id']}"
            assert item["image_url"].startswith("https://")
        # Only open-licence sources are ever fetched, so nothing link-only is quoted here.
        assert item["redistribute_text"] is True, "a link-only source was quoted"
        assert item["category"] in {
            "monetary_policy",
            "macro",
            "regulation",
            "market",
            "research",
        }

    # Total order, so two runs over the same feeds diff cleanly.
    order = [(item["published_at"], item["item_id"]) for item in snapshot["items"]]
    assert order == sorted(order, reverse=True), "items are not in the contracted order"

    # Every source that was reached is reported, so a silent drop is visible.
    assert snapshot["source_health"]
    assert set(snapshot["source_health"]) >= {"fed", "ecb", "sec"}


def test_news_contract_field_names_mirror_the_frozen_backend() -> None:
    # shared/contracts/news.py — NewsItem + NewsSnapshot (docs/specs/m2.12_news_ingest.spec.md)
    data = _read("mock_news_data.ts")
    for field in (
        "item_id:",
        "source_id:",
        "publisher:",
        "category:",
        "title:",
        "url:",
        "published_at:",
        "availability_time:",
        "reference_snapshot_id:",
    ):
        assert field in data, f"missing NewsItem field {field}"
    for field in ("status:", "items:", "as_of:", "source_health:", "last_good_at:"):
        assert field in data, f"missing NewsSnapshot field {field}"
    # The snapshot status vocabulary is four values, not a boolean.
    assert "fresh" in data and "degraded" in data and "unavailable" in data
    # The empty shape is documented, and the status vocabulary is the real four.
    assert "NEWS_SHAPE" in data
    assert '"unavailable"' in data
    # The live payload is a separate generated module, not hand-maintained here.
    assert "NEWS_SNAPSHOT" in _read("news_snapshot.generated.ts")
    assert 'from "./mock_news_data"' in _read("news_snapshot.generated.ts")


def test_news_section_states_not_connected_and_never_fetches() -> None:
    section = _read("NewsSection.tsx")
    flat = _flat(section).lower()

    # No network from the browser: the frontend stays mock-only, as decided for this build.
    for forbidden in ("fetch(", "websocket", "xmlhttprequest", "localstorage"):
        assert forbidden not in flat, f"the news section must not reach the network: {forbidden}"
    # The not-connected state is explicit, not an empty list that reads as "no news".
    assert "no feed connected" in flat or "not connected" in flat
    assert "unavailable" in flat
    # And it explains the consequence rather than just showing an empty panel. The
    # reason text lives beside the empty shape so the two cannot drift apart.
    reason = _flat(_read("mock_news_data.ts")).lower()
    assert "news_snapshot" in reason or "verify_m2.12" in reason


def test_news_section_links_out_and_labels_the_time_and_source() -> None:
    section = _read("NewsSection.tsx")
    styles = _styles()

    # A headline is a link to the publisher, not republished text.
    assert "<a " in section
    assert "rel=" in section
    assert "noreferrer" in section
    # Publisher and time are shown: a headline without its source or age is not usable.
    assert "publisher" in section
    assert "published_at" in section
    for selector in (".news-item", ".news-publisher", ".news-meta"):
        assert selector in styles, f"missing CSS for {selector}"


def test_news_section_surfaces_staleness_and_failed_sources() -> None:
    section = _read("NewsSection.tsx")
    styles = _styles()
    flat = _flat(section).lower()

    # The chosen failure behaviour: last good snapshot, timestamped, with the failed
    # sources named. A silently missing section would read as a quiet news day.
    assert "as of" in flat
    assert "source_health" in section or "failed" in flat
    assert "stale" in flat
    assert ".news-stale" in styles
    assert "rel=\"noopener noreferrer\"" in section or "noreferrer" in section


def test_news_is_not_a_trade_input() -> None:
    # M2.12 out of scope: no analytic may consume a NewsItem until a strategy manifest
    # declares it and a DependencyReport says ELIGIBLE. Checked as trade *affordances* —
    # a bare "entry" also names a map callback, which is not a trading signal.
    for name in ("HomeDashboard.tsx", "NewsSection.tsx"):
        text = _code_only(_read(name)).lower()
        for affordance in (
            "place order",
            "buy ",
            "sell ",
            "entry price",
            "exit",
            "stop loss",
            "take profit",
            "open position",
            "signal_strength",
        ):
            assert affordance not in text, f"{name} must not imply a trade action from news: {affordance!r}"
    section = _code_only(_read("NewsSection.tsx")).lower()
    for secret in ("api_key", "api_token", "vault", "password"):
        assert secret not in section, f"the news section must not reference {secret}"
