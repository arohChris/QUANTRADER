// Mock research analyst: the deterministic answer builder for the Research surface.
//
// WHY this is its own module: it is pure logic over the mock time path with no JSX, and
// keeping it out of ResearchChat.tsx holds that file under the 20 kB modularity guard the
// shell spec enforces. Nothing here talks to a network, a model, or a write path.
import { MOCK_INSTRUMENTS, TIMEFRAME_WINDOW, pathPhase, withSymbol, type ExperimentMarket } from "./experiment_market";
import { COMMAND_GROUPS, COMMANDS, resolveCommand, type Command } from "./commands";

// Predetermined questions, phrased the way the manual phrases them. Natural language,
// not commands — the command language is the M10.4.8 registry.
export const PRESET_QUESTIONS: string[] = [
  "Why did this signal trigger?",
  "Why is oil moving today?",
  "What is the price doing right now?",
  "How deep is the drawdown from the high?",
  "Which assets are strongest today?",
  "How fresh and complete is this data?",
];

// Answer status vocabulary from ui_ux_design_plan.md §8.3.
type Status = "COMPLETE" | "UNVERIFIED" | "INSUFFICIENT_CONTEXT";

type ContextBlock = { label: string; value: string };

export type ChatMessage = {
  id: number;
  role: "you" | "assistant";
  text: string;
  command?: string;
  status?: Status;
  sections?: { title: string; blocks: ContextBlock[] }[];
  evidence?: { label: string; met: boolean }[];
  followUps?: string[];
};

function decimalsFor(symbol: string): number {
  return MOCK_INSTRUMENTS.find((entry) => entry.symbol === symbol)?.decimals ?? 4;
}

function price(value: number, symbol: string): string {
  return value.toFixed(decimalsFor(symbol));
}

function symbolFrom(text: string, fallback: string): string {
  const found = MOCK_INSTRUMENTS.find((entry) =>
    text.toUpperCase().includes(entry.symbol.toUpperCase()) || text.toUpperCase().includes(entry.label.replace("/", "")),
  );
  return found?.symbol ?? fallback;
}

function readMarket(base: ExperimentMarket, symbol: string) {
  const market = withSymbol(base, symbol);
  const window = TIMEFRAME_WINDOW[market.timeframe];
  const visible = market.points.slice(-window);
  const first = visible[0]?.close ?? 1;
  const last = visible.at(-1)?.close ?? first;
  const high = Math.max(...visible.map((point) => point.close));
  const low = Math.min(...visible.map((point) => point.close));
  return {
    market,
    window,
    first,
    last,
    high,
    low,
    change: ((last - first) / first) * 100,
    drawdown: ((last - high) / high) * 100,
    rangePosition: high === low ? 50 : ((last - low) / (high - low)) * 100,
    phase: pathPhase(market.points.length),
  };
}

// Commands whose data source is not connected in this build. Each says what it needs
// instead of inventing content. Presentation honesty, not a capability claim.
const NO_DATA_COMMANDS = new Map<string, string>([
  ["news", "a news feed — M2 ingest does not carry headlines in this build"],
  ["calendar", "an economic calendar — M2b TradingCalendar is not built"],
  ["sources", "the source registry — M2 lineage is not exposed to this screen"],
  ["resources", "a curated content index — not part of this build"],
  ["session", "the M2b session calendar — indicative hours only, no authoritative clock"],
  ["journal", "a journal store — M7 ledger is not connected"],
  ["update", "a live refresh — the mock path steps on a timer, not on demand"],
  ["dayrecap", "paper-trade records — there are no fills in this build"],
  ["weekrecap", "paper-trade records — there are no fills in this build"],
  ["monthrecap", "paper-trade records — there are no fills in this build"],
  ["quarterlyrecap", "paper-trade records — there are no fills in this build"],
  ["risk", "an M5 risk decision for a tenant — no account or exposure in this build"],
  ["avoid", "an M5 risk decision plus a signal — no account in this build"],
  ["weekahead", "a forward calendar of scheduled events — not connected"],
  ["fundamental", "fundamental data — no filings or macro series in this build"],
]);

// Deterministic mock analyst on the same time path as the charts, shaped like the
// documented answer: context → technical → assessment → evidence → provenance.
// Read-only: it explains, summarises, retrieves and answers. It never trades.
//
// The command language is the M10.4.8 registry, resolved case-insensitively. A command
// with no data behind it in this build says so with INSUFFICIENT_CONTEXT rather than
// inventing news, a calendar, or a journal entry.
export function buildContextReply(question: string, base: ExperimentMarket, symbol: string): ChatMessage {
  const ask = question.toLowerCase().trim();
  const head = ask.split(" ")[0];
  const registered: Command | undefined = resolveCommand(head);
  const id = registered?.id ?? "";

  if (id === "help") {
    return {
      id: 0,
      role: "assistant",
      command: "/help",
      status: "COMPLETE",
      text: `I explain, summarise, retrieve and answer — read-only. ${COMMANDS.length} commands are registered; every one of them is a preview, and none of them can trade.`,
      sections: COMMAND_GROUPS.map((group) => ({
        title: group.label,
        blocks: COMMANDS.filter((entry) => entry.group === group.id).map((entry) => ({
          label: entry.command,
          value: entry.summary,
        })),
      })),
      followUps: PRESET_QUESTIONS.slice(0, 3),
    };
  }

  // Commands that need a feed, a calendar, or a tenant this build does not have.
  if (NO_DATA_COMMANDS.has(id)) {
    return {
      id: 0,
      role: "assistant",
      command: registered?.command ?? head,
      status: "INSUFFICIENT_CONTEXT",
      text: `${registered?.summary ?? "That command"} needs a source this build does not have. I will not invent one, so here is exactly what is missing.`,
      sections: [
        {
          title: "What would answer this",
          blocks: [
            { label: "needs", value: NO_DATA_COMMANDS.get(id) ?? "a connected source" },
            { label: "has", value: "mock price path only" },
            { label: "status", value: "INSUFFICIENT_CONTEXT" },
          ],
        },
      ],
      evidence: [
        { label: "command is registered", met: true },
        { label: "its data source connected", met: false },
      ],
      followUps: ["/help", "What is the price doing right now?"],
    };
  }

  if (id === "top3" || id === "tradetop") {
    const ranked = MOCK_INSTRUMENTS.map((entry) => {
      const read = readMarket(base, entry.symbol);
      return { symbol: entry.symbol, change: read.change, phase: read.phase };
    }).sort((a, b) => b.change - a.change);
    const shortlist = ranked.slice(0, 3);

    return {
      id: 0,
      role: "assistant",
      command: registered?.command ?? head,
      status: "COMPLETE",
      text: `Shortlist of ${shortlist.length} from ${ranked.length} instruments on the mock path. Best is ${shortlist[0].symbol} at ${shortlist[0].change >= 0 ? "+" : ""}${shortlist[0].change.toFixed(2)}%. Ranking is a preview only — no entry, stop, or reward is computed here.`,
      sections: [
        {
          title: "Shortlist",
          blocks: shortlist.map((entry, index) => ({
            label: `${index + 1}. ${entry.symbol}`,
            value: `${entry.change >= 0 ? "+" : ""}${entry.change.toFixed(2)}% · ${entry.phase}`,
          })),
        },
        {
          title: "Not computed here",
          blocks: [
            { label: "entry / stop / target", value: "backend owned — M4 signal, not this screen" },
            { label: "reward-to-risk", value: "backend owned" },
            { label: "position size", value: "backend owned — M5 risk" },
          ],
        },
      ],
      evidence: [
        { label: "every instrument in the catalogue", met: true },
        { label: "single deterministic time path", met: true },
        { label: "live feed or model", met: false },
      ],
      followUps: ["/marketday", "Which assets are strongest today?"],
    };
  }

  if (ask.startsWith("/compare")) {
    const symbols = MOCK_INSTRUMENTS.filter((entry) => ask.includes(entry.symbol.toUpperCase()) || ask.includes(entry.label.replace("/", "")));
    const picks = symbols.length >= 2 ? symbols.slice(0, 2) : MOCK_INSTRUMENTS.slice(0, 2);
    const reads = picks.map((entry) => ({ entry, read: readMarket(base, entry.symbol) }));
    const spread = Math.abs(reads[0].read.change - reads[1].read.change);

    return {
      id: 0,
      role: "assistant",
      command: "/compare",
      status: "COMPLETE",
      text: `${reads[0].entry.symbol} and ${reads[1].entry.symbol} are ${spread.toFixed(2)} points apart on the day. Both read from the same time path.`,
      sections: reads.map(({ entry, read }) => ({
        title: entry.symbol,
        blocks: [
          { label: "last", value: price(read.last, entry.symbol) },
          { label: "day move", value: `${read.change >= 0 ? "+" : ""}${read.change.toFixed(2)}%` },
          { label: "from high", value: `${read.drawdown.toFixed(2)}%` },
          { label: "phase", value: read.phase },
        ],
      })),
      evidence: [
        { label: "same window and clock for both", met: true },
        { label: "correlation or hedge ratio computed", met: false },
      ],
      followUps: ["/analyze FX.EURUSD", "Which assets are strongest today?"],
    };
  }

  if (id === "why" || ask.includes("signal trigger")) {
    const read = readMarket(base, symbol);
    return {
      id: 0,
      role: "assistant",
      command: "/why",
      status: "UNVERIFIED",
      text: `No signal engine is running in this build, so I cannot attribute a trigger. What I can show is the price context a signal would have been read against: ${price(read.last, read.market.symbol)}, ${read.change >= 0 ? "+" : ""}${read.change.toFixed(2)}% on the day, ${read.drawdown.toFixed(2)}% from the high.`,
      sections: [
        {
          title: "Price context",
          blocks: [
            { label: "last", value: price(read.last, read.market.symbol) },
            { label: "day move", value: `${read.change >= 0 ? "+" : ""}${read.change.toFixed(2)}%` },
            { label: "from high", value: `${read.drawdown.toFixed(2)}%` },
            { label: "phase", value: read.phase },
            { label: "as_of", value: read.market.asOf },
          ],
        },
      ],
      evidence: [
        { label: "signal explainability from M4", met: false },
        { label: "price context from the same clock as the chart", met: true },
      ],
      followUps: ["Why is oil moving today?", "/deep FX.EURUSD"],
    };
  }

  // A registered command with no data in this build, or an unregistered one. Neither
  // gets an invented answer.
  if (ask.startsWith("/") && !["marketday", "deep", "technical", "top3", "tradetop", "why", "compare", "forex", "crypto", "commodities", "danger"].includes(id)) {
    const known = Boolean(registered);
    return {
      id: 0,
      role: "assistant",
      command: head,
      status: "INSUFFICIENT_CONTEXT",
      text: known
        ? `${registered?.summary ?? "That command"} is registered but its data source is not connected in this build.`
        : `${head} is not a registered command. I will not guess at what it meant.`,
      sections: [
        {
          title: known ? "What would answer this" : "Registered commands",
          blocks: known
            ? [{ label: "needs", value: "a connected source — see /help" }, { label: "status", value: "INSUFFICIENT_CONTEXT" }]
            : COMMAND_GROUPS.map((group) => ({
                label: group.label,
                value: COMMANDS.filter((entry) => entry.group === group.id)
                  .map((entry) => entry.command)
                  .join(" "),
              })),
        },
      ],
      evidence: [
        { label: known ? "command is registered" : "command is registered", met: known },
        { label: "its data source connected", met: false },
      ],
      followUps: ["/help", "What is the price doing right now?"],
    };
  }

  const read = readMarket(base, symbolFrom(question, symbol));
  const explain = ask.includes("why is") || ask.includes("moving");

  return {
    id: 0,
    role: "assistant",
    command: registered?.command ?? (explain ? "/deep" : "/marketday"),
    status: "COMPLETE",
    text: explain
      ? `${read.market.symbol} is ${read.change >= 0 ? "up" : "down"} ${Math.abs(read.change).toFixed(2)}% on the window and sits ${Math.abs(read.drawdown).toFixed(2)}% below the high, in the ${read.phase} phase.`
      : `${read.market.symbol} at ${price(read.last, read.market.symbol)}: ${read.change >= 0 ? "+" : ""}${read.change.toFixed(2)}% on the day, ${read.rangePosition.toFixed(0)}% up the day's range, ${read.phase} phase.`,
    sections: [
      {
        title: "Market context",
        blocks: [
          { label: "asset", value: `${read.market.symbol} · ${read.market.timeframe}` },
          { label: "last", value: price(read.last, read.market.symbol) },
          { label: "day range", value: `${price(read.low, read.market.symbol)} – ${price(read.high, read.market.symbol)}` },
          { label: "position in range", value: `${read.rangePosition.toFixed(0)}%` },
        ],
      },
      {
        title: "Technical",
        blocks: [
          { label: "day move", value: `${read.change >= 0 ? "+" : ""}${read.change.toFixed(2)}%` },
          { label: "from high", value: `${read.drawdown.toFixed(2)}%` },
          { label: "phase", value: read.phase },
          { label: "window", value: `${read.window} pts · ${read.market.timeframe}` },
        ],
      },
      {
        title: "Provenance",
        blocks: [
          { label: "as_of", value: read.market.asOf },
          { label: "snapshot", value: read.market.referenceSnapshotId },
          { label: "quality", value: read.market.quality },
          { label: "source", value: "local mock path" },
        ],
      },
    ],
    evidence: [
      { label: "same clock as the chart", met: true },
      { label: "as_of and snapshot shown", met: true },
      { label: "model inference", met: false },
    ],
    followUps: ["/compare FX.EURUSD CRYPTO.BTCUSD", "How fresh and complete is this data?"],
  };
}

// `withCommands` renders the M10.4.8 command picker above the log. Trader and Admin get
// it; Viewer gets the plain research chat, because the picker belongs to the tenant
// research surface even though every command in it is read-only.
