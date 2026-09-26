// Command picker: the control that types a registry command into the Research chat.
//
// WHY this is a picker and not a page: the human decided commands belong with research,
// on the research surface, for Trader and Admin (2026-09-26). So this component owns no
// answer of its own — it resolves a command from the registry, checks its gate, and
// hands the text to the chat, which already renders the structured answer.
//
// Read-only: every registry entry is `writes: false` and there is no submit path.
import { useMemo, useState } from "react";
import { CircleSlash, Info, Search, Terminal } from "lucide-react";
import {
  COMMAND_GROUPS,
  argHint,
  availableCommands,
  gateReason,
  resolveCommand,
  type Command,
  type CommandGroup,
} from "./commands";
import { Note } from "./surface_ui";

export default function CommandPicker({ onRun, now }: { onRun: (text: string) => void; now?: Date }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  // Read once per mount: session gates are evaluated against the same instant the
  // screen rendered, so a row cannot flip state under the reader's cursor.
  const at = useMemo(() => now ?? new Date(), [now]);
  const entries = useMemo(() => availableCommands(at), [at]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return entries;
    }
    return entries.filter(
      ({ command }) => command.command.includes(needle) || command.summary.toLowerCase().includes(needle),
    );
  }, [entries, query]);

  const run = (command: Command) => {
    onRun(argHint(command));
    setOpen(false);
    setQuery("");
  };

  const runTyped = () => {
    const resolved = resolveCommand(query);
    if (resolved) {
      run(resolved);
    }
  };

  return (
    <div className="command-picker-block">
      <div className="command-runner">
        <button
          className="command-trigger"
          type="button"
          aria-label="Command picker"
          aria-expanded={open}
          aria-haspopup="listbox"
          onClick={() => setOpen((current) => !current)}
        >
          <Terminal size={15} />
          {open ? "Close command list" : "Open command list"}
        </button>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              runTyped();
            }
          }}
          placeholder="Type a command, or open the list"
          aria-label="Command input"
        />
        <button className="mini-button" type="button" onClick={runTyped} disabled={!query.trim()}>
          Run
        </button>
      </div>

      {open ? (
        <div className="command-picker" role="listbox" aria-label="Command list" aria-multiselectable="false">
          {filtered.length === 0 ? (
            <p className="muted command-empty">No command matches that text.</p>
          ) : (
            COMMAND_GROUPS.map((group) => {
              const inGroup = filtered.filter(({ command }) => command.group === (group.id as CommandGroup));
              if (inGroup.length === 0) {
                return null;
              }
              return (
                <section className="command-group" key={group.id}>
                  <h3>{group.label}</h3>
                  {inGroup.map(({ command }) => {
                    const reason = gateReason(command, at);
                    return (
                      <button
                        className="command-row"
                        type="button"
                        key={command.id}
                        role="option"
                        aria-selected={false}
                        aria-disabled={reason !== null}
                        disabled={reason !== null}
                        title={reason ?? command.summary}
                        onClick={() => run(command)}
                      >
                        <span className="command-name mono">{argHint(command)}</span>
                        <span className="command-summary">{command.summary}</span>
                        {reason ? (
                          <span className="command-gate">
                            <CircleSlash size={12} />
                            {reason}
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </section>
              );
            })
          )}
        </div>
      ) : null}

      <Note>
        <Info size={12} /> Every command is read-only research. Answers are mock previews from the same
        clock as the charts, and the write commands are absent from this build by design, so nothing
        here can become an order.
      </Note>
    </div>
  );
}
