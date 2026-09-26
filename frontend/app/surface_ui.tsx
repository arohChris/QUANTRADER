// Shared presentation for the Trader surfaces. Formatting only:
// percent change and rounding are allowed client-side (frontend_spec.md:65-70);
// money, margin, exposure and risk percentages are backend-owned and passed in.
import type { ReactNode } from "react";
import type { OrderState } from "./mock_trader_data";

export function money(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

export function signedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function stateClass(state: OrderState): string {
  if (state === "filled") {
    return "state-chip is-filled";
  }
  if (state === "rejected" || state === "cancelled") {
    return "state-chip is-rejected";
  }
  if (state === "unknown") {
    return "state-chip is-unknown";
  }
  return "state-chip is-live";
}

export function SurfaceHead({
  title,
  subtitle,
  badge,
}: {
  title: string;
  subtitle: ReactNode;
  badge: string;
}) {
  return (
    <header className="surface-head">
      <h1>{title}</h1>
      <p className="muted">{subtitle}</p>
      <p className="surface-badge">{badge}</p>
    </header>
  );
}

export function SurfacePanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="surface-panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="surface-note">{children}</p>;
}

export function KeyValues({ rows }: { rows: { label: string; value: ReactNode; tone?: string }[] }) {
  return (
    <div className="kv-grid">
      {rows.map((row) => (
        <div key={row.label}>
          <span>{row.label}</span>
          <span className={row.tone ?? "mono"}>{row.value}</span>
        </div>
      ))}
    </div>
  );
}
