"""Spec: M10.4.8 Command Center (frontend surface for the M10.3 command layer).

The backend command layer is M10.3 and is still deferred
(System_Development_Manual.md §22 M10.3, modules/10_M10_Experience.md:67-85). This
slice is the FRONTEND surface only: a registry-driven picker, availability gating
with reasons, and a mock result. No command here can write.

Decisions recorded with this spec:
  * The picker is driven by a data registry, never by a hardcoded list in the UI.
  * `/indices` is absent — AssetClass is frozen to forex|crypto|commodity
    (shared/contracts/instrument.py:9,23-25; AGENTS.md:28). Human directed removal.
  * `/trade` and `/confirm` are absent — they are the only write paths in the whole
    system (System_Development_Manual.md:3197-3199) and this build has no write path.
    Human directed removal.
  * Command matching is case-insensitive, so `/weekAhead` and `/weekahead` are one
    command. The manual documents no case rule; this fills that gap.
  * Recap commands are time-gated and show WHY they are unavailable rather than
    hiding.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"

EXPECTED = {
    "/marketday",
    "/top3",
    "/danger",
    "/forex",
    "/crypto",
    "/commodities",
    "/deep",
    "/why",
    "/technical",
    "/fundamental",
    "/compare",
    "/news",
    "/calendar",
    "/sources",
    "/resources",
    "/session",
    "/risk",
    "/avoid",
    "/update",
    "/dayrecap",
    "/weekrecap",
    "/monthrecap",
    "/quarterlyrecap",
    "/weekahead",
    "/tradetop",
    "/help",
    "/journal",
}

REMOVED = ("/indices", "/trade", "/confirm")


def _registry() -> str:
    return (_FRONTEND / "app" / "commands.ts").read_text(encoding="utf-8")


def test_registry_holds_the_agreed_command_set() -> None:
    text = _registry()
    for command in EXPECTED:
        assert f'command: "{command}"' in text, f"missing command {command}"
    for removed in REMOVED:
        assert f'command: "{removed}"' not in text, f"{removed} must not ship"


def test_registry_is_data_driven_with_groups_and_no_writes() -> None:
    text = _registry()
    assert "export const COMMANDS" in text
    assert "COMMAND_GROUPS" in text
    # Counted on the entry terminator so the `writes: false` type field is not counted.
    assert text.count("writes: false, },") == 27
    assert "writes: true" not in text
    for field in ("id:", "command:", "group:", "summary:", "args:", "availability:"):
        assert field in text, f"registry entry missing {field}"


def test_command_matching_is_case_insensitive() -> None:
    text = _registry()
    assert "toLowerCase()" in text
    assert "resolveCommand" in text
    # The duplicate spelling collapses into one entry rather than two.
    assert '"/weekahead"' in text
    assert 'command: "/weekAhead"' not in text


def test_recap_commands_are_time_gated_with_reasons() -> None:
    text = _registry()
    for gate in ("after_close", "friday_after_close", "month_end", "quarter_end"):
        assert gate in text, f"missing availability gate {gate}"
    for command in ("/dayrecap", "/weekrecap", "/monthrecap", "/quarterlyrecap"):
        assert f'command: "{command}"' in text
    assert "gateReason" in text
    assert "INSUFFICIENT_CONTEXT" in text or "not available until" in text


def test_commands_live_inside_research_for_trader_and_admin_only() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    chat = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    picker = (_FRONTEND / "app" / "CommandPicker.tsx").read_text(encoding="utf-8")
    nav = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")

    # Human decision 2026-09-26: commands belong with research, on the research page,
    # for Trader/Admin. There is no separate Commands page and no separate nav item.
    assert (_FRONTEND / "app" / "CommandPicker.tsx").is_file()
    assert not (_FRONTEND / "app" / "CommandCenter.tsx").exists(), "the standalone page was dissolved into Research"
    assert 'path="/app/commands"' not in app
    assert "/app/commands" not in nav
    assert "CommandPicker" in chat
    assert "withCommands" in chat
    assert "withCommands={role !== \"Viewer\"}" in app

    # The picker reads the registry through its exported accessors; it must not restate
    # the command list.
    assert 'from "./commands"' in picker
    for accessor in ("availableCommands", "COMMAND_GROUPS", "gateReason", "argHint"):
        assert accessor in picker, f"{accessor} must come from the registry, not the UI"

    # Picker affordances: grouped listbox, search, disabled-with-reason.
    assert 'aria-label="Command picker"' in picker
    assert 'role="listbox"' in picker
    assert "aria-disabled" in picker
    assert "gateReason" in picker
    assert ".command-" in (_FRONTEND / "app" / "styles" / "commands.css").read_text(encoding="utf-8")


def test_picker_is_read_only_and_mock_only() -> None:
    for name in ("CommandPicker.tsx", "commands.ts", "ResearchChat.tsx"):
        text = (_FRONTEND / "app" / name).read_text(encoding="utf-8").lower()
        for forbidden in ("fetch(", "websocket", "authorization", "localstorage", "bearer"):
            assert forbidden not in text, f"{name} must stay mock-only"
    picker = (_FRONTEND / "app" / "CommandPicker.tsx").read_text(encoding="utf-8")
    assert "preview" in picker.lower()
    assert "mock" in picker.lower()
