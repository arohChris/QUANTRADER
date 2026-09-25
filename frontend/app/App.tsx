import { useEffect, useRef, useState } from "react";
import { ArrowUp, Bell, ChevronLeft, ChevronRight, LayoutDashboard, Moon, Search, Sun } from "lucide-react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { mockSurfaces, mockUser } from "./mock_data";
import {
  MOCK_INSTRUMENTS,
  createExperimentMarket,
  stepExperimentMarket,
  withSymbol,
  withTimeframe,
  type ExperimentMarket,
} from "./experiment_market";
import HomeDashboard from "./HomeDashboard";
import type { MarketControls, Theme } from "./MarketPanel";
import MarketsGrid from "./MarketsGrid";
import ResearchChat from "./ResearchChat";
import TradePane, { type TradeMode } from "./TradePane";
import { NAV_BY_ROLE, NAV_ICONS, type AccessRole } from "./nav_config";

const ACCESS_ROLES = ["Viewer", "Trader", "Admin"] as const;
const THEME_CLASS: Record<Theme, string> = { dark: "theme-dark", light: "theme-light" };

function AccessPreview({ role }: { role: AccessRole }) {
  return (
    <section className="workspace access-placeholder" aria-label="Access preview">
      <div className="panel">
        <h2>{role} surface</h2>
        <p>Session data is not connected. Choose the access surface in the navigation bar.</p>
      </div>
    </section>
  );
}

export default function App() {
  const [role, setRole] = useState<AccessRole>(mockUser.role as AccessRole);
  const [market, setMarket] = useState<ExperimentMarket>(createExperimentMarket);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([MOCK_INSTRUMENTS[0].symbol]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [tradeMode, setTradeMode] = useState<TradeMode>("manual");
  const [theme, setTheme] = useState<Theme>("dark");
  const sidebar = useRef<HTMLElement>(null);
  const location = useLocation();
  const items = NAV_BY_ROLE[role];

  useEffect(() => {
    const timer = window.setInterval(() => setMarket((current) => stepExperimentMarket(current)), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    // Body carries the palette so page background, scrollbars and the shell agree.
    document.body.classList.toggle("theme-light", theme === "light");
  }, [theme]);

  useEffect(() => {
    // The sidebar closes on any outside click or Escape, not only via its own button.
    if (sidebarCollapsed) {
      return undefined;
    }

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!sidebar.current?.contains(target)) {
        setSidebarCollapsed(true);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSidebarCollapsed(true);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [sidebarCollapsed]);

  const controls: MarketControls = {
    selectedSymbols,
    onToggleSymbol: (symbol) =>
      setSelectedSymbols((current) =>
        current.includes(symbol)
          ? current.length > 1
            ? current.filter((entry) => entry !== symbol)
            : current
          : [...current, symbol],
      ),
    onTimeframe: (timeframe) => setMarket((current) => withTimeframe(current, timeframe)),
  };

  // Each selected asset is the same deterministic mock shape at its own price level.
  const markets = selectedSymbols.map((symbol) => withSymbol(market, symbol));

  return (
    <main className={`app-shell ${THEME_CLASS[theme]} theme-${theme}`}>
      <header className="app-header">
        <div className="header-left">
          <button
            className="icon-button"
            type="button"
            aria-expanded={!sidebarCollapsed}
            aria-label="Toggle navigation"
            title={sidebarCollapsed ? "Show navigation" : "Hide navigation"}
            onClick={() => setSidebarCollapsed((current) => !current)}
          >
            {sidebarCollapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
          </button>
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              QT
            </span>
            <span className="brand-text">
              <h1>QUANTRADER</h1>
              <span className="muted">research workspace</span>
            </span>
          </div>
        </div>
        <div className="header-actions">
          <button className="icon-button" type="button" aria-label="Search" title="Search" disabled>
            <Search size={18} />
          </button>
          <button className="icon-button has-badge" type="button" aria-label="Notifications" title="Notifications" disabled>
            <Bell size={18} />
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label="Toggle theme"
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <span className="avatar" title="Signed-out mock session">
            QT
          </span>
        </div>
      </header>
      <div className={`app-body ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
        {sidebarCollapsed ? null : (
          <aside className="sidebar" ref={sidebar}>
            <nav className="primary-nav" aria-label="Primary navigation">
              {items.map((item) => {
                const Icon = NAV_ICONS[item.href] ?? LayoutDashboard;
                return (
                  <Link
                    key={item.href}
                    className={location.pathname === item.href ? "is-active" : undefined}
                    to={item.href}
                    aria-current={location.pathname === item.href ? "page" : undefined}
                  >
                    <Icon size={17} />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </aside>
        )}
        <section className="content-area">
          <Routes>
            <Route path="/" element={role === "Viewer" ? <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} /> : <AccessPreview role={role} />} />
            <Route path="/login" element={<AccessPreview role={role} />} />
            <Route path="/markets" element={<MarketsGrid base={market} theme={theme} />} />
            <Route path="/research" element={<ResearchChat base={market} symbol={selectedSymbols[0]} />} />
            <Route path="/app/research" element={<ResearchChat base={market} symbol={selectedSymbols[0]} />} />
            <Route path="/app/markets" element={<MarketsGrid base={market} theme={theme} />} />
            <Route path="/app/trading" element={role === "Viewer" ? <AccessPreview role={role} /> : <TradePane market={market} theme={theme} controls={controls} markets={markets} tradeMode={tradeMode} onTradeMode={setTradeMode} />} />
            <Route path="/app" element={role === "Viewer" ? <AccessPreview role={role} /> : <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} />} />
            <Route path="/app/*" element={role === "Viewer" ? <AccessPreview role={role} /> : <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} />} />
            <Route path="/admin" element={<AccessPreview role={role} />} />
            <Route path="/admin/*" element={<AccessPreview role={role} />} />
            <Route path="*" element={<AccessPreview role={role} />} />
          </Routes>
        </section>
      </div>
      <footer className="app-footer" aria-label="Site footer">
        <div className="footer-inner">
          <button
            className="back-to-top"
            type="button"
            title="Back to top"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          >
            <ArrowUp size={14} />
            Back to top
          </button>
          <section className="footer-story" aria-label="Project story">
            <p className="story-beat">
              <strong>Built</strong> Research-grade scaffolding: data contracts, risk gates, and a workspace
              where a market is read once and reused everywhere.
            </p>
            <p className="story-beat">
              <strong>Challenge</strong> One trader, one strategy, one honest number — expectancy after fees.
              Until that is proven out-of-sample, nothing here is allowed to trade.
            </p>
            <p className="story-beat">
              <strong>Need</strong> Time and real data to validate the edge, then capital to size it.
            </p>
          </section>
          <div className="footer-main">
            <nav className="footer-nav" aria-label="Footer navigation">
              <div className="access-bar footer-group">
                <h2 className="footer-heading">Access</h2>
                <label htmlFor="role">Role</label>
                <select id="role" value={role} onChange={(event) => setRole(event.target.value as AccessRole)}>
                  {ACCESS_ROLES.map((access) => (
                    <option key={access} value={access}>
                      {access}
                    </option>
                  ))}
                </select>
              </div>
              <div className="footer-group">
                <h2 className="footer-heading">Workspace</h2>
                <Link to="/app/trading">Trade pane</Link>
                <Link to="/app/markets">Markets</Link>
                <Link to="/app/research">Research</Link>
              </div>
              <div className="footer-group">
                <h2 className="footer-heading">Build</h2>
                <p className="muted">{mockSurfaces.join(" · ")}</p>
                <p className="muted">Mock data · no broker connected</p>
              </div>
              <div className="footer-group">
                <h2 className="footer-heading">Legal</h2>
                <Link to="/legal/privacy">Privacy</Link>
                <Link to="/legal/terms">Terms</Link>
                <Link to="/legal/status">Status</Link>
              </div>
            </nav>
          </div>
          <div className="footer-legal">
            <span className="muted">
              © {new Date().getFullYear()} QUANTRADER. Research build — no broker connection, no trade is submitted.
            </span>
            <nav className="footer-legal-links" aria-label="Legal">
              <Link to="/legal/privacy">Privacy</Link>
              <Link to="/legal/terms">Terms</Link>
              <Link to="/legal/status">Status</Link>
            </nav>
          </div>
        </div>
      </footer>
    </main>
  );
}
