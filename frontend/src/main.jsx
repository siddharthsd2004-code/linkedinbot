import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Building2, CalendarDays, Download, ExternalLink, FileJson, FileText, RefreshCw, Search } from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const SAMPLE_POSTS_JSON = JSON.stringify(
  [
    {
      source_name: "CoreWeave",
      source_kind: "company",
      posted_at: todayInputValue(),
      text: "AI Factory usage-based pricing and consumption billing for inference",
      reactions: 120,
      comments: 14,
    },
  ],
  null,
  2
);

function App() {
  const [companies, setCompanies] = useState([]);
  const [reports, setReports] = useState([]);
  const [config, setConfig] = useState({ collector_configured: false, openrouter_configured: false });
  const [scanDate, setScanDate] = useState(todayInputValue());
  const [postsJson, setPostsJson] = useState(SAMPLE_POSTS_JSON);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadDashboard();
  }, []);

  const filteredCompanies = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return companies;
    return companies.filter((company) => {
      return `${company.name} ${company.reason} ${company.url}`.toLowerCase().includes(needle);
    });
  }, [companies, query]);

  async function loadDashboard() {
    setLoading(true);
    setError("");
    try {
      const [companyResponse, reportResponse] = await Promise.all([
        fetch(`${API_BASE}/api/companies`),
        fetch(`${API_BASE}/api/reports`),
      ]);
      if (!companyResponse.ok || !reportResponse.ok) {
        throw new Error("Unable to load dashboard data.");
      }
      const companyData = await companyResponse.json();
      const reportData = await reportResponse.json();
      const configResponse = await fetch(`${API_BASE}/api/config`);
      const configData = configResponse.ok ? await configResponse.json() : {};
      setCompanies(companyData.companies || []);
      setReports(reportData.reports || []);
      setConfig(configData);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  async function runEmptyScan() {
    setRunning(true);
    setError("");
    try {
      const trimmedPosts = postsJson.trim();
      let posts;
      if (trimmedPosts) {
        const parsed = JSON.parse(trimmedPosts);
        posts = Array.isArray(parsed) ? parsed : parsed.posts;
        if (!Array.isArray(posts)) {
          throw new Error("Posts JSON must be a list, or an object with a posts list.");
        }
      }
      if (!config.collector_configured && !posts) {
        throw new Error("Paste posts JSON first, or add collector credentials in backend/.env.");
      }
      const response = await fetch(`${API_BASE}/api/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date: scanDate,
          collector: config.collector_configured && !posts,
          posts,
          no_ai: true,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Scan failed.");
      }
      await loadDashboard();
    } catch (err) {
      setError(err.message || "Scan failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Monetize360</p>
          <h1>LinkedIn Scan Reports</h1>
          <p className="subtext">
            Collector {config.collector_configured ? "connected" : "not configured"}
          </p>
        </div>
        <div className="actions">
          <label className="date-control">
            <CalendarDays size={17} />
            <input
              type="date"
              value={scanDate}
              onChange={(event) => setScanDate(event.target.value)}
              aria-label="Scan date"
            />
          </label>
          <button className="icon-button" onClick={loadDashboard} title="Refresh" aria-label="Refresh">
            <RefreshCw size={18} />
          </button>
          <button className="primary-button" onClick={runEmptyScan} disabled={running}>
            <FileText size={18} />
            {running ? "Running" : "Run Scan"}
          </button>
        </div>
      </header>

      {error && <div className="alert">{error}</div>}

      {!config.collector_configured && (
        <section className="scan-panel">
          <div className="scan-panel-header">
            <div>
              <p className="eyebrow">Manual Source</p>
              <h2>Posts JSON</h2>
            </div>
            <FileJson size={20} />
          </div>
          <textarea
            value={postsJson}
            onChange={(event) => setPostsJson(event.target.value)}
            placeholder="Paste a JSON list of LinkedIn posts"
            aria-label="Posts JSON"
          />
        </section>
      )}

      <section className="dashboard-grid">
        <div className="panel companies-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Watchlist</p>
              <h2>Companies</h2>
            </div>
            <div className="search-box">
              <Search size={16} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search"
                aria-label="Search companies"
              />
            </div>
          </div>

          <div className="company-list">
            {loading ? (
              <LoadingRows count={6} />
            ) : (
              filteredCompanies.map((company) => (
                <article className="company-row" key={`${company.tier}-${company.name}`}>
                  <div className="company-icon">
                    <Building2 size={18} />
                  </div>
                  <div className="company-main">
                    <div className="company-title">
                      <h3>{company.name}</h3>
                      <span>Tier {company.tier}</span>
                    </div>
                    <p>{company.reason}</p>
                  </div>
                  <a className="icon-link" href={company.url} target="_blank" rel="noreferrer" title="Open LinkedIn">
                    <ExternalLink size={17} />
                  </a>
                </article>
              ))
            )}
          </div>
        </div>

        <div className="panel reports-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">Archive</p>
              <h2>Reports</h2>
            </div>
          </div>

          <div className="report-list">
            {loading ? (
              <LoadingRows count={4} />
            ) : reports.length ? (
              reports.map((report) => (
                <article className="report-row" key={report.date}>
                  <div className="report-date">
                    <CalendarDays size={18} />
                    <strong>{report.date}</strong>
                  </div>
                  <div className="report-stats">
                    <span>{report.posts_reviewed} posts</span>
                    <span>{report.high_relevance_posts_found} high</span>
                    <span>Tier {(report.active_tiers || []).join("+") || "-"}</span>
                  </div>
                  <a className="download-button" href={`${API_BASE}${report.download_url}`}>
                    <Download size={17} />
                    Word
                  </a>
                </article>
              ))
            ) : (
              <div className="empty-state">
                <FileText size={26} />
                <span>No reports yet</span>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

function LoadingRows({ count }) {
  return Array.from({ length: count }).map((_, index) => <div className="loading-row" key={index} />);
}

function todayInputValue() {
  const date = new Date();
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

createRoot(document.getElementById("root")).render(<App />);
