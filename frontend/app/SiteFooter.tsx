// Site footer — sitemap-lite, and the last thing on every page.
//
// WHY its own component: it is the one region that appears on every route and every
// role, so it carries its own mock data and its own stylesheet instead of bloating
// App.tsx. App.tsx renders <SiteFooter /> and nothing else.
//
// The role selector and theme toggle moved to the END of the sidebar (2026-09-27):
// a control belongs next to the navigation it changes, not at the far end of the page.
//
// Structure, top to bottom — four bands, in this order and no other:
//   1. back-to-top            a control, centred
//   2. footer-story           Built / Challenge / Need, three beats, one shared measure
//   3. footer-nav             three link groups on flowing columns
//   4. footer-base            social (left half) | copyright (right half), mirrored
//
// WHY no Privacy / Terms / Status links, no broker disclaimer, and no "Mock data · no
// broker connected" line (human directives 2026-09-27): all removed from this surface.
// The mock/broker claim still gets made where it actually matters — on the surfaces
// themselves ("mock analyst · no model or feed connected", the markets provenance, the
// Admin scope note). Repeating it as a site-wide footer line was noise, and a
// disclaimer repeated on every page stops being read as one.
//
// WHY social and legal are one row of two equal halves rather than two stacked bands:
// pushed to the edges with space-between they end up different widths, which reads as
// accidental. `1fr 1fr` makes them a mirrored pair that aligns in length.
//
// Read-only: this surface navigates and describes. No account switch, no environment
// change, no YAML editing, no secret value — the backend owns all of it.
import { ArrowUp } from "lucide-react";
import { Link } from "react-router-dom";
import { mockSurfaces } from "./mock_data";

// WHY name-only: the account handles have not been supplied, and a guessed profile URL
// is a wrong link shipped to users. These render as labelled, non-navigating chips until
// the real handles land. The spec fails if anyone writes a domain in here.
const SOCIAL_CHANNELS = ["Instagram", "Facebook", "TikTok"] as const;

// First topics for the beginners path. Also name-only: no written guide exists behind
// any of them yet, so linking would be a dead end.
const BEGINNER_TOPICS = ["What is a pip", "Position sizing", "Reading a candlestick", "Risk per trade"] as const;

const STORY = [
  { beat: "Built", line: "Research-grade scaffolding: data contracts, risk gates, and a workspace where a market is read once and reused everywhere." },
  { beat: "Challenge", line: "One trader, one strategy, one honest number — expectancy after fees. Until that is proven out-of-sample, nothing here is allowed to trade." },
  { beat: "Need", line: "Time and real data to validate the edge, then capital to size it." },
] as const;

export default function SiteFooter() {
  return (
    <footer className="app-footer">
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
          {STORY.map((entry) => (
            <p className="story-beat" key={entry.beat}>
              <strong>{entry.beat}</strong>
              {entry.line}
            </p>
          ))}
        </section>

        <nav className="footer-nav" aria-label="Footer navigation">
          <div className="footer-group">
            <h2 className="footer-heading">Workspace</h2>
            <Link to="/app/trading">Trade pane</Link>
            <Link to="/app/markets">Markets</Link>
            <Link to="/app/research">Research</Link>
          </div>
          <div className="footer-group">
            <h2 className="footer-heading">Build</h2>
            <p className="muted">{mockSurfaces.join(" · ")}</p>
          </div>
          <div className="footer-group">
            <h2 className="footer-heading">Trading for beginners</h2>
            <ul className="footer-beginners">
              {BEGINNER_TOPICS.map((topic) => (
                <li key={topic}>{topic}</li>
              ))}
            </ul>
            <p className="muted footer-pending">Guides in progress</p>
          </div>
        </nav>

        <div className="footer-base">
          <div className="footer-social" aria-label="Social channels">
            <span className="muted footer-social-label">Follow</span>
            {SOCIAL_CHANNELS.map((channel) => (
              <span className="footer-social-chip" key={channel} title="handle pending — link not yet published">
                {channel}
              </span>
            ))}
          </div>
          <div className="footer-copy">
            <span className="muted">
              © {new Date().getFullYear()} QUANTRADER. Research build.
            </span>
          </div>
        </div>
      </div>
    </footer>
  );}
