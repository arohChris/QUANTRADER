import { useMemo, useRef, useState } from "react";
import { CornerDownLeft, FileSearch, Search, Sparkles, Terminal } from "lucide-react";
import { MOCK_INSTRUMENTS, TIMEFRAME_WINDOW, pathPhase, withSymbol, type ExperimentMarket } from "./experiment_market";

type Theme = "dark" | "light";

// Command language is taken from the frozen design authority
// (frontend_spec.md §5 and ui_ux_design_plan.md §7.5) so the surface is ready for
// POST /api/v1/commands when M10.3 freezes. Nothing here calls a backend.
type Command = {
  command: string;
  label: string;
  question: string;
  summary: string;
};

export const RESEARCH_COMMANDS: Command[] = [
  { command: "/analyze", label: "Analyze", question: "/analyze FX.EURUSD", summary: "read one asset end to end" },
  { command: "/compare", label: "Compare", question: "/compare FX.EURUSD CRYPTO.BTCUSD", summary: "two assets side by side" },
  { command: "/show", label: "Show", question: "/show risk", summary: "what the risk state is" },
  { command: "/open", label: "Open", question: "/open FX.EURUSD", summary: "focus an asset" },
  { command: "/find", label: "Find", question: "/find momentum opportunities", summary: "scan the catalogue" },
  { command: "/explain", label: "Explain", question: "/explain today's market move", summary: "why price moved" },
  { command: "/why", label: "Why", question: "/why did this signal trigger?", summary: "the documented hero question" },
  { command: "/research", label: "Research", question: "/research crude", summary: "topic research" },
  { command: "/help", label: "Help", question: "/help", summary: "command list" },
];

// Predetermined questions, phrased the way the manual phrases them.
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

// Deterministic mock analyst on the same time path as the charts, shaped like the
// documented answer: context → technical → assessment → evidence → provenance.
// Read-only: it explains, summarises, retrieves and answers. It never trades.
export function buildContextReply(question: string, base: ExperimentMarket, symbol: string): ChatMessage {
  const ask = question.toLowerCase();

  if (ask.startsWith("/help") || ask.includes("what can you answer")) {
    return {
      id: 0,
      role: "assistant",
      command: "/help",
      status: "COMPLETE",
      text: "I explain, summarise, retrieve and answer — read-only. These commands are wired to a local mock path, not a model.",
      sections: [
        {
          title: "Commands",
          blocks: RESEARCH_COMMANDS.map((entry) => ({ label: entry.command, value: `${entry.label} — ${entry.summary}` })),
        },
      ],
      followUps: PRESET_QUESTIONS.slice(0, 3),
    };
  }

  if (ask.startsWith("/find") || ask.includes("strongest") || ask.includes("opportunit")) {
    const ranked = MOCK_INSTRUMENTS.map((entry) => {
      const read = readMarket(base, entry.symbol);
      return { symbol: entry.symbol, change: read.change, phase: read.phase };
    }).sort((a, b) => b.change - a.change);

    return {
      id: 0,
      role: "assistant",
      command: "/find",
      status: "COMPLETE",
      text: `Scanned ${ranked.length} instruments on the mock path. Leader ${ranked[0].symbol} at ${ranked[0].change >= 0 ? "+" : ""}${ranked[0].change.toFixed(2)}%.`,
      sections: [
        {
          title: "Ranked by trend of the day",
          blocks: ranked.slice(0, 6).map((entry, index) => ({
            label: `${index + 1}. ${entry.symbol}`,
            value: `${entry.change >= 0 ? "+" : ""}${entry.change.toFixed(2)}% · ${entry.phase}`,
          })),
        },
      ],
      evidence: [
        { label: "every instrument in the catalogue", met: true },
        { label: "single deterministic time path", met: true },
        { label: "live feed or model", met: false },
      ],
      followUps: ["/compare FX.EURUSD CRYPTO.BTCUSD", "Why is oil moving today?"],
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

  if (ask.startsWith("/show") || ask.includes("risk")) {
    return {
      id: 0,
      role: "assistant",
      command: "/show risk",
      status: "INSUFFICIENT_CONTEXT",
      text: "Risk state is not connected in this build. The mock path has no account, no position and no exposure, so there is nothing to size.",
      sections: [
        {
          title: "What would answer this",
          blocks: [
            { label: "needs", value: "M5 risk decision for a tenant" },
            { label: "has", value: "market path only" },
            { label: "status", value: "INSUFFICIENT_CONTEXT" },
          ],
        },
      ],
      evidence: [
        { label: "account or tenant in session", met: false },
        { label: "position or exposure", met: false },
      ],
      followUps: ["What is the price doing right now?", "/help"],
    };
  }

  if (ask.startsWith("/why") || ask.includes("signal trigger")) {
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
      followUps: ["Why is oil moving today?", "/analyze FX.EURUSD"],
    };
  }

  const read = readMarket(base, symbolFrom(question, symbol));
  const explain = ask.startsWith("/explain") || ask.includes("why is") || ask.includes("moving");

  return {
    id: 0,
    role: "assistant",
    command: explain ? "/explain" : "/analyze",
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

export default function ResearchChat({ base, symbol }: { base: ExperimentMarket; symbol: string }) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const nextId = useRef(1);

  const activeCommand = useMemo(
    () => RESEARCH_COMMANDS.find((entry) => draft.trim().startsWith(entry.command)) ?? null,
    [draft],
  );

  const send = (text: string) => {
    const clean = text.trim();
    if (!clean) {
      return;
    }
    const you: ChatMessage = { id: nextId.current++, role: "you", text: clean };
    const reply = { ...buildContextReply(clean, base, symbol), id: nextId.current++ };
    setMessages((current) => [...current, you, reply]);
    setDraft("");
  };

  return (
    <section className="research-view" aria-label="Research assistant">
      <header className="research-head">
        <h1>
          <Sparkles size={18} /> Research
        </h1>
        <p className="muted">
          Explain, summarise, retrieve, answer — read-only, never a trade action. Answers read the same clock
          as the charts, so nothing here can disagree with what you see.
        </p>
        <p className="research-mock-badge">mock analyst · no model or feed connected</p>
      </header>

      <div className="chat-log" role="log" aria-live="polite" aria-label="Research answers">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <Terminal size={22} />
            <p className="data-large">Ask a question, or run a command</p>
            <p className="muted">Every answer returns context, evidence and provenance. Nothing is sent anywhere.</p>
          </div>
        ) : (
          messages.map((message) => <Answer key={message.id} message={message} onFollowUp={send} />)
        )}
      </div>

      <div className="command-bar" role="group" aria-label="Research commands">
        {RESEARCH_COMMANDS.map((entry) => (
          <button
            key={entry.command}
            className="command-chip"
            type="button"
            title={entry.summary}
            onClick={() => send(entry.question)}
          >
            {entry.command}
            <span className="command-label">{entry.label}</span>
          </button>
        ))}
      </div>

      <div className="preset-bar" role="group" aria-label="Preset questions">
        {PRESET_QUESTIONS.map((question) => (
          <button key={question} className="preset-chip" type="button" onClick={() => send(question)}>
            <Search size={13} />
            {question}
          </button>
        ))}
      </div>

      <form
        className="chat-input"
        onSubmit={(event) => {
          event.preventDefault();
          send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask, or type /analyze /compare /show /open /find /explain /why /research /help"
          aria-label="Ask the research assistant"
        />
        <button type="submit" aria-label="Send question" title="Send">
          <CornerDownLeft size={16} />
        </button>
        {activeCommand ? <span className="chat-hint">{activeCommand.summary}</span> : null}
      </form>
    </section>
  );
}

// Not a chat bubble: a structured answer with context sections, an evidence bar,
// read-only actions and the mandatory provenance footer.
function Answer({ message, onFollowUp }: { message: ChatMessage; onFollowUp: (text: string) => void }) {
  if (message.role === "you") {
    return (
      <article className="answer answer-you">
        <p className="answer-question">{message.text}</p>
      </article>
    );
  }

  const met = message.evidence?.filter((item) => item.met).length ?? 0;
  const total = message.evidence?.length ?? 0;

  return (
    <article className="answer">
      <header className="answer-head">
        <span className="answer-source">
          <Sparkles size={13} /> research
        </span>
        {message.command ? <code className="chat-command">{message.command}</code> : null}
        {message.status ? <span className={message.status === "COMPLETE" ? "status-chip is-complete" : "status-chip is-partial"}>{message.status}</span> : null}
      </header>

      <p className="answer-lead">{message.text}</p>

      {message.sections?.map((section) => (
        <section className="answer-section" key={section.title}>
          <h3>{section.title}</h3>
          <dl className="chat-blocks">
            {section.blocks.map((block) => (
              <div key={`${section.title}-${block.label}`}>
                <dt>{block.label}</dt>
                <dd>{block.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      {message.evidence?.length ? (
        <div className="evidence-bar" aria-label="Evidence">
          <span className="evidence-label">Evidence</span>
          <span className="evidence-track">
            <span className="evidence-fill" style={{ width: `${total ? (met / total) * 100 : 0}%` }} />
          </span>
          <span className="evidence-count">
            {met}/{total}
          </span>
          {message.evidence.map((item) => (
            <span className={item.met ? "evidence-item is-met" : "evidence-item"} key={item.label}>
              {item.met ? "✓" : "—"} {item.label}
            </span>
          ))}
        </div>
      ) : null}

      <div className="answer-actions">
        <button className="action-chip" type="button" onClick={() => onFollowUp("Challenge this answer")}>
          Challenge
        </button>
        <button className="action-chip" type="button" onClick={() => onFollowUp("Deep research on this")}>
          <FileSearch size={13} /> Deep research
        </button>
        {message.followUps?.map((question) => (
          <button key={question} className="preset-chip" type="button" onClick={() => onFollowUp(question)}>
            {question}
          </button>
        ))}
      </div>

      <p className="answer-provenance">prompt v1 · mock model · data cut {baseCut(message)}</p>
    </article>
  );
}

function baseCut(message: ChatMessage): string {
  const found = message.sections?.flatMap((section) => section.blocks).find((block) => block.label === "as_of");
  return found?.value ?? "n/a";
}
