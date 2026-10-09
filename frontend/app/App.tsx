import { useEffect, useMemo, useRef, useState } from "react";
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
import OrdersSurface from "./OrdersSurface";
import PortfolioSurface from "./PortfolioSurface";
import SettingsSurface from "./SettingsSurface";
import { ACCESS_ROLES, NAV_BY_ROLE, NAV_ICONS, type AccessRole, type NavItem } from "./nav_config";
import SiteFooter from "./SiteFooter";
import AdminTradingSurface from "./AdminTradingSurface";
import AdminRiskSurface from "./AdminRiskSurface";
import AdminSystemSurface from "./AdminSystemSurface";
import AdminAuditSurface from "./AdminAuditSurface";
import AdminConfigurationSurface from "./AdminConfigurationSurface";
import AdminKillSurface from "./AdminKillSurface";

const THEME_CLASS: Record<Theme, string> = { dark: "theme-dark", light: "theme-light" };

// Social channels, listed by name only. WHY no href: the account handles have not been
// supplied yet, and a guessed profile URL is a wrong link shipped to users. These render
// as labelled, non-navigating chips until the real handles land. The spec fails if
// anyone invents a domain in the meantime.
const SOCIAL_CHANNELS = ["Instagram", "Facebook", "TikTok"] as const;

// First topics for the beginners path. Also name-only for now: there is no written guide
// behind any of them, so linking would be a dead end.
const BEGINNER_TOPICS = ["What is a pip", "Position sizing", "Reading a candlestick", "Risk per trade"] as const;

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
  // M10.4.12: mobile starts hidden (the desktop icon rail makes no sense below
  // 900px), desktop keeps its expanded default — the small screen follows suit.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const [tradeMode, setTradeMode] = useState<TradeMode>("manual");
  const [theme, setTheme] = useState<Theme>("dark");
  const sidebar = useRef<HTMLElement>(null);
  const location = useLocation();
  const items = NAV_BY_ROLE[role];
  // The nav config has always carried `group`; nothing rendered it, so a 9-item rail read
  // as one undifferentiated list. Grouped in first-appearance order so the config stays
  // the single source of order.
  const navGroups = useMemo<[string, NavItem[]][]>(() => {
    const grouped = new Map<string, NavItem[]>();
    for (const item of items) {
      const bucket = grouped.get(item.group);
      if (bucket) {
        bucket.push(item);
      } else {
        grouped.set(item.group, [item]);
      }
    }
    return [...grouped.entries()];
  }, [items]);

  useEffect(() => {
    const timer = window.setInterval(() => setMarket((current) => stepExperimentMarket(current)), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    // Body carries the palette so page background, scrollbars and the shell agree.
    document.body.classList.toggle("theme-light", theme === "light");
  }, [theme]);

  useEffect(() => {
    // Rotating or resizing across the breakpoint mirrors the sidebar state in both
    // directions, so the menu can never be left visible on a shrunken screen.
    const mobile = window.matchMedia("(max-width: 900px)");
    const onChange = (event: MediaQueryListEvent) => setSidebarCollapsed(event.matches);
    mobile.addEventListener("change", onChange);
    return () => mobile.removeEventListener("change", onChange);
  }, []);

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

  // A nav tap on mobile closes the menu it was made from; on desktop the rail
  // keeps its own click-to-collapse behaviour and route changes leave it alone.
  const closeOnMobile = () => {
    if (window.matchMedia("(max-width: 900px)").matches) {
      setSidebarCollapsed(true);
    }
  };

  return (
    <main className={`app-shell ${THEME_CLASS[theme]} theme-${theme}`}>
      <header className="app-header">
        <div className="header-left">
          <button
            className="icon-button"
            type="button"
            aria-expanded={!sidebarCollapsed}
            aria-label="Toggle navigation"
            title={sidebarCollapsed ? "Expand navigation" : "Collapse to icon rail"}
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
        <aside className={sidebarCollapsed ? "sidebar is-collapsed" : "sidebar"} ref={sidebar}>
          <nav className="primary-nav" aria-label="Primary navigation">
            {navGroups.map(([group, groupItems]) => (
              <div className="nav-group" key={group} role="group" aria-label={group}>
                <p className="nav-group-label">{group}</p>
                {groupItems.map((item) => {
                  const Icon = NAV_ICONS[item.href] ?? LayoutDashboard;
                  const active = location.pathname === item.href;
                  return (
                    <Link
                      key={item.href}
                      className={active ? "is-active" : undefined}
                      to={item.href}
                      title={item.label}
                      aria-current={active ? "page" : undefined}
                      onClick={closeOnMobile}
                    >
                      <Icon size={17} />
                      <span className="nav-label">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
          {/* Access sits at the END of the rail, next to the navigation it changes.
              Hidden in glyph-rail mode and revealed on hover with the labels.
              No caption and no visible label: the select's own value already states the
              role. The accessible name stays, because a bare select is unusable with a
              screen reader. */}
          <div className="sidebar-access">
            <select
              id="role"
              aria-label="Role"
              value={role}
              onChange={(event) => setRole(event.target.value as AccessRole)}
            >
              {ACCESS_ROLES.map((access) => (
                <option key={access} value={access}>
                  {access}
                </option>
              ))}
            </select>
            <button
              className="theme-toggle"
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? "Light" : "Dark"} theme
            </button>
          </div>
        </aside>
        <section className="content-area">
          <Routes>
            <Route path="/" element={role === "Viewer" ? <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} /> : <AccessPreview role={role} />} />
            <Route path="/login" element={<AccessPreview role={role} />} />
            <Route path="/markets" element={<MarketsGrid base={market} theme={theme} />} />
            <Route path="/research" element={role === "Viewer" ? <AccessPreview role={role} /> : <ResearchChat base={market} symbol={selectedSymbols[0]} />} />
            <Route path="/app/research" element={<ResearchChat withCommands={role !== "Viewer"} base={market} symbol={selectedSymbols[0]} />} />
            <Route path="/app/markets" element={<MarketsGrid base={market} theme={theme} />} />
            <Route path="/app/trading" element={role === "Viewer" ? <AccessPreview role={role} /> : <TradePane market={market} theme={theme} controls={controls} markets={markets} tradeMode={tradeMode} onTradeMode={setTradeMode} />} />
            <Route path="/app/portfolio" element={role === "Viewer" ? <AccessPreview role={role} /> : <PortfolioSurface />} />
            <Route path="/app/orders" element={role === "Viewer" ? <AccessPreview role={role} /> : <OrdersSurface />} />
            <Route path="/app/settings" element={role === "Viewer" ? <AccessPreview role={role} /> : <SettingsSurface />} />
            <Route path="/app" element={role === "Viewer" ? <AccessPreview role={role} /> : <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} />} />
            <Route path="/app/*" element={role === "Viewer" ? <AccessPreview role={role} /> : <HomeDashboard market={market} theme={theme} controls={controls} markets={markets} />} />
            {/* Admin: global control surfaces. Viewer AND Trader are both refused —
                navigation hiding is a UX measure, this is the actual gate. */}
            {/* Admin's Trading item is the GLOBAL view; a Trader keeps the private trade
                pane at /app/trading. */}
            <Route
              path="/admin/trading"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminTradingSurface role={role} />}
            />
            <Route
              path="/admin/risk"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminRiskSurface />}
            />
            <Route
              path="/admin/system"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminSystemSurface />}
            />
            <Route
              path="/admin/audit"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminAuditSurface />}
            />
            <Route
              path="/admin/configuration"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminConfigurationSurface />}
            />
            <Route
              path="/admin/kill"
              element={role !== "Admin" ? <AccessPreview role={role} /> : <AdminKillSurface />}
            />
            <Route path="/admin" element={<AdminSystemSurface />} />
            <Route path="/admin/*" element={<AccessPreview role={role} />} />
            <Route path="*" element={<AccessPreview role={role} />} />
          </Routes>
        </section>
      </div>
        <SiteFooter />
    </main>
  );
}
