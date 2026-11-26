(() => {
  let state = null;
  let schemePaused = false;
  let activeTab = "alerts";

  const $ = (id) => document.getElementById(id);

  function projectLonLat(lon, lat) {
    const x = ((lon + 180) / 360) * 1000;
    const y = ((90 - lat) / 180) * 480;
    return [x, y];
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  function renderKPIs() {
    const facs = state.facilities || [];
    const alerts = state.alerts || [];
    const opt = state.optimization_summary || {};
    const bt = state.backtest_metrics || {};
    const donor = state.donor_feed || {};

    $("kpi-facilities").textContent = facs.length;
    const regions = new Set(facs.map((f) => f.region));
    $("kpi-regions").textContent = `Across ${regions.size} global regions`;

    const crit = alerts.filter((a) => a.severity === "CRITICAL").length;
    const warn = alerts.filter((a) => a.severity === "WARNING").length;
    $("kpi-alerts").textContent = `${crit} Critical / ${warn} Warn`;
    $("kpi-lead-time").textContent = `Avg lead time gained: ${bt.avg_lead_time_gained_hours || 54}h`;

    $("kpi-matched").textContent = `${opt.total_units_matched || 0} units`;
    $("kpi-transfers").textContent = `${(state.transfers || []).length} active/proposed routes`;

    $("kpi-recall").textContent = `${Math.round((bt.critical_shortage_recall || 0.94) * 100)}% Recall`;
    $("kpi-mae").textContent = `MAE: ${bt.mae_units_per_day || 0} u/day`;

    $("kpi-pledges").textContent = donor.community_pledge_count || 0;
    $("kpi-lives").textContent = `Up to ${donor.estimated_lives_impacted || 0} lives protected`;

    const topAlert = alerts[0];
    if (topAlert) {
      $("urgent-banner-text").textContent =
        `Early Warning (${topAlert.severity}): ${topAlert.facility_name} (${topAlert.city}) projected ${topAlert.blood_type} ${topAlert.component} shortfall in ~${topAlert.lead_time_hours}h — ${topAlert.matched_transfer_units}u pre-matched.`;
    } else {
      $("urgent-banner-text").textContent = "All connected hospitals are operating safely within reserve thresholds.";
    }
  }

  function renderWorldMap() {
    const svg = $("world-map");
    const tooltip = $("map-tooltip");
    const facs = state.facilities || [];
    const transfers = state.transfers || [];
    const eco = state.ecosystem || {};
    const btFilter = $("map-blood-filter") ? $("map-blood-filter").value : "ALL";
    const showDocs = $("layer-doctors") ? $("layer-doctors").checked : true;
    const showCols = $("layer-colleges") ? $("layer-colleges").checked : true;
    const showMfgs = $("layer-mfgs") ? $("layer-mfgs").checked : true;
    const facById = Object.fromEntries(facs.map((f) => [f.id, f]));

    // Build blood-group specific status per facility if filtered
    const btStatusByFac = {};
    if (btFilter !== "ALL") {
      (state.forecasts || []).forEach((fc) => {
        if (fc.blood_type === btFilter && fc.component === "RBC") {
          btStatusByFac[fc.facility_id] = fc;
        }
      });
    }

    // Subtle world grid + simplified continent silhouettes
    let html = `
      <defs>
        <radialGradient id="bgGrad" cx="50%" cy="45%" r="65%">
          <stop offset="0%" stop-color="#11223d"/>
          <stop offset="100%" stop-color="#070d18"/>
        </radialGradient>
      </defs>
      <rect width="1000" height="480" fill="url(#bgGrad)" />
    `;
    for (let gx = 100; gx < 1000; gx += 100) {
      html += `<line x1="${gx}" y1="0" x2="${gx}" y2="480" stroke="#182742" stroke-width="1"/>`;
    }
    for (let gy = 80; gy < 480; gy += 80) {
      html += `<line x1="0" y1="${gy}" x2="1000" y2="${gy}" stroke="#182742" stroke-width="1"/>`;
    }
    const continents = [
      "M80,95 L250,70 L300,150 L235,215 L170,220 L110,165 Z",
      "M235,235 L330,245 L355,335 L295,425 L245,390 L225,295 Z",
      "M455,85 L595,75 L610,145 L515,165 L455,140 Z",
      "M460,175 L590,175 L615,280 L550,385 L485,335 L445,230 Z",
      "M605,80 L875,90 L905,205 L780,255 L650,235 L605,165 Z",
      "M790,305 L900,310 L915,390 L810,395 Z",
    ];
    continents.forEach((d) => {
      html += `<path d="${d}" fill="#122038" stroke="#223659" stroke-width="1.2"/>`;
    });

    transfers
      .filter((t) => btFilter === "ALL" || t.target_blood_type === btFilter || t.source_blood_type === btFilter)
      .slice(0, 35)
      .forEach((t) => {
        const s = facById[t.source_facility_id];
        const d = facById[t.target_facility_id];
        if (!s || !d) return;
        const [x1, y1] = projectLonLat(s.lon, s.lat);
        const [x2, y2] = projectLonLat(d.lon, d.lat);
        const mx = (x1 + x2) / 2;
        const my = Math.min(y1, y2) - Math.min(75, Math.hypot(x2 - x1, y2 - y1) * 0.22);
        const stroke = t.status === "IN_TRANSIT" || t.status === "RECEIVED" ? "#22c55e" : "#38bdf8";
        const dash = t.status === "PROPOSED" ? "5,5" : "none";
        html += `<path d="M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}" fill="none" stroke="${stroke}" stroke-width="1.8" stroke-dasharray="${dash}" opacity="0.82"/>`;
      });

    const hoverData = {};

    facs.forEach((f) => {
      const [x, y] = projectLonLat(f.lon, f.lat);
      const isBank = f.kind !== "hospital";
      const fcBt = btStatusByFac[f.id];
      const status = fcBt ? fcBt.risk_level : f.overall_status;
      let color = "#22c55e";
      if (isBank) color = "#38bdf8";
      else if (status === "CRITICAL") color = "#ef4444";
      else if (status === "WARNING") color = "#f59e0b";

      const r = isBank ? 7 : 6;
      if (status === "CRITICAL") {
        html += `<circle cx="${x}" cy="${y}" r="13" fill="none" stroke="#ef4444" stroke-width="1.6" opacity="0.75"/>`;
      }
      const key = `fac-${f.id}`;
      hoverData[key] = `<strong>${f.name}</strong><br/>${f.city}, ${f.country} (${f.kind.replace("_", " ")})<br/>Status: <strong>${status}</strong> | Stock: ${fcBt ? `${fcBt.current_units}u (${btFilter} RBC)` : `${f.total_units}u total`}<br/>Cover: ${fcBt ? fcBt.days_of_cover : f.min_days_of_cover} days`;
      html += `<circle class="map-node" data-key="${key}" cx="${x}" cy="${y}" r="${r}" fill="${color}" stroke="#08101e" stroke-width="1.5" style="cursor:pointer"/>`;
    });

    if (showDocs) {
      (eco.doctors || []).forEach((d) => {
        const [x, y] = projectLonLat(d.lon, d.lat);
        const key = `doc-${d.doctor_id}`;
        hoverData[key] = `<strong>${d.name}</strong> (WHO Verified)<br/>${d.specialty}<br/>Exp: ${d.experience_years}y | Success: ${d.success_rate_pct}% | Mental Strength: ${d.mental_strength_score}/100`;
        html += `<rect class="map-node" data-key="${key}" x="${x + 5}" y="${y - 9}" width="7" height="7" rx="1.5" fill="#a855f7" stroke="#ffffff" stroke-width="0.9" style="cursor:pointer"/>`;
      });
    }
    if (showCols) {
      (eco.medical_colleges || []).forEach((c) => {
        const [x, y] = projectLonLat(c.lon, c.lat);
        const key = `col-${c.college_id}`;
        hoverData[key] = `<strong>${c.name}</strong><br/>${c.city}, ${c.country}<br/>WHO ID: ${c.who_verification_id}`;
        html += `<polygon class="map-node" data-key="${key}" points="${x - 8},${y + 8} ${x - 4},${y + 1} ${x},${y + 8}" fill="#c084fc" stroke="#08101e" stroke-width="0.9" style="cursor:pointer"/>`;
      });
    }
    if (showMfgs) {
      (eco.drug_manufacturers || []).forEach((m) => {
        const [x, y] = projectLonLat(m.lon, m.lat);
        const key = `mfg-${m.manufacturer_id}`;
        hoverData[key] = `<strong>${m.name}</strong><br/>Products: ${m.products.slice(0, 2).join(", ")}<br/>WHO PQ: ${m.who_verification_id}`;
        html += `<polygon class="map-node" data-key="${key}" points="${x},${y - 10} ${x + 5},${y - 5} ${x},${y} ${x - 5},${y - 5}" fill="#f472b6" stroke="#08101e" stroke-width="0.9" style="cursor:pointer"/>`;
      });
    }

    svg.innerHTML = html;
    svg.querySelectorAll(".map-node").forEach((el) => {
      el.addEventListener("mouseenter", (ev) => {
        const tip = hoverData[el.getAttribute("data-key")];
        if (!tip) return;
        tooltip.hidden = false;
        tooltip.innerHTML = tip;
        const rect = svg.getBoundingClientRect();
        tooltip.style.left = `${Math.min(rect.width - 250, ev.offsetX + 12)}px`;
        tooltip.style.top = `${Math.max(8, ev.offsetY - 12)}px`;
      });
      el.addEventListener("mouseleave", () => {
        tooltip.hidden = true;
      });
    });
  }

  function renderDonorFeed(feed) {
    if (!feed) return;
    const scheme = feed.active_scheme || {};
    $("scheme-badge").textContent = scheme.badge || "LIVE SCHEME";
    $("scheme-title").textContent = scheme.title || "Continuous Donor Motivation";
    $("scheme-tagline").textContent = scheme.tagline || "";
    $("scheme-fact").textContent = `${scheme.impact_fact || ""} • Reward: ${scheme.incentive || ""}`;

    const chips = (feed.urgent_blood_types || [])
      .map((u) => `<span class="chip">Urgent: ${u.blood_type} (${u.units_needed}u needed)</span>`)
      .join("");
    $("donor-urgent-chips").innerHTML = chips || `<span class="chip">All types welcomed today</span>`;

    $("donor-dialog-stream").innerHTML = (feed.live_dialog_stream || [])
      .map((line) => `<div class="dialog-line"><strong>${line.speaker}:</strong> ${line.message}</div>`)
      .join("");
  }

  function appendChat(role, text, quickReplies = []) {
    const log = $("donor-chat-log");
    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;

    if (role === "bot") {
      $("donor-quick-replies").innerHTML = quickReplies
        .map((q) => `<button type="button" class="quick-btn">${q}</button>`)
        .join("");
      $("donor-quick-replies").querySelectorAll(".quick-btn").forEach((btn) => {
        btn.addEventListener("click", () => sendDonorQuestion(btn.textContent));
      });
    }
  }

  async function sendDonorQuestion(msg) {
    if (!msg.trim()) return;
    appendChat("user", msg);
    if (msg.toLowerCase().includes("pledge")) {
      $("pledge-name").focus();
    }
    try {
      const res = await api("/api/donor/dialog", {
        method: "POST",
        body: JSON.stringify({ message: msg, context: { city: $("pledge-city").value || "your area" } }),
      });
      appendChat("bot", `${res.reply} 💡 ${res.motivation_nudge}`, res.quick_replies || []);
    } catch (err) {
      appendChat("bot", `Companion offline: ${err.message}`);
    }
  }

  function renderAlerts() {
    const query = ($("alert-search").value || "").toLowerCase();
    const sev = $("alert-severity").value;
    const alerts = (state.alerts || []).filter((a) => {
      if (sev !== "ALL" && a.severity !== sev) return false;
      if (query) {
        const hay = `${a.facility_name} ${a.city} ${a.country} ${a.blood_type} ${a.component}`.toLowerCase();
        if (!hay.includes(query)) return false;
      }
      return true;
    });

    if (!alerts.length) {
      $("alerts-list").innerHTML = `<div class="panel">No shortage alerts match your current filter.</div>`;
      return;
    }

    $("alerts-list").innerHTML = alerts
      .map((a) => {
        const linked = (state.transfers || []).find((t) => a.linked_transfers.includes(t.transfer_id) && t.status === "PROPOSED");
        return `
          <article class="card sev-${a.severity}">
            <div class="panel-header" style="margin-bottom:4px">
              <strong>${a.facility_name}</strong>
              <span class="badge badge-${a.severity}">${a.severity} • ${a.days_of_cover}d cover</span>
            </div>
            <div style="font-size:0.86rem;color:var(--muted)">
              ${a.city}, ${a.country} • <strong>${a.blood_type} ${a.component}</strong> (${a.current_units}u left) • Lead time: <strong>${a.lead_time_hours}h</strong>
            </div>
            <div style="font-size:0.85rem">
              <strong>Why:</strong> ${a.driver_note || a.primary_driver} (7d risk: ${Math.round(a.shortage_probability_7d * 100)}%)
            </div>
            <ul class="prep-list">
              ${a.preparedness_actions.map((step) => `<li>${step}</li>`).join("")}
            </ul>
            ${linked ? `<button class="btn btn-primary btn-sm approve-inline" data-tid="${linked.transfer_id}" type="button">Approve Matched Route ${linked.transfer_id} (${linked.units}u from ${linked.source_city})</button>` : ""}
          </article>
        `;
      })
      .join("");

    document.querySelectorAll(".approve-inline").forEach((btn) => {
      btn.addEventListener("click", () => transitionTransfer(btn.getAttribute("data-tid"), "APPROVED"));
    });
  }

  function renderTransfers() {
    const transfers = state.transfers || [];
    $("transfers-tbody").innerHTML = transfers
      .map((t) => {
        let actionBtn = `<span style="color:var(--muted)">Completed</span>`;
        if (t.status === "PROPOSED") {
          actionBtn = `<button class="btn btn-primary btn-sm txf-act" data-tid="${t.transfer_id}" data-next="APPROVED" type="button">Approve</button>`;
        } else if (t.status === "APPROVED") {
          actionBtn = `<button class="btn btn-accent btn-sm txf-act" data-tid="${t.transfer_id}" data-next="IN_TRANSIT" type="button">Dispatch</button>`;
        } else if (t.status === "IN_TRANSIT") {
          actionBtn = `<button class="btn btn-ghost btn-sm txf-act" data-tid="${t.transfer_id}" data-next="RECEIVED" type="button">Confirm Receipt</button>`;
        }
        return `
          <tr>
            <td><strong>${t.transfer_id}</strong></td>
            <td><span class="badge badge-${t.status === "RECEIVED" ? "SAFE" : t.status === "PROPOSED" ? "WATCH" : "WARNING"}">${t.status}</span></td>
            <td>${t.component}: <strong>${t.source_blood_type} → ${t.target_blood_type}</strong>${t.substitution_used ? " (Safe Sub)" : ""}</td>
            <td>${t.source_facility_name}<br/><small>${t.source_city}, ${t.source_country}</small></td>
            <td>${t.target_facility_name}<br/><small>${t.target_city}, ${t.target_country}</small></td>
            <td><strong>${t.units}u</strong>${t.rescues_expiring_units ? " ♻️" : ""}</td>
            <td>${t.transport_mode}<br/><small>${t.transit_hours}h (${t.distance_km} km)</small></td>
            <td>${t.requires_clinical_signoff ? `Required (${t.signoff_reason})` : "Standard"}</td>
            <td>${actionBtn}</td>
          </tr>
        `;
      })
      .join("");

    document.querySelectorAll(".txf-act").forEach((btn) => {
      btn.addEventListener("click", () =>
        transitionTransfer(btn.getAttribute("data-tid"), btn.getAttribute("data-next"))
      );
    });
  }

  function renderDirectory() {
    const facs = state.facilities || [];
    const regionSel = $("dir-region");
    if (regionSel.options.length === 1) {
      [...new Set(facs.map((f) => f.region))].sort().forEach((r) => {
        const opt = document.createElement("option");
        opt.value = r;
        opt.textContent = r;
        regionSel.appendChild(opt);
      });
    }
    const q = ($("dir-search").value || "").toLowerCase();
    const reg = regionSel.value;
    const filtered = facs.filter((f) => {
      if (reg !== "ALL" && f.region !== reg) return false;
      if (q && !`${f.name} ${f.city} ${f.country} ${f.isbt_code}`.toLowerCase().includes(q)) return false;
      return true;
    });

    $("directory-grid").innerHTML = filtered
      .map(
        (f) => `
        <article class="card sev-${f.overall_status}">
          <div class="panel-header" style="margin-bottom:2px">
            <strong>${f.name}</strong>
            <span class="badge badge-${f.overall_status}">${f.overall_status}</span>
          </div>
          <div style="font-size:0.85rem;color:var(--muted)">
            ${f.city}, ${f.country} • ${f.region} • ISBT: <code>${f.isbt_code}</code>
          </div>
          <div style="font-size:0.86rem">
            Type: <strong>${f.kind.replace("_", " ")}</strong> • Total Stock: <strong>${f.total_units}u</strong> • Min Cover: <strong>${f.min_days_of_cover}d</strong>
          </div>
          <div style="font-size:0.8rem;color:var(--muted)">
            Channel: ${f.contact_channel} • Fed Token: <code>${f.anonymized_token}</code>
          </div>
        </article>
      `
      )
      .join("");
  }

  function renderPrivacyAndAudit() {
    const fed = state.federated_privacy || {};
    $("privacy-list").innerHTML = Object.entries(fed)
      .map(
        ([reg, d]) => `
        <div class="list-row">
          <strong>${reg}</strong> — Hospitals: ${d.count} | Noisy Burn Sum: <strong>${d.noisy_sum} u/day</strong> | Mean: ${d.noisy_mean} u/day (ε=${d.epsilon})
        </div>
      `
      )
      .join("");

    const logs = state.audit_log || [];
    $("audit-list").innerHTML = logs
      .map(
        (l) => `
        <div class="list-row">
          <strong>[${l.event_type}]</strong> ${l.summary}<br/>
          <small style="color:var(--muted)">Actor: ${l.actor} • ${l.created_at}</small>
        </div>
      `
      )
      .join("");
  }

  function renderAnalytics() {
    const an = state.analytics || {};
    $("analytics-burn-summary").textContent =
      `Live global stock: ${an.total_live_units || 0} units • Avg daily global consumption: ${an.avg_global_daily_consumption || 0} u/day`;

    // 30-day SVG line chart
    const series = an.historic_consumption_series || [];
    const svg = $("consumption-chart");
    if (series.length > 1) {
      const maxVal = Math.max(...series.flatMap((p) => [p.consumption_units, p.collection_units]), 10);
      const padL = 44, padR = 16, padT = 20, padB = 28;
      const w = 640 - padL - padR;
      const h = 240 - padT - padB;
      const pt = (idx, val) => {
        const x = padL + (idx / (series.length - 1)) * w;
        const y = padT + h - (val / maxVal) * h;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      };
      const consPts = series.map((p, i) => pt(i, p.consumption_units)).join(" ");
      const colPts = series.map((p, i) => pt(i, p.collection_units)).join(" ");
      svg.innerHTML = `
        <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + h}" stroke="#27395d"/>
        <line x1="${padL}" y1="${padT + h}" x2="${padL + w}" y2="${padT + h}" stroke="#27395d"/>
        <text x="6" y="${padT + 8}" fill="#9eb0cf" font-size="10">${Math.round(maxVal)}u</text>
        <text x="12" y="${padT + h}" fill="#9eb0cf" font-size="10">0u</text>
        <text x="${padL}" y="232" fill="#9eb0cf" font-size="10">${series[0].date}</text>
        <text x="${padL + w - 70}" y="232" fill="#9eb0cf" font-size="10">${series[series.length - 1].date}</text>
        <polyline fill="none" stroke="#f43f5e" stroke-width="2.4" points="${consPts}" />
        <polyline fill="none" stroke="#22c55e" stroke-width="2.2" points="${colPts}" />
        <text x="415" y="16" fill="#f43f5e" font-size="11" font-weight="bold">● Historic Consumption</text>
        <text x="540" y="16" fill="#22c55e" font-size="11" font-weight="bold">● Collections</text>
      `;
    }

    // Populate sync facility dropdown once
    const syncSel = $("sync-facility");
    if (syncSel.options.length === 0) {
      (state.facilities || []).forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f.id;
        opt.textContent = `${f.name} (${f.city})`;
        syncSel.appendChild(opt);
      });
    }

    $("top-consumers-list").innerHTML = (an.top_consuming_hospitals || [])
      .map(
        (h) => `
        <div class="list-row">
          <strong>${h.facility_name}</strong> (${h.city}) — 30d burn: <strong>${h.consumption_30d_units}u</strong> (${h.avg_daily_burn} u/day)
        </div>`
      )
      .join("");

    // Blood group x component matrix
    const mat = an.blood_group_matrix || {};
    const comps = ["RBC", "PLT", "FFP", "CRYO"];
    $("availability-matrix-tbody").innerHTML = Object.entries(mat)
      .map(([bt, row]) => {
        const cells = comps
          .map((c) => {
            const cell = row[c] || {};
            return `<td><span class="badge badge-${cell.status || "SAFE"}">${cell.current_units || 0}u</span> <small>${cell.network_days_cover || 0}d • Burn ${cell.daily_demand || 0}/d</small></td>`;
          })
          .join("");
        return `<tr><td><strong>${bt}</strong></td>${cells}</tr>`;
      })
      .join("");

    // Real-time stream monitor
    const monBt = $("mon-bt").value;
    const monComp = $("mon-comp").value;
    const streams = (state.forecasts || [])
      .filter((f) => (monBt === "ALL" || f.blood_type === monBt) && (monComp === "ALL" || f.component === monComp))
      .slice(0, 40);
    $("monitor-tbody").innerHTML = streams
      .map(
        (s) => `
        <tr>
          <td>${s.facility_name}</td>
          <td>${s.city}</td>
          <td><strong>${s.blood_type}</strong> ${s.component}</td>
          <td>${s.current_units}u</td>
          <td>${s.predicted_daily_demand}/d</td>
          <td>${s.days_of_cover}d</td>
          <td>${Math.round(s.shortage_probability_7d * 100)}%</td>
          <td><span class="badge badge-${s.risk_level}">${s.risk_level}</span></td>
        </tr>`
      )
      .join("");
  }

  function renderCaretakerAndEcosystem() {
    const eco = state.ecosystem || {};
    const q = ($("eco-search").value || "").toLowerCase();
    const reg = $("eco-region").value;
    const keep = (item, text) => (reg === "ALL" || item.region === reg) && (!q || text.toLowerCase().includes(q));

    const docs = (eco.doctors || []).filter((d) => keep(d, `${d.name} ${d.specialty} ${d.blood_group_focus.join(" ")} ${d.city} ${d.hospital_name}`));
    $("doctors-grid").innerHTML = docs
      .map(
        (d) => `
        <article class="card sev-SAFE">
          <div class="panel-header" style="margin-bottom:2px">
            <strong>${d.name}</strong>
            <span class="who-pill">WHO Verified</span>
          </div>
          <div style="font-size:0.84rem;color:var(--muted)">${d.specialty} • ${d.hospital_name} (${d.city}, ${d.country})</div>
          <div style="font-size:0.84rem">Blood Focus: <strong>${d.blood_group_focus.join(", ")}</strong></div>
          <div style="font-size:0.84rem">Experience: <strong>${d.experience_years} yrs</strong> • Success Rate: <strong>${d.success_rate_pct}%</strong></div>
          <div style="font-size:0.82rem">Mental Strength: <strong>${d.mental_strength_score}/100</strong> (${d.mental_strength_tier})</div>
          <div class="metric-bar-bg"><div class="metric-bar-fill" style="width:${d.mental_strength_score}%"></div></div>
          <div style="font-size:0.78rem;color:var(--muted)">WHO ID: <code>${d.who_verification_id}</code></div>
          <button class="btn btn-primary btn-sm connect-doc-btn" data-doc-id="${d.doctor_id}" data-city="${d.city}" type="button">Connect Caretaker to ${d.name.split(",")[0]}</button>
        </article>`
      )
      .join("");

    document.querySelectorAll(".connect-doc-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const res = await api("/api/caretaker/connect", {
          method: "POST",
          body: JSON.stringify({
            caretaker_name: $("care-name").value || "Guardian Quick-Connect",
            patient_relation: $("care-relation").value || "Family Guardian",
            blood_type: $("care-bt").value || "O-",
            component: $("care-comp").value || "RBC",
            city: $("care-city").value || btn.getAttribute("data-city"),
            urgency: $("care-urgency").value || "URGENT",
            clinical_notes: $("care-notes").value || "Direct specialist doctor consultation",
            doctor_id: btn.getAttribute("data-doc-id"),
          }),
        });
        $("caretaker-status").textContent = `Connected! Ticket ${res.connection.request_id} -> ${res.assigned_doctor.name} (${res.assigned_doctor.who_verification_id})`;
        state = res.overview;
        renderAll();
      });
    });

    const cols = (eco.medical_colleges || []).filter((c) => keep(c, `${c.name} ${c.city} ${c.country} ${c.research_focus}`));
    $("colleges-grid").innerHTML = cols
      .map(
        (c) => `
        <article class="card">
          <div class="panel-header" style="margin-bottom:2px"><strong>${c.name}</strong><span class="who-pill">WHO Verified</span></div>
          <div style="font-size:0.84rem;color:var(--muted)">${c.city}, ${c.country} • ${c.region}</div>
          <div style="font-size:0.84rem">Annual Donor Collection: <strong>${c.annual_donor_units_collected.toLocaleString()} units/yr</strong></div>
          <div style="font-size:0.83rem">${c.research_focus}</div>
          <div style="font-size:0.78rem;color:var(--muted)">WHO ID: <code>${c.who_verification_id}</code></div>
        </article>`
      )
      .join("");

    const mfgs = (eco.drug_manufacturers || []).filter((m) => keep(m, `${m.name} ${m.city} ${m.country} ${m.products.join(" ")}`));
    $("mfgs-grid").innerHTML = mfgs
      .map(
        (m) => `
        <article class="card">
          <div class="panel-header" style="margin-bottom:2px"><strong>${m.name}</strong><span class="who-pill">WHO PQ Verified</span></div>
          <div style="font-size:0.84rem;color:var(--muted)">${m.city}, ${m.country} • Cold-chain SLA: ${m.cold_chain_sla_hours}h</div>
          <div style="font-size:0.83rem">Products: <strong>${m.products.join(", ")}</strong></div>
          <div style="font-size:0.78rem;color:var(--muted)">${m.gmp_status} • <code>${m.who_verification_id}</code></div>
        </article>`
      )
      .join("");

    const exps = (eco.who_experts || []).filter((e) => keep(e, `${e.name} ${e.role} ${e.city} ${e.focus_area}`));
    $("experts-grid").innerHTML = exps
      .map(
        (e) => `
        <article class="card">
          <div class="panel-header" style="margin-bottom:2px"><strong>${e.name}</strong><span class="who-pill">WHO Expert</span></div>
          <div style="font-size:0.84rem;color:var(--muted)">${e.role} • ${e.city}, ${e.country}</div>
          <div style="font-size:0.84rem">Exp: <strong>${e.experience_years} yrs</strong> • Success: <strong>${e.success_rate_pct}%</strong> • Mental Strength: <strong>${e.mental_strength_score}/100</strong></div>
          <div style="font-size:0.83rem">${e.focus_area}</div>
          <div style="font-size:0.78rem;color:var(--muted)">WHO ID: <code>${e.who_verification_id}</code></div>
        </article>`
      )
      .join("");

    const reqs = state.caretaker_requests || [];
    $("caretaker-requests-list").innerHTML = reqs.length
      ? reqs
          .map(
            (r) => `
            <div class="list-row">
              <strong>${r.request_id}</strong> (${r.urgency}) — <strong>${r.caretaker_name}</strong> (${r.patient_relation}) connected to <strong>${r.assigned_doctor_name}</strong><br/>
              <small style="color:var(--muted)">Need: ${r.blood_type} ${r.component} in ${r.city} • Hospital: ${r.hospital_name} • WHO: ${r.who_verification_id}</small>
            </div>`
          )
          .join("")
      : `<div class="list-row">No caretaker consultation tickets yet. Submit the form to connect instantly with a WHO-verified specialist.</div>`;
  }

  function renderAll() {
    if (!state) return;
    renderKPIs();
    renderWorldMap();
    renderDonorFeed(state.donor_feed);
    renderAlerts();
    renderAnalytics();
    renderCaretakerAndEcosystem();
    renderTransfers();
    renderDirectory();
    renderPrivacyAndAudit();
  }

  async function refreshOverview() {
    state = await api("/api/overview");
    renderAll();
  }

  async function transitionTransfer(tid, nextStatus) {
    try {
      const res = await api(`/api/transfers/${tid}/transition`, {
        method: "POST",
        body: JSON.stringify({ status: nextStatus, actor: $("role-select").value }),
      });
      state = res.overview;
      renderAll();
    } catch (err) {
      alert(err.message);
    }
  }

  // Tabs
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeTab = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab").forEach((t) => {
        const active = t === btn;
        t.classList.toggle("active", active);
        t.setAttribute("aria-selected", String(active));
      });
      document.querySelectorAll(".tab-panel").forEach((p) => {
        const show = p.id === `tab-${activeTab}`;
        p.hidden = !show;
        p.classList.toggle("active", show);
      });
    });
  });

  // Accessibility & UI toggles
  $("btn-toggle-text").addEventListener("click", (e) => {
    const on = document.body.classList.toggle("large-text");
    e.currentTarget.setAttribute("aria-pressed", String(on));
  });
  $("btn-toggle-contrast").addEventListener("click", (e) => {
    const on = document.body.classList.toggle("high-contrast");
    e.currentTarget.setAttribute("aria-pressed", String(on));
  });
  $("btn-pause-scheme").addEventListener("click", (e) => {
    schemePaused = !schemePaused;
    e.currentTarget.setAttribute("aria-pressed", String(schemePaused));
    e.currentTarget.textContent = schemePaused ? "Resume Cycle" : "Pause Cycle";
  });
  $("btn-open-donor-dialog").addEventListener("click", () => {
    const panel = $("donor-dialog-panel");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    panel.classList.add("highlight-pulse");
    setTimeout(() => panel.classList.remove("highlight-pulse"), 1800);
    $("donor-chat-input").focus();
  });

  // Role perspective helper
  $("role-select").addEventListener("change", (e) => {
    const role = e.target.value;
    if (role === "public") {
      $("btn-open-donor-dialog").click();
    } else if (role === "logistics") {
      document.querySelector('[data-tab="transfers"]').click();
    } else {
      document.querySelector('[data-tab="alerts"]').click();
    }
  });

  // Connect Hospital Modal
  const modal = $("connect-modal");
  $("btn-connect-hospital").addEventListener("click", () => modal.showModal());
  $("btn-close-connect").addEventListener("click", () => modal.close());
  $("connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const omin = parseInt($("conn-omin").value || "3", 10);
      const res = await api("/api/facilities", {
        method: "POST",
        body: JSON.stringify({
          name: $("conn-name").value,
          kind: $("conn-kind").value,
          city: $("conn-city").value,
          country: $("conn-country").value,
          region: $("conn-region").value,
          capacity_units: parseInt($("conn-cap").value || "600", 10),
          lat: parseFloat($("conn-lat").value || "14.59"),
          lon: parseFloat($("conn-lon").value || "120.98"),
          isbt_code: $("conn-isbt").value || "NEW01",
          initial_inventory: { "RBC:O-": omin },
        }),
      });
      state = res.overview;
      renderAll();
      modal.close();
      e.target.reset();
    } catch (err) {
      alert(err.message);
    }
  });

  // Donor Chat & Pledge
  $("donor-chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const inp = $("donor-chat-input");
    const msg = inp.value;
    inp.value = "";
    sendDonorQuestion(msg);
  });

  $("pledge-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/donor/pledges", {
        method: "POST",
        body: JSON.stringify({
          donor_name: $("pledge-name").value,
          blood_type: $("pledge-blood").value,
          component: $("pledge-comp").value,
          city: $("pledge-city").value,
          team_name: $("pledge-team").value,
          preferred_window: $("pledge-window").value,
          scheme_id: state?.donor_feed?.active_scheme?.scheme_id || "scheme-rapid-match",
        }),
      });
      $("pledge-status").textContent = `Thank you ${res.pledge.donor_name}! Pledge ${res.pledge.pledge_id} logged — potentially protecting 3 lives.`;
      state.donor_feed = res.donor_feed;
      renderKPIs();
      renderDonorFeed(res.donor_feed);
      e.target.reset();
    } catch (err) {
      $("pledge-status").textContent = `Error: ${err.message}`;
    }
  });

  // Filters
  $("alert-search").addEventListener("input", renderAlerts);
  $("alert-severity").addEventListener("change", renderAlerts);
  $("dir-search").addEventListener("input", renderDirectory);
  $("dir-region").addEventListener("change", renderDirectory);
  $("map-blood-filter").addEventListener("change", renderWorldMap);
  $("layer-doctors").addEventListener("change", renderWorldMap);
  $("layer-colleges").addEventListener("change", renderWorldMap);
  $("layer-mfgs").addEventListener("change", renderWorldMap);
  $("mon-bt").addEventListener("change", renderAnalytics);
  $("mon-comp").addEventListener("change", renderAnalytics);
  $("eco-search").addEventListener("input", renderCaretakerAndEcosystem);
  $("eco-region").addEventListener("change", renderCaretakerAndEcosystem);

  // Live Inventory Sync
  $("inv-sync-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    state = await api("/api/inventory/sync", {
      method: "POST",
      body: JSON.stringify({
        facility_id: $("sync-facility").value,
        component: $("sync-comp").value,
        blood_type: $("sync-bt").value,
        units: parseInt($("sync-units").value || "0", 10),
      }),
    });
    renderAll();
  });

  // Caretaker -> Specialist Doctor Connect
  $("caretaker-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/caretaker/connect", {
        method: "POST",
        body: JSON.stringify({
          caretaker_name: $("care-name").value,
          patient_relation: $("care-relation").value,
          blood_type: $("care-bt").value,
          component: $("care-comp").value,
          city: $("care-city").value,
          urgency: $("care-urgency").value,
          clinical_notes: $("care-notes").value,
        }),
      });
      $("caretaker-status").textContent =
        `Connected! Ticket ${res.connection.request_id} assigned to ${res.assigned_doctor.name} (${res.assigned_doctor.hospital_name}, WHO: ${res.assigned_doctor.who_verification_id}).`;
      state = res.overview;
      renderAll();
    } catch (err) {
      $("caretaker-status").textContent = `Error: ${err.message}`;
    }
  });

  // What-If Simulator
  ["surg", "trauma", "outbreak", "donor"].forEach((k) => {
    $(`sim-${k}`).addEventListener("input", (e) => {
      const v = parseFloat(e.target.value).toFixed(1);
      $(`val-${k}`).textContent = k === "outbreak" ? `+${v}` : `${v}x`;
    });
  });

  $("sim-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    state = await api("/api/simulate", {
      method: "POST",
      body: JSON.stringify({
        region: $("sim-region").value,
        surgery_multiplier: parseFloat($("sim-surg").value),
        trauma_multiplier: parseFloat($("sim-trauma").value),
        outbreak_boost: parseFloat($("sim-outbreak").value),
        donor_turnout_multiplier: parseFloat($("sim-donor").value),
      }),
    });
    renderAll();
    document.querySelector('[data-tab="alerts"]').click();
  });

  $("btn-sim-reset").addEventListener("click", async () => {
    $("sim-region").value = "";
    $("sim-surg").value = "1.0"; $("val-surg").textContent = "1.0x";
    $("sim-trauma").value = "1.0"; $("val-trauma").textContent = "1.0x";
    $("sim-outbreak").value = "0.0"; $("val-outbreak").textContent = "+0.0";
    $("sim-donor").value = "1.0"; $("val-donor").textContent = "1.0x";
    state = await api("/api/simulate", { method: "POST", body: JSON.stringify({}) });
    renderAll();
  });

  // Interop Lab
  $("isbt-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await api("/api/isbt128/parse", {
      method: "POST",
      body: JSON.stringify({ barcode: $("isbt-input").value }),
    });
    $("isbt-output").textContent = JSON.stringify(res, null, 2);
  });

  $("btn-test-fhir").addEventListener("click", async () => {
    const sampleBundle = {
      resourceType: "Bundle",
      type: "collection",
      entry: [
        { resource: { resourceType: "Organization", id: "hosp-del-aiims", name: "AIIMS New Delhi" } },
        {
          resource: {
            resourceType: "BiologicallyDerivedProduct",
            productCode: { text: "RBC" },
            quantity: 2,
            property: [{ valueCodeableConcept: { text: "O-" } }],
          },
        },
        { resource: { resourceType: "Appointment", status: "booked", patient: { reference: "Patient/MASKED" } } },
      ],
    };
    const res = await api("/api/fhir/ingest", { method: "POST", body: JSON.stringify(sampleBundle) });
    $("fhir-output").textContent = JSON.stringify(res.signals, null, 2);
    state = res.overview;
    renderAll();
  });

  // Real-time SSE for continuous Donor Motivation Scheme & live network updates
  function connectSSE() {
    const es = new EventSource("/api/events");
    es.addEventListener("donor_tick", (ev) => {
      if (schemePaused || !state) return;
      const feed = JSON.parse(ev.data);
      state.donor_feed = feed;
      renderDonorFeed(feed);
      $("kpi-pledges").textContent = feed.community_pledge_count || 0;
      $("kpi-lives").textContent = `Up to ${feed.estimated_lives_impacted || 0} lives protected`;
    });
    es.addEventListener("network_update", () => {
      refreshOverview().catch(() => {});
    });
  }

  refreshOverview().then(() => {
    appendChat(
      "bot",
      "Hi! I'm the HemoNet Donor Companion running continuously to help communities prevent blood emergencies. Ask me anything or pledge a slot below!",
      ["Which hospitals need help?", "Am I eligible today?", "First-timer comfort tips"]
    );
    connectSSE();
  });
})();
