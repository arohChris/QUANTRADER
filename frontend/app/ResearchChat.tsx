import { useMemo, useRef, useState } from "react";
import { CornerDownLeft, FileSearch, Search, Sparkles, Terminal } from "lucide-react";
import { resolveCommand, type Command } from "./commands";
import CommandPicker from "./CommandPicker";
import { PRESET_QUESTIONS, buildContextReply, type ChatMessage } from "./research_analyst";
import type { ExperimentMarket } from "./experiment_market";

type Theme = "dark" | "light";

export default function ResearchChat({
  base,
  symbol,
  withCommands = false,
}: {
  base: ExperimentMarket;
  symbol: string;
  withCommands?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const nextId = useRef(1);

  const activeCommand = useMemo(() => resolveCommand(draft.trim().split(" ")[0] ?? "") ?? null, [draft]);

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

      {withCommands ? <CommandPicker onRun={send} /> : null}

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
          placeholder={
            withCommands
              ? "Ask a question, or type a command from the list"
              : "Ask the research assistant a question"
          }
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
