import {
  Activity,
  BookOpen,
  CircleUser,
  FileText,
  LayoutDashboard,
  LineChart,
  Scale,
  Settings,
  ShieldCheck,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

// Icons live with the nav config so route labels stay in one place.
// Decorative only: aria-hidden by default, accessible name stays on the link text.
export const NAV_ICONS: Record<string, LucideIcon> = {
  "/": LayoutDashboard,
  "/markets": LineChart,
  "/research": BookOpen,
  "/health": Activity,
  "/login": CircleUser,
  "/app": LayoutDashboard,
  "/app/markets": LineChart,
  "/app/research": BookOpen,
  "/app/trading": TrendingUp,
  "/app/portfolio": Scale,
  "/app/orders": FileText,
  "/app/risk": ShieldCheck,
  "/app/settings": Settings,
  "/admin": ShieldCheck,
  "/admin/system": Activity,
  "/admin/audit": FileText,
  "/admin/configuration": Settings,
  "/admin/risk": ShieldCheck,
};

export const NAV_BY_ROLE = {
  Admin: [
    { label: "Overview", href: "/app", group: "Overview" },
    { label: "Markets", href: "/app/markets", group: "Markets" },
    { label: "Research", href: "/app/research", group: "Research" },
    { label: "Trading", href: "/app/trading", group: "Trading" },
    { label: "Risk", href: "/app/risk", group: "Risk" },
    { label: "System", href: "/admin/system", group: "System" },
    { label: "Audit", href: "/admin/audit", group: "System" },
    { label: "Configuration", href: "/admin/configuration", group: "Configuration" },
    { label: "Kill / Resume", href: "/admin/risk", group: "Configuration" },
  ],
  Trader: [
    { label: "Overview", href: "/app", group: "Overview" },
    { label: "Markets", href: "/app/markets", group: "Markets" },
    { label: "Research", href: "/app/research", group: "Research" },
    { label: "Trading", href: "/app/trading", group: "Trading" },
    { label: "Portfolio", href: "/app/portfolio", group: "Trading" },
    { label: "Orders", href: "/app/orders", group: "Trading" },
    { label: "Settings", href: "/app/settings", group: "Configuration" },
  ],
  Viewer: [
    { label: "Sign in", href: "/login", group: "Account", read_only: true },
    { label: "Overview", href: "/", group: "Overview", read_only: true },
    { label: "Markets", href: "/markets", group: "Markets", read_only: true },
    { label: "Research", href: "/research", group: "Research", read_only: true },
    { label: "Health", href: "/health", group: "System", read_only: true },
  ],
} as const;

export type AccessRole = keyof typeof NAV_BY_ROLE;
export type NavItem = (typeof NAV_BY_ROLE)[AccessRole][number];
