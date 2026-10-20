/* Wireframe shell: injects topbar + sidebar, active state, and tiny sparklines.
   Structural only. No external requests. */
(() => {
  const page = document.body.dataset.page || "";
  const nav = [
    ["Overview", [
      ["dashboard", "03-dashboard.html", "Dashboard", "▤"],
      ["positions", "04-positions.html", "Positions", "▥"],
      ["markets", "06-markets.html", "Markets", "◫"],
    ]],
    ["Trade", [
      ["wallets", "08-wallets.html", "Wallets", "▣"],
      ["ledger", "18-ledger.html", "Activity", "≡"],
      ["reports", "19-reports.html", "Reports", "▦"],
    ]],
    ["Automate", [
      ["automations", "10-automations.html", "Automations", "⟳"],
      ["strategies", "12-strategies.html", "Strategies", "◇"],
      ["signals", "14-signals.html", "Signals", "⌁"],
      ["backtests", "16-backtests.html", "Backtests", "∿"],
    ]],
    ["Protect", [
      ["risk", "17-risk.html", "Risk", "◈"],
      ["alerts", "15-alerts.html", "Alerts", "◉"],
    ]],
    ["System", [
      ["chains", "20-chains.html", "Chains & RPC", "⬡"],
      ["integrations", "21-integrations.html", "Integrations", "⇄"],
      ["settings", "22-settings.html", "Settings", "⚙"],
      ["security", "23-security.html", "Security", "⌂"],
      ["developer", "24-developer.html", "Developer", "⌘"],
      ["operations", "25-operations.html", "Operations", "◍"],
      ["workspace", "26-workspace.html", "Workspace", "◎"],
    ]],
  ];

  const badges = {
    positions: "4",
    automations: "3",
    alerts: "2",
  };

  const topbar = document.createElement("header");
  topbar.className = "topbar";
  topbar.innerHTML = `
    <div class="brand">
      <a class="brand-name" href="index.html">ezCHAIN</a>
      <span class="brand-tag">Wireframe</span>
    </div>
    <button class="ws-chip" title="Switch workspace">
      <span class="avatar" style="width:18px;height:18px;font-size:8px">JW</span>
      Personal workspace <span class="muted">▾</span>
    </button>
    <form class="search" role="search" action="06-markets.html">
      <input type="search" placeholder="Search token, wallet, strategy, position…" aria-label="Search">
    </form>
    <span class="chain chip" title="Active chain filter">⬡ All chains <span class="muted">▾</span></span>
    <span class="mode paper" title="Execution mode"><span class="dot"></span>PAPER</span>
    <span class="chip" title="Last successful sync">⟳ synced 4s</span>
    <span class="spacer"></span>
    <a class="icon-btn" href="15-alerts.html" title="Alerts">◉<span class="count">2</span></a>
    <a class="kill" href="17-risk.html" title="Kill switch: halt new risk (k)">KILL SWITCH</a>
    <span class="avatar" title="Account menu">JW</span>
  `;

  const sidebar = document.createElement("aside");
  sidebar.className = "sidebar";
  const groups = nav.map(([label, items]) => {
    const links = items.map(([id, file, title, glyph]) => {
      const active = id === page ? " active" : "";
      const b = badges[id] ? ` <span class="badge">${badges[id]}</span>` : "";
      return `<a class="nav-item${active}" href="${file}"${active ? ' aria-current="page"' : ""}><span class="g">${glyph}</span><span class="t">${title}</span>${b}</a>`;
    }).join("");
    return `<div class="nav-group"><div class="nav-label">${label}</div>${links}</div>`;
  }).join("");

  sidebar.innerHTML = `${groups}
    <div class="sidebar-foot">
      <a href="index.html">All screens</a>
      <a href="27-states.html">State gallery</a>
      <a href="29-components.html">Components</a>
      <a href="28-mobile.html">Mobile</a>
      <div class="micro" style="margin-top:8px">v0.0.0 · wireframe</div>
    </div>`;

  const banner = document.createElement("div");
  banner.className = "wire-banner";
  banner.innerHTML = `<strong>WIREFRAME</strong><span>structure · content · states only</span><span>visual design follows design/BRIEF.md</span>`;

  document.body.prepend(banner);
  document.body.prepend(sidebar);
  document.body.prepend(topbar);

  // Tiny sparklines: <span class="spark" data-spark="1,2,1.5,3"></span>
  document.querySelectorAll("[data-spark]").forEach((el) => {
    const vals = el.dataset.spark.split(",").map(Number).filter((n) => Number.isFinite(n));
    if (vals.length < 2) return;
    const w = 72, h = 18, min = Math.min(...vals), max = Math.max(...vals);
    const span = max - min || 1;
    const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - min) / span) * (h - 2) - 1}`).join(" ");
    el.innerHTML = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>`;
  });
})();
