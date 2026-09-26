// Command registry for the frontend Command Center.
//
// WHY a registry and not a hardcoded list: the backend command layer
// (System_Development_Manual.md §22 M10.3, modules/10_M10_Experience.md:67-85) is
// `command/{parser, interpreter, registry, permissions, queue_manager}` with one file
// per command. Adding a command must never mean editing the UI, so the picker, the
// help listing, and the availability gates are all derived from this data.
//
// Decisions recorded with M10.4.8:
//   * `/indices` is NOT here. AssetClass is frozen to forex|crypto|commodity
//     (shared/contracts/instrument.py:9,23-25; AGENTS.md:28). Removed by human direction.
//   * `/trade` and `/confirm` are NOT here. They are the only write paths in the whole
//     system (System_Development_Manual.md:3197-3199) and this build has no write path.
//     Removed by human direction; they return with the M6 write contract.
//   * Matching is case-insensitive, so `/weekAhead` and `/weekahead` are ONE command.
//     The manual documents no case rule; this fills that gap.
//   * Every entry is `writes: false`. Read-only, per the M11 boundary
//     (explains / summarizes / retrieves / answers — never trades).
//   * Time-gated recaps stay VISIBLE with a reason instead of disappearing.

export type CommandGroup =
  | "primary"
  | "filtered"
  | "analysis"
  | "information"
  | "risk"
  | "recap"
  | "trade"
  | "system";

export type Gate =
  | "always"
  | "after_close"
  | "friday_after_close"
  | "month_end"
  | "quarter_end";

export type CommandArg = "asset" | "assets" | "count" | "none";

export type Command = {
  id: string;
  command: string;
  group: CommandGroup;
  summary: string;
  args: CommandArg;
  availability: Gate;
  writes: false;
};

export const COMMAND_GROUPS: { id: CommandGroup; label: string }[] = [
  { id: "primary", label: "Primary" },
  { id: "filtered", label: "Filtered views" },
  { id: "analysis", label: "Analysis" },
  { id: "information", label: "Information" },
  { id: "risk", label: "Risk" },
  { id: "recap", label: "Recap" },
  { id: "trade", label: "Trade" },
  { id: "system", label: "System" },
];

export const COMMANDS: Command[] = [
  { id: "marketday", command: "/marketday", group: "primary", summary: "full daily market research — the main activation", args: "none", availability: "always", writes: false, },
  { id: "top3", command: "/top3", group: "filtered", summary: "top 3 strongest setups", args: "none", availability: "always", writes: false, },
  { id: "danger", command: "/danger", group: "filtered", summary: "weakest / riskiest setups", args: "none", availability: "always", writes: false, },
  { id: "forex", command: "/forex", group: "filtered", summary: "forex only", args: "asset", availability: "always", writes: false, },
  { id: "crypto", command: "/crypto", group: "filtered", summary: "crypto only", args: "asset", availability: "always", writes: false, },
  { id: "commodities", command: "/commodities", group: "filtered", summary: "commodities only", args: "asset", availability: "always", writes: false, },
  { id: "deep", command: "/deep", group: "analysis", summary: "deep analysis on one asset", args: "asset", availability: "always", writes: false, },
  { id: "why", command: "/why", group: "analysis", summary: "explain the ranking for one asset", args: "asset", availability: "always", writes: false, },
  { id: "technical", command: "/technical", group: "analysis", summary: "technical analysis explanation", args: "asset", availability: "always", writes: false, },
  { id: "fundamental", command: "/fundamental", group: "analysis", summary: "fundamental analysis explanation", args: "asset", availability: "always", writes: false, },
  { id: "compare", command: "/compare", group: "analysis", summary: "compare two assets", args: "assets", availability: "always", writes: false, },
  { id: "news", command: "/news", group: "information", summary: "market-moving news", args: "asset", availability: "always", writes: false, },
  { id: "calendar", command: "/calendar", group: "information", summary: "economic calendar", args: "none", availability: "always", writes: false, },
  { id: "sources", command: "/sources", group: "information", summary: "data sources used", args: "none", availability: "always", writes: false, },
  { id: "resources", command: "/resources", group: "information", summary: "curated articles, blogs, podcasts", args: "none", availability: "always", writes: false, },
  { id: "session", command: "/session", group: "information", summary: "current session, time remaining, suitable assets", args: "none", availability: "always", writes: false, },
  { id: "risk", command: "/risk", group: "risk", summary: "risk and correlation review", args: "asset", availability: "always", writes: false, },
  { id: "avoid", command: "/avoid", group: "risk", summary: "no-trade assets and why", args: "none", availability: "always", writes: false, },
  { id: "update", command: "/update", group: "recap", summary: "refresh data and setup validity", args: "none", availability: "always", writes: false, },
  { id: "dayrecap", command: "/dayrecap", group: "recap", summary: "daily paper-trading review", args: "none", availability: "after_close", writes: false, },
  { id: "weekrecap", command: "/weekrecap", group: "recap", summary: "weekly review", args: "none", availability: "friday_after_close", writes: false, },
  { id: "monthrecap", command: "/monthrecap", group: "recap", summary: "monthly review", args: "none", availability: "month_end", writes: false, },
  { id: "quarterlyrecap", command: "/quarterlyrecap", group: "recap", summary: "quarterly review", args: "none", availability: "quarter_end", writes: false, },
  { id: "weekahead", command: "/weekahead", group: "recap", summary: "weekly forecast and key events (case-insensitive)", args: "none", availability: "always", writes: false, },
  { id: "tradetop", command: "/tradetop", group: "trade", summary: "trade analysis for the top N setups", args: "count", availability: "always", writes: false, },
  { id: "help", command: "/help", group: "system", summary: "command list", args: "none", availability: "always", writes: false, },
  { id: "journal", command: "/journal", group: "system", summary: "create a journal entry", args: "none", availability: "always", writes: false, },
];

// Case-insensitive lookup: `/weekAhead`, `/WEEKAHEAD` and `/weekahead` are one command.
export function resolveCommand(input: string): Command | undefined {
  const needle = input.trim().toLowerCase();
  return COMMANDS.find((entry) => entry.command === needle);
}

const UTC_HOURS_PER_DAY = 24;

// Session close is 17:00 UTC on weekdays; recaps are gated on that boundary.
export const SESSION_CLOSE_HOUR_UTC = 17;

function isWeekend(now: Date): boolean {
  const day = now.getUTCDay();
  return day === 0 || day === 6;
}

function lastDayOfMonth(now: Date): number {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
}

// Returns why a command is unavailable, or null when it can run. A gated command is
// never hidden — the reason is shown so the reader knows it exists.
export function gateReason(command: Command, now: Date): string | null {
  const hour = now.getUTCHours();
  const pastClose = hour >= SESSION_CLOSE_HOUR_UTC;
  const day = now.getUTCDate();
  const month = now.getUTCMonth();
  const quarterEndMonth = Math.floor(month / 3) * 3 + 2;

  switch (command.availability) {
    case "always":
      return null;
    case "after_close":
      return pastClose && !isWeekend(now)
        ? null
        : `not available until ${String(SESSION_CLOSE_HOUR_UTC).padStart(2, "0")}:00 UTC on a weekday — INSUFFICIENT_CONTEXT`;
    case "friday_after_close":
      return now.getUTCDay() === 5 && pastClose
        ? null
        : "not available until Friday after 17:00 UTC — INSUFFICIENT_CONTEXT";
    case "month_end":
      return day >= lastDayOfMonth(now) - 1 && pastClose
        ? null
        : "not available until the last two days of the month after close — INSUFFICIENT_CONTEXT";
    case "quarter_end":
      return month === quarterEndMonth && day >= lastDayOfMonth(now) - 1 && pastClose
        ? null
        : "not available in the final days of a quarter after close — INSUFFICIENT_CONTEXT";
    default:
      return null;
  }
}

export function availableCommands(now: Date): { command: Command; reason: string | null }[] {
  return COMMANDS.map((command) => ({ command, reason: gateReason(command, now) }));
}

// Argument hint shown under the picker, derived from the registry.
export function argHint(command: Command): string {
  switch (command.args) {
    case "asset":
      return `${command.command} <ASSET>`;
    case "assets":
      return `${command.command} <ASSET1> <ASSET2>`;
    case "count":
      return `${command.command} <N>`;
    default:
      return command.command;
  }
}

export const TOTAL_COMMANDS = COMMANDS.length;
export const GATE_COUNT = UTC_HOURS_PER_DAY; // documented, kept for readability of gates
