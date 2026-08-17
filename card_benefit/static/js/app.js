(() => {
  const cardList = document.getElementById("card-list");
  const emptyHint = document.getElementById("empty-hint");
  const inboxSection = document.getElementById("inbox-section");
  const inboxList = document.getElementById("inbox-list");
  const inboxCount = document.getElementById("inbox-count");
  const noBenefitSection = document.getElementById("no-benefit-section");
  const noBenefitList = document.getElementById("no-benefit-list");
  const noBenefitCount = document.getElementById("no-benefit-count");

  const CARD_COLORS = ["#3987e5", "#ec4899", "#fab219", "#22c55e", "#a78bfa", "#f97316"];

  let state = { cards: [], inbox: [], no_benefit: [] };
  let noBenefitOpen = false;
  const openLogPanels = new Set(); // benefit id 목록 - 펼쳐진 사용내역 기억
  const openMemoPanels = new Set(); // "card-1", "benefit-3" 같은 키 - 펼쳐진 설명 기억
  const openCompactPanels = new Set(); // card id 목록 - "월/연 횟수 혜택" 펼침 기억

  async function api(url, options) {
    const res = await fetch(url, options);
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || "오류가 발생했습니다.");
      throw new Error(data.error || "request failed");
    }
    return data;
  }

  function fmt(n) {
    return Number(n).toLocaleString("ko-KR");
  }

  function shortDate(iso) {
    return (iso || "").replaceAll("-", "").slice(2);
  }

  function shortCardName(name) {
    return (name || "").replace(/신한카드\s*/g, "").replace(/\s*\([^)]*\)/g, "").trim();
  }

  function shortBenefitName(name) {
    return (name || "").replace(/\s*\([^)]*\)/g, "").trim();
  }

  function splitBenefitName(name) {
    const idx = (name || "").indexOf(" - ");
    if (idx < 0) return [name, ""];
    return [name.slice(0, idx).trim(), name.slice(idx + 3).trim()];
  }

  function statusClass(percent, overLimit) {
    if (overLimit) return "bad";
    if (percent >= 80) return "warn";
    return "good";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function highlightDouble(escapedText) {
    return escapedText.replace(/\(2배\)/g, '<span class="badge-double">2배 특별적립</span>');
  }

  function render() {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    renderInbox();
    renderNoBenefit();
    renderCards();
    renderMonthlySummary();
    requestAnimationFrame(() => window.scrollTo(scrollX, scrollY));
  }

  // ---- 최상단 이번 달 적립/캐시백 합계 ----
  function renderMonthlySummary() {
    const section = document.getElementById("monthly-summary");
    let earn = 0;
    let cashback = 0;
    state.cards.forEach((card) => {
      card.benefits.forEach((b) => {
        if (b.calc_mode === "change_under_1000") earn += b.used || 0;
        else if (b.calc_mode === "percent_discount") cashback += b.used || 0;
      });
    });
    section.style.display = state.cards.length ? "block" : "none";
    const monthLabel = `${new Date().getMonth() + 1}월`;
    document.getElementById("summary-earn-label").textContent = `${monthLabel} 적립`;
    document.getElementById("summary-cashback-label").textContent = `${monthLabel} 할인·캐시백`;
    document.getElementById("summary-earn-value").textContent = `${fmt(earn)}원`;
    document.getElementById("summary-cashback-value").textContent = `${fmt(cashback)}원`;

    const jumpRow = document.getElementById("summary-jump-row");
    jumpRow.innerHTML = state.cards.map((card, idx) => `
      <button class="jump-btn" data-card-id="${card.id}" style="border-color:${CARD_COLORS[idx % CARD_COLORS.length]};color:${CARD_COLORS[idx % CARD_COLORS.length]};">
        ${escapeHtml(shortCardName(card.name))}
      </button>
    `).join("");
    jumpRow.querySelectorAll(".jump-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        document.getElementById(`card-${btn.dataset.cardId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      })
    );
  }

  function memoToggleTemplate(key, memo) {
    if (!memo) return "";
    const open = openMemoPanels.has(key);
    return `
      <button class="memo-toggle" data-memo-key="${key}">${open ? "설명 접기 ▲" : "설명 보기 ▾"}</button>
      <div class="memo-text ${open ? "open" : ""}" data-memo-for="${key}">${escapeHtml(memo)}</div>
    `;
  }

  // ---- 받은 알림 ----
  function renderInbox() {
    const items = state.inbox;
    inboxSection.style.display = items.length ? "block" : "none";
    inboxCount.textContent = items.length ? `${items.length}건` : "";
    inboxList.innerHTML = items.map((it) => `
      <div class="inbox-item" data-id="${it.id}">
        <div class="inbox-item-info">
          <div class="inbox-item-amount">${it.amount != null ? fmt(it.amount) + "원" : "(금액 미확인)"}</div>
          <div class="inbox-item-sub">${it.occurred_at}${it.matched_card_name ? " · " + escapeHtml(shortCardName(it.matched_card_name)) : ""}${it.merchant ? " · " + escapeHtml(it.merchant) : ""}</div>
          ${it.matched_benefit_name ? `<div class="inbox-item-sub">🎯 ${escapeHtml(shortBenefitName(it.matched_benefit_name))}</div>` : ""}
        </div>
        <button class="btn small assign-inbox" data-id="${it.id}">혜택 선택</button>
      </div>
    `).join("");

    inboxList.querySelectorAll(".assign-inbox").forEach((btn) =>
      btn.addEventListener("click", () => {
        const item = state.inbox.find((x) => x.id == btn.dataset.id);
        openAssignModal(item);
      })
    );
  }

  // ---- 혜택 없음 처리 내역 ----
  function renderNoBenefit() {
    const items = state.no_benefit || [];
    noBenefitSection.style.display = items.length ? "block" : "none";
    noBenefitCount.textContent = items.length ? `${items.length}건` : "";
    noBenefitList.style.display = noBenefitOpen ? "flex" : "none";
    noBenefitList.innerHTML = items.map((it) => `
      <div class="inbox-item" data-id="${it.id}">
        <div class="inbox-item-info">
          <div class="inbox-item-amount">${it.amount != null ? fmt(it.amount) + "원" : "(금액 미확인)"}</div>
          <div class="inbox-item-sub">${it.occurred_at}${it.card_name ? " · " + escapeHtml(shortCardName(it.card_name)) : ""}${it.merchant ? " · " + escapeHtml(it.merchant) : ""}</div>
        </div>
        <div class="benefit-buttons">
          <button class="btn secondary small reopen-no-benefit" data-id="${it.id}">다시 확인</button>
          <button class="btn secondary small delete-no-benefit" data-id="${it.id}">완전 삭제</button>
        </div>
      </div>
    `).join("");

    noBenefitList.querySelectorAll(".reopen-no-benefit").forEach((btn) =>
      btn.addEventListener("click", async () => {
        state = await api(`/api/inbox/${btn.dataset.id}/reopen`, { method: "POST" });
        render();
      })
    );
    noBenefitList.querySelectorAll(".delete-no-benefit").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("완전히 삭제할까요? 되돌릴 수 없습니다.")) return;
        state = await api(`/api/inbox/${btn.dataset.id}`, { method: "DELETE" });
        render();
      })
    );
  }
  document.getElementById("no-benefit-toggle").addEventListener("click", () => {
    noBenefitOpen = !noBenefitOpen;
    render();
  });

  // ---- 카드 목록 ----
  function renderCards() {
    cardList.querySelectorAll(".card").forEach((el) => el.remove());
    emptyHint.style.display = state.cards.length ? "none" : "block";

    state.cards.forEach((card, idx) => {
      const el = document.createElement("div");
      el.className = "card";
      el.id = `card-${card.id}`;
      el.innerHTML = cardTemplate(card);
      el.querySelector(".card-title").style.color = CARD_COLORS[idx % CARD_COLORS.length];
      cardList.appendChild(el);
      wireCard(el, card);
    });
  }

  function cardTemplate(card) {
    const mainBenefits = card.benefits.filter((b) => b.limit_type !== "count");
    const compactBenefits = card.benefits.filter((b) => b.limit_type === "count");

    const benefitsHtml = mainBenefits.map((b) => benefitTemplate(card, b)).join("") ||
      (compactBenefits.length ? "" : `<p class="hint">등록된 혜택이 없습니다.</p>`);

    const compactOpen = openCompactPanels.has(card.id);
    const compactSectionHtml = compactBenefits.length ? `
      <div class="benefit-compact-section">
        <button class="benefit-compact-toggle" data-card-id="${card.id}">
          🎫 월/연 횟수 혜택 (${compactBenefits.length}개) ${compactOpen ? "▲" : "▾"}
        </button>
        <div class="benefit-compact-list ${compactOpen ? "open" : ""}">
          ${compactBenefits.map((b) => compactBenefitTemplate(card, b)).join("")}
        </div>
      </div>
    ` : "";

    return `
      <div class="card-head">
        <div class="card-title-row">
          <span class="card-title">${escapeHtml(shortCardName(card.name))}</span>
          ${card.perf_threshold ? `<span class="perf-badge ${card.perf_met ? "met" : "unmet"}">${card.perf_met ? "실적 충족" : "실적 부족"}</span>` : ""}
        </div>
        <div class="card-actions">
          <button class="icon-btn edit-card" data-id="${card.id}" title="카드 수정">✏️</button>
          <button class="icon-btn delete-card" data-id="${card.id}" title="카드 삭제">🗑️</button>
        </div>
      </div>
      <div class="card-cycle">혜택기간 ${shortDate(card.cycle_start)}~${shortDate(card.cycle_end)} (D-${card.days_left})</div>
      ${perfRowTemplate(card)}
      ${memoToggleTemplate(`card-${card.id}`, card.memo)}
      <div class="benefit-list">${benefitsHtml}</div>
      ${compactSectionHtml}
      <div class="add-benefit-row">
        <button class="btn secondary small add-benefit" data-card-id="${card.id}">+ 혜택 추가</button>
      </div>
    `;
  }

  function perfRowTemplate(card) {
    if (!card.perf_threshold) return "";
    const autoTag = card.perf_auto ? " (자동)" : "";
    return `
      <div class="perf-row">
        <span class="perf-current">당월 ${fmt(card.this_month_spend)}원 (다음달 반영)</span>
      </div>
      <div class="perf-row perf-row-sub">
        <span>전월 ${fmt(card.perf_spend)}원 / ${fmt(card.perf_threshold)}원${autoTag}</span>
        <button class="perf-edit" data-id="${card.id}" data-spend="${card.perf_spend}">수정</button>
      </div>
    `;
  }

  function logsListHtml(b, unit) {
    return b.logs.length
      ? b.logs.map((l) => {
          const timePart = l.notif_occurred_at ? l.notif_occurred_at.split(" ")[1]?.slice(0, 5) : "";
          const dateLabel = timePart ? `${l.used_at} ${timePart}` : l.used_at;
          const merchantLabel = l.merchant ? `<strong>${escapeHtml(l.merchant)}</strong> · ` : "";
          // memo가 이미 "결제 X원 → 잔돈/할인 Y원" 형태로 금액을 포함하면
          // 앞에 금액을 또 안 보여준다 (같은 금액이 두 번 찍히는 것 방지)
          const hasBreakdown = l.memo && l.memo.includes("→");
          const valuePart = hasBreakdown
            ? highlightDouble(escapeHtml(l.memo))
            : `${fmt(l.used_value)}${unit}${l.memo ? " · " + highlightDouble(escapeHtml(l.memo)) : ""}`;
          return `
          <div class="log-item">
            <span>${dateLabel} · ${merchantLabel}${valuePart}</span>
            <button class="delete-log" data-id="${l.id}">삭제</button>
          </div>`;
        }).join("")
      : `<div class="log-item"><span>이번 기간 사용 기록 없음</span></div>`;
  }

  function compactBenefitTemplate(card, b) {
    const unit = "회";
    const logsOpen = openLogPanels.has(b.id);
    const cls = statusClass(b.percent, b.over_limit);
    const remainingText = b.unlimited
      ? `누적 ${fmt(b.used)}${unit}`
      : (b.over_limit ? `초과 ${fmt(Math.abs(b.remaining))}${unit}` : `잔여 ${fmt(b.remaining)}${unit}`);
    return `
      <div class="benefit-compact" data-benefit-id="${b.id}">
        <div class="benefit-compact-top">
          <span class="benefit-compact-name">${escapeHtml(shortBenefitName(b.name))}</span>
          <span class="benefit-remaining ${b.unlimited ? "" : "remaining-" + cls}">${remainingText}</span>
        </div>
        <div class="benefit-sub">
          <div class="benefit-buttons">
            <button class="icon-btn use-benefit" data-id="${b.id}" data-card-id="${card.id}" data-type="${b.limit_type}" title="사용 기록">➕</button>
            <button class="btn small toggle-log" data-id="${b.id}">내역</button>
            <button class="icon-btn edit-benefit" data-id="${b.id}" data-card-id="${card.id}">✏️</button>
            <button class="icon-btn delete-benefit" data-id="${b.id}">🗑️</button>
          </div>
        </div>
        <div class="log-list ${logsOpen ? "open" : ""}" data-log-for="${b.id}">${logsListHtml(b, unit)}</div>
      </div>
    `;
  }

  function benefitTemplate(card, b) {
    const unit = b.limit_type === "count" ? "회" : "원";
    const logsOpen = openLogPanels.has(b.id);
    const logsHtml = logsListHtml(b, unit);

    const rightSideHtml = b.unlimited
      ? `<div class="benefit-remaining remaining-good">이번 달 ${fmt(b.used)}${unit} 적립</div>`
      : (() => {
          const cls = statusClass(b.percent, b.over_limit);
          const remainingText = b.over_limit
            ? `초과 ${fmt(Math.abs(b.remaining))}${unit}`
            : `잔여 ${fmt(b.remaining)}${unit}`;
          return `<div class="benefit-remaining remaining-${cls}">${remainingText}</div>`;
        })();

    const progressHtml = b.unlimited
      ? ""
      : (() => {
          const cls = statusClass(b.percent, b.over_limit);
          return `<div class="progress-track"><div class="progress-fill ${cls}" style="width:${b.percent}%"></div></div>`;
        })();

    const subText = b.unlimited
      ? ``
      : `한도 ${fmt(b.limit_value)}${unit} 중 ${fmt(b.used)}${unit} 사용`;

    const categoryUsageHtml = (b.category_usage && b.category_usage.length)
      ? `<div class="category-usage">${b.category_usage.map((c) => {
          let text;
          if (c.monthly_limit) text = `${escapeHtml(c.label)} 월${fmt(c.count)}/${fmt(c.monthly_limit)}회`;
          else if (c.daily_limit) text = `${escapeHtml(c.label)} 일${fmt(c.today_count)}/${fmt(c.daily_limit)}회`;
          else text = `${escapeHtml(c.label)} ${fmt(c.count)}회`;
          return `<span class="category-usage-item">${text}</span>`;
        }).join("")}</div>`
      : "";

    const [benefitTitle, benefitSubtitle] = splitBenefitName(shortBenefitName(b.name));

    return `
      <div class="benefit" data-benefit-id="${b.id}">
        <div class="benefit-top">
          <div class="benefit-name">${escapeHtml(benefitTitle)}</div>
          ${rightSideHtml}
        </div>
        ${benefitSubtitle ? `<div class="benefit-subtitle">${escapeHtml(benefitSubtitle)}</div>` : ""}
        ${memoToggleTemplate(`benefit-${b.id}`, b.memo)}
        ${progressHtml}
        ${categoryUsageHtml}
        <div class="benefit-sub">
          <span>${subText}</span>
          <div class="benefit-buttons">
            <button class="icon-btn use-benefit" data-id="${b.id}" data-card-id="${card.id}" data-type="${b.limit_type}" title="사용 기록">➕</button>
            <button class="btn small toggle-log" data-id="${b.id}">내역</button>
            <button class="icon-btn edit-benefit" data-id="${b.id}" data-card-id="${card.id}">✏️</button>
            <button class="icon-btn delete-benefit" data-id="${b.id}">🗑️</button>
          </div>
        </div>
        <div class="log-list ${logsOpen ? "open" : ""}" data-log-for="${b.id}">${logsHtml}</div>
      </div>
    `;
  }

  function wireCard(el, card) {
    el.querySelector(".edit-card").addEventListener("click", () => openCardModal(card));
    const perfEditBtn = el.querySelector(".perf-edit");
    if (perfEditBtn) {
      perfEditBtn.addEventListener("click", () => openPerfModal(card));
    }
    el.querySelector(".delete-card").addEventListener("click", async () => {
      if (!confirm(`"${card.name}" 카드를 삭제할까요? 혜택/사용기록도 모두 삭제됩니다.`)) return;
      state = await api(`/api/cards/${card.id}`, { method: "DELETE" });
      render();
    });
    el.querySelector(".add-benefit").addEventListener("click", () => openBenefitModal(card.id));
    const compactToggleBtn = el.querySelector(".benefit-compact-toggle");
    if (compactToggleBtn) {
      compactToggleBtn.addEventListener("click", () => {
        const id = Number(compactToggleBtn.dataset.cardId);
        if (openCompactPanels.has(id)) openCompactPanels.delete(id);
        else openCompactPanels.add(id);
        render();
      });
    }

    el.querySelectorAll(".use-benefit").forEach((btn) =>
      btn.addEventListener("click", () => {
        const c = state.cards.find((x) => x.id == btn.dataset.cardId);
        const b = c.benefits.find((x) => x.id == btn.dataset.id);
        openUseModal(b);
      })
    );
    el.querySelectorAll(".edit-benefit").forEach((btn) =>
      btn.addEventListener("click", () => {
        const c = state.cards.find((x) => x.id == btn.dataset.cardId);
        const b = c.benefits.find((x) => x.id == btn.dataset.id);
        openBenefitModal(c.id, b);
      })
    );
    el.querySelectorAll(".delete-benefit").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 혜택을 삭제할까요?")) return;
        state = await api(`/api/benefits/${btn.dataset.id}`, { method: "DELETE" });
        render();
      })
    );
    el.querySelectorAll(".toggle-log").forEach((btn) =>
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.id);
        if (openLogPanels.has(id)) openLogPanels.delete(id);
        else openLogPanels.add(id);
        render();
      })
    );
    el.querySelectorAll(".memo-toggle").forEach((btn) =>
      btn.addEventListener("click", () => {
        const key = btn.dataset.memoKey;
        if (openMemoPanels.has(key)) openMemoPanels.delete(key);
        else openMemoPanels.add(key);
        render();
      })
    );
    el.querySelectorAll(".delete-log").forEach((btn) =>
      btn.addEventListener("click", async () => {
        state = await api(`/api/logs/${btn.dataset.id}`, { method: "DELETE" });
        render();
      })
    );
  }

  // ---- 카드 모달 ----
  const cardModal = document.getElementById("card-modal");
  let editingCardId = null;

  function openCardModal(card) {
    editingCardId = card ? card.id : null;
    document.getElementById("card-modal-title").textContent = card ? "카드 수정" : "카드 추가";
    document.getElementById("card-name").value = card ? card.name : "";
    document.getElementById("card-issuer").value = card ? card.issuer : "";
    document.getElementById("card-last4").value = card ? card.last4 : "";
    document.getElementById("card-reset-day").value = card ? card.reset_day : 1;
    document.getElementById("card-perf-threshold").value = card && card.perf_threshold ? card.perf_threshold : "";
    document.getElementById("card-memo").value = card ? card.memo : "";
    cardModal.classList.add("open");
  }
  document.getElementById("btn-add-card").addEventListener("click", () => openCardModal(null));
  document.getElementById("card-cancel").addEventListener("click", () => cardModal.classList.remove("open"));
  document.getElementById("card-save").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("card-name").value.trim(),
      issuer: document.getElementById("card-issuer").value.trim(),
      last4: document.getElementById("card-last4").value.trim(),
      reset_day: document.getElementById("card-reset-day").value,
      perf_threshold: document.getElementById("card-perf-threshold").value || 0,
      memo: document.getElementById("card-memo").value.trim(),
    };
    if (!payload.name) { alert("카드 이름을 입력하세요."); return; }
    const url = editingCardId ? `/api/cards/${editingCardId}` : "/api/cards";
    const method = editingCardId ? "PUT" : "POST";
    state = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    cardModal.classList.remove("open");
    render();
  });

  // ---- 전월실적(지난달 사용액) 입력 모달 ----
  const perfModal = document.getElementById("perf-modal");
  let editingPerfCardId = null;

  function openPerfModal(card) {
    editingPerfCardId = card.id;
    document.getElementById("perf-value").value = card.perf_spend || "";
    perfModal.classList.add("open");
  }
  document.getElementById("perf-cancel").addEventListener("click", () => perfModal.classList.remove("open"));
  document.getElementById("perf-save").addEventListener("click", async () => {
    const total_spend = document.getElementById("perf-value").value;
    if (total_spend === "" || Number(total_spend) < 0) {
      alert("사용액을 입력하세요.");
      return;
    }
    state = await api(`/api/cards/${editingPerfCardId}/performance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total_spend }),
    });
    perfModal.classList.remove("open");
    render();
  });

  // ---- 혜택 모달 ----
  const benefitModal = document.getElementById("benefit-modal");
  let editingBenefit = null; // { cardId, benefitId }

  function updateBenefitCalcFieldsUI() {
    const mode = document.getElementById("benefit-calc-mode").value;
    const show = mode === "percent_discount" ? "block" : "none";
    document.getElementById("benefit-percent-row").style.display = show;
    document.getElementById("benefit-cap-row").style.display = show;
    document.getElementById("benefit-rate-table-row").style.display = show;
    document.getElementById("benefit-always-doubled-row").style.display = mode === "change_under_1000" ? "flex" : "none";
  }
  document.getElementById("benefit-calc-mode").addEventListener("change", updateBenefitCalcFieldsUI);

  function openBenefitModal(cardId, benefit) {
    editingBenefit = { cardId, benefitId: benefit ? benefit.id : null };
    document.getElementById("benefit-modal-title").textContent = benefit ? "혜택 수정" : "혜택 추가";
    document.getElementById("benefit-name").value = benefit ? benefit.name : "";
    document.getElementById("benefit-type").value = benefit ? benefit.limit_type : "amount";
    document.getElementById("benefit-limit").value = benefit ? benefit.limit_value : "";
    document.getElementById("benefit-calc-mode").value = benefit ? benefit.calc_mode : "raw";
    document.getElementById("benefit-always-doubled").checked = !!(benefit && benefit.always_doubled);
    document.getElementById("benefit-percent").value = benefit && benefit.discount_percent ? benefit.discount_percent : "";
    document.getElementById("benefit-cap").value = benefit && benefit.per_txn_cap ? benefit.per_txn_cap : "";
    document.getElementById("benefit-rate-table").value = benefit ? benefit.rate_table : "";
    document.getElementById("benefit-keywords").value = benefit ? benefit.merchant_keywords : "";
    document.getElementById("benefit-tiers").value = benefit ? benefit.tier_table : "";
    document.getElementById("benefit-memo").value = benefit ? benefit.memo : "";
    updateBenefitCalcFieldsUI();
    benefitModal.classList.add("open");
  }
  document.getElementById("benefit-cancel").addEventListener("click", () => benefitModal.classList.remove("open"));
  document.getElementById("benefit-save").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("benefit-name").value.trim(),
      limit_type: document.getElementById("benefit-type").value,
      limit_value: document.getElementById("benefit-limit").value,
      calc_mode: document.getElementById("benefit-calc-mode").value,
      always_doubled: document.getElementById("benefit-always-doubled").checked,
      discount_percent: document.getElementById("benefit-percent").value || 0,
      per_txn_cap: document.getElementById("benefit-cap").value || 0,
      rate_table: document.getElementById("benefit-rate-table").value.trim(),
      merchant_keywords: document.getElementById("benefit-keywords").value.trim(),
      tier_table: document.getElementById("benefit-tiers").value.trim(),
      memo: document.getElementById("benefit-memo").value.trim(),
    };
    if (!payload.name) { alert("혜택 이름을 입력하세요."); return; }
    if (payload.calc_mode === "percent_discount" && (!payload.discount_percent || Number(payload.discount_percent) <= 0)) {
      alert("기본 할인율(%)을 입력하세요.");
      return;
    }
    const url = editingBenefit.benefitId
      ? `/api/benefits/${editingBenefit.benefitId}`
      : `/api/cards/${editingBenefit.cardId}/benefits`;
    const method = editingBenefit.benefitId ? "PUT" : "POST";
    state = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    benefitModal.classList.remove("open");
    render();
  });

  // ---- 사용 기록 모달 (수동 입력) ----
  const useModal = document.getElementById("use-modal");
  let usingBenefit = null;

  function computeChangeEarned(amount, doubled) {
    amount = Number(amount) || 0;
    if (amount < 5000) return 0;
    const remainder = amount % 1000;
    return remainder * (doubled ? 2 : 1);
  }

  function computePercentDiscount(amount, percent, cap) {
    amount = Number(amount) || 0;
    percent = Number(percent) || 0;
    let base = amount;
    if (cap && cap > 0) base = Math.min(base, cap);
    return Math.round((base * percent) / 100);
  }

  function matchRateTable(merchant, rateTableJson, defaultPercent, defaultCap) {
    if (!rateTableJson || !merchant) return [defaultPercent, defaultCap, ""];
    let rows;
    try {
      rows = JSON.parse(rateTableJson);
    } catch (e) {
      return [defaultPercent, defaultCap, ""];
    }
    if (!Array.isArray(rows)) return [defaultPercent, defaultCap, ""];
    const merchantLower = merchant.toLowerCase();
    for (const entry of rows) {
      if (!Array.isArray(entry) || entry.length < 2 || entry.length > 5) continue;
      const rawKeywords = String(entry[0]);
      const colonIdx = rawKeywords.indexOf(":");
      const label = colonIdx >= 0 ? rawKeywords.slice(0, colonIdx) : rawKeywords;
      const kwPart = colonIdx >= 0 ? rawKeywords.slice(colonIdx + 1) : rawKeywords;
      const percent = entry[1];
      const cap = entry.length >= 3 ? entry[2] : defaultCap;
      for (const kw of kwPart.split(",")) {
        const k = kw.trim().toLowerCase();
        if (k && merchantLower.includes(k)) return [percent, cap, label];
      }
    }
    return [defaultPercent, defaultCap, ""];
  }

  function rateTableEntryByLabel(rateTableJson, label, defaultPercent, defaultCap) {
    if (!rateTableJson || !label) return [defaultPercent, defaultCap];
    let rows;
    try {
      rows = JSON.parse(rateTableJson);
    } catch (e) {
      return [defaultPercent, defaultCap];
    }
    if (!Array.isArray(rows)) return [defaultPercent, defaultCap];
    for (const entry of rows) {
      if (!Array.isArray(entry) || entry.length < 2 || entry.length > 5) continue;
      const rawKeywords = String(entry[0]);
      const colonIdx = rawKeywords.indexOf(":");
      const entryLabel = colonIdx >= 0 ? rawKeywords.slice(0, colonIdx) : rawKeywords;
      if (entryLabel === label) {
        const cap = entry.length >= 3 ? entry[2] : defaultCap;
        return [entry[1], cap];
      }
    }
    return [defaultPercent, defaultCap];
  }

  function updateUseChangePreview() {
    if (!usingBenefit) return;
    const amount = document.getElementById("use-value").value;
    const preview = document.getElementById("use-change-preview");
    if (usingBenefit.calc_mode === "change_under_1000") {
      const doubled = document.getElementById("use-doubled").checked;
      if (!amount) { preview.style.display = "none"; return; }
      preview.style.display = "block";
      preview.textContent = Number(amount) < 5000
        ? "건당 5,000원 미만은 적립되지 않습니다."
        : `잔돈 적립 예상: ${fmt(computeChangeEarned(amount, doubled))}원${doubled ? " (2배 적용)" : ""}`;
      return;
    }
    if (usingBenefit.calc_mode === "percent_discount") {
      if (!amount) { preview.style.display = "none"; return; }
      const manualCategory = document.getElementById("use-category").value;
      let percent, cap, label;
      if (manualCategory) {
        [percent, cap] = rateTableEntryByLabel(usingBenefit.rate_table, manualCategory, usingBenefit.discount_percent, usingBenefit.per_txn_cap);
        label = manualCategory;
      } else {
        const merchant = document.getElementById("use-merchant").value.trim();
        [percent, cap, label] = matchRateTable(merchant, usingBenefit.rate_table, usingBenefit.discount_percent, usingBenefit.per_txn_cap);
      }
      const earned = computePercentDiscount(amount, percent, cap);
      preview.style.display = "block";
      preview.textContent = `할인 예상: ${fmt(earned)}원 (${label ? label + " " : ""}${percent}%${cap ? `, ${fmt(cap)}원까지만 적용` : ""})`;
      return;
    }
    preview.style.display = "none";
  }

  function openUseModal(benefit) {
    usingBenefit = benefit;
    const isChange = benefit.calc_mode === "change_under_1000";
    const isPercent = benefit.calc_mode === "percent_discount";
    document.getElementById("use-modal-title").textContent = isChange
      ? "잔돈 적립 기록"
      : (isPercent ? "할인 받은 결제 기록" : "혜택 사용 기록");
    document.getElementById("use-value-label").firstChild.textContent =
      (isChange || isPercent) ? "결제 금액 (원) " : (benefit.limit_type === "count" ? "사용 횟수 (회) " : "사용 금액 (원) ");
    document.getElementById("use-value").value = !isChange && !isPercent && benefit.limit_type === "count" ? 1 : "";
    document.getElementById("use-merchant-row").style.display = (isChange || isPercent) ? "flex" : "none";
    document.getElementById("use-doubled-row").style.display = (isChange && !benefit.always_doubled) ? "flex" : "none";
    document.getElementById("use-merchant").value = "";
    document.getElementById("use-doubled").checked = !!benefit.always_doubled;
    document.getElementById("use-change-preview").style.display = "none";
    document.getElementById("use-date").value = new Date().toISOString().slice(0, 10);
    document.getElementById("use-memo").value = "";
    const useCategoryRow = document.getElementById("use-category-row");
    const useCategorySelect = document.getElementById("use-category");
    if (isPercent && benefit.category_usage && benefit.category_usage.length) {
      useCategoryRow.style.display = "flex";
      useCategorySelect.innerHTML = `<option value="">자동인식(가맹점명 기준)</option>` +
        benefit.category_usage.map((c) => `<option value="${escapeHtml(c.label)}">${escapeHtml(c.label)}</option>`).join("");
    } else {
      useCategoryRow.style.display = "none";
      useCategorySelect.innerHTML = `<option value="">자동인식(가맹점명 기준)</option>`;
    }
    useModal.classList.add("open");
  }
  document.getElementById("use-value").addEventListener("input", updateUseChangePreview);
  document.getElementById("use-doubled").addEventListener("change", updateUseChangePreview);
  document.getElementById("use-merchant").addEventListener("input", updateUseChangePreview);
  document.getElementById("use-category").addEventListener("change", updateUseChangePreview);
  document.getElementById("use-cancel").addEventListener("click", () => useModal.classList.remove("open"));
  document.getElementById("use-save").addEventListener("click", async () => {
    const payload = {
      used_value: document.getElementById("use-value").value,
      used_at: document.getElementById("use-date").value,
      memo: document.getElementById("use-memo").value.trim(),
    };
    if (usingBenefit.calc_mode === "change_under_1000" || usingBenefit.calc_mode === "percent_discount") {
      payload.merchant = document.getElementById("use-merchant").value.trim();
    }
    if (usingBenefit.calc_mode === "percent_discount") {
      payload.category = document.getElementById("use-category").value;
    }
    if (usingBenefit.calc_mode === "change_under_1000") {
      payload.doubled = document.getElementById("use-doubled").checked;
    }
    if (!payload.used_value || Number(payload.used_value) <= 0) {
      alert("사용 금액(또는 횟수)을 입력하세요.");
      return;
    }
    state = await api(`/api/benefits/${usingBenefit.id}/use`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    useModal.classList.remove("open");
    render();
  });

  // ---- 받은 알림 -> 혜택 배정 모달 ----
  const assignModal = document.getElementById("assign-modal");
  const assignCardSelect = document.getElementById("assign-card");
  const assignBenefitSelect = document.getElementById("assign-benefit");
  let assigningItem = null;

  function fillAssignBenefitOptions(cardId, preselectBenefitId) {
    const card = state.cards.find((c) => c.id == cardId);
    const benefits = card ? card.benefits : [];
    const placeholder = benefits.length
      ? `<option value="">해당 없음</option>`
      : `<option value="">등록된 혜택이 없습니다</option>`;
    assignBenefitSelect.innerHTML = placeholder + benefits.map((b) => {
      const unit = b.limit_type === "count" ? "회" : "원";
      const tag = b.unlimited ? "" : ` (잔여 ${fmt(b.remaining)}${unit})`;
      return `<option value="${b.id}">${escapeHtml(shortBenefitName(b.name))}${tag}</option>`;
    }).join("");
    assignBenefitSelect.value = preselectBenefitId || "";
  }

  function currentAssignBenefit() {
    const card = state.cards.find((c) => c.id == assignCardSelect.value);
    if (!card) return null;
    return card.benefits.find((b) => b.id == assignBenefitSelect.value) || null;
  }

  function updateAssignChangeUI() {
    const benefit = currentAssignBenefit();
    const isChange = !!benefit && benefit.calc_mode === "change_under_1000";
    const isPercent = !!benefit && benefit.calc_mode === "percent_discount";
    document.getElementById("assign-amount-label").firstChild.textContent = (isChange || isPercent) ? "결제 금액 " : "금액 ";
    document.getElementById("assign-doubled-row").style.display = (isChange && !benefit.always_doubled) ? "flex" : "none";
    if (isChange && benefit.always_doubled) document.getElementById("assign-doubled").checked = true;

    const categoryRow = document.getElementById("assign-category-row");
    const categorySelect = document.getElementById("assign-category");
    if (isPercent && benefit.category_usage && benefit.category_usage.length) {
      categoryRow.style.display = "flex";
      const existingValue = categorySelect.value;
      categorySelect.innerHTML = `<option value="">자동인식(가맹점명 기준)</option>` +
        benefit.category_usage.map((c) => `<option value="${escapeHtml(c.label)}">${escapeHtml(c.label)}</option>`).join("");
      categorySelect.value = existingValue;
    } else {
      categoryRow.style.display = "none";
      categorySelect.innerHTML = `<option value="">자동인식(가맹점명 기준)</option>`;
    }

    const preview = document.getElementById("assign-change-preview");
    const amount = document.getElementById("assign-amount").value;
    if (isChange && amount) {
      const doubled = document.getElementById("assign-doubled").checked;
      preview.style.display = "block";
      preview.textContent = Number(amount) < 5000
        ? "건당 5,000원 미만은 적립되지 않습니다."
        : `잔돈 적립 예상: ${fmt(computeChangeEarned(amount, doubled))}원${doubled ? " (2배 적용)" : ""}`;
      return;
    }
    if (isPercent && amount) {
      const manualCategory = categorySelect.value;
      let percent, cap, label;
      if (manualCategory) {
        [percent, cap] = rateTableEntryByLabel(benefit.rate_table, manualCategory, benefit.discount_percent, benefit.per_txn_cap);
        label = manualCategory;
      } else {
        const merchant = (assigningItem && assigningItem.merchant) || "";
        [percent, cap, label] = matchRateTable(merchant, benefit.rate_table, benefit.discount_percent, benefit.per_txn_cap);
      }
      const earned = computePercentDiscount(amount, percent, cap);
      preview.style.display = "block";
      preview.textContent = `할인 예상: ${fmt(earned)}원 (${label ? label + " " : ""}${percent}%${cap ? `, ${fmt(cap)}원까지만 적용` : ""})`;
      return;
    }
    preview.style.display = "none";
  }

  function openAssignModal(item) {
    if (!state.cards.length) {
      alert("먼저 카드와 혜택을 등록해주세요.");
      return;
    }
    assigningItem = item;
    document.getElementById("assign-raw-text").textContent = item.raw_text;
    assignCardSelect.innerHTML = state.cards
      .map((c) => `<option value="${c.id}">${escapeHtml(shortCardName(c.name))}</option>`)
      .join("");
    const preselectCardId = item.matched_card_id || state.cards[0].id;
    assignCardSelect.value = preselectCardId;
    fillAssignBenefitOptions(preselectCardId, item.matched_benefit_id);

    document.getElementById("assign-amount").value = item.amount != null ? item.amount : "";
    document.getElementById("assign-doubled").checked = !!item.matched_benefit_doubled;
    document.getElementById("assign-date").value = (item.occurred_at || "").slice(0, 10) || new Date().toISOString().slice(0, 10);
    updateAssignChangeUI();
    assignModal.classList.add("open");
  }

  assignCardSelect.addEventListener("change", () => {
    fillAssignBenefitOptions(assignCardSelect.value);
    updateAssignChangeUI();
  });
  assignBenefitSelect.addEventListener("change", updateAssignChangeUI);
  document.getElementById("assign-amount").addEventListener("input", updateAssignChangeUI);
  document.getElementById("assign-doubled").addEventListener("change", updateAssignChangeUI);
  document.getElementById("assign-category").addEventListener("change", updateAssignChangeUI);

  document.getElementById("assign-cancel").addEventListener("click", () => assignModal.classList.remove("open"));

  document.getElementById("assign-discard").addEventListener("click", async () => {
    if (!confirm("이 알림을 무시할까요? (광고 등 결제와 무관한 알림일 때)")) return;
    state = await api(`/api/inbox/${assigningItem.id}`, { method: "DELETE" });
    assignModal.classList.remove("open");
    render();
  });

  document.getElementById("assign-no-benefit").addEventListener("click", async () => {
    state = await api(`/api/inbox/${assigningItem.id}/mark-no-benefit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: assignCardSelect.value || null }),
    });
    assignModal.classList.remove("open");
    render();
  });

  document.getElementById("assign-save").addEventListener("click", async () => {
    const benefitId = assignBenefitSelect.value;
    if (!benefitId) { alert("\"혜택\" 항목이 아직 \"해당 없음\"으로 되어있습니다. 이 결제가 어떤 혜택에 해당하는지 위에서 골라야 기록됩니다."); return; }
    const benefit = currentAssignBenefit();
    const payload = {
      benefit_id: benefitId,
      amount: document.getElementById("assign-amount").value,
      used_at: document.getElementById("assign-date").value,
    };
    if (benefit && benefit.calc_mode === "change_under_1000") {
      payload.merchant = assigningItem.merchant || "";
      payload.doubled = document.getElementById("assign-doubled").checked;
    }
    if (benefit && benefit.calc_mode === "percent_discount") {
      payload.category = document.getElementById("assign-category").value;
    }
    state = await api(`/api/inbox/${assigningItem.id}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    assignModal.classList.remove("open");
    render();
  });

  // ---- 자동수집(웹훅) 안내 모달 ----
  const webhookModal = document.getElementById("webhook-modal");

  async function loadWebhookInfo() {
    const info = await api("/api/settings/inbox");
    const url = `${location.origin}${info.webhook_path}?token=${info.token}`;
    document.getElementById("webhook-url").value = url;
    document.getElementById("webhook-token").value = info.token;
  }

  document.getElementById("btn-webhook-info").addEventListener("click", async () => {
    await loadWebhookInfo();
    webhookModal.classList.add("open");
  });
  document.getElementById("webhook-close").addEventListener("click", () => webhookModal.classList.remove("open"));
  document.getElementById("webhook-regenerate").addEventListener("click", async () => {
    if (!confirm("토큰을 재발급하면 기존 MacroDroid 설정을 새 주소로 다시 바꿔줘야 합니다. 계속할까요?")) return;
    const info = await api("/api/settings/inbox/regenerate", { method: "POST" });
    const url = `${location.origin}${info.webhook_path}?token=${info.token}`;
    document.getElementById("webhook-url").value = url;
    document.getElementById("webhook-token").value = info.token;
  });

  // ---- 지난/누락 내역 엑셀 업로드 ----
  const importFile = document.getElementById("import-file");
  const importLog = document.getElementById("import-log");

  importFile.addEventListener("change", async () => {
    const file = importFile.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    importLog.innerHTML = `<div class="upload-log-item">업로드 중...</div>`;
    let data;
    try {
      const res = await fetch("/api/import/usage", { method: "POST", body: formData });
      data = await res.json();
      if (!res.ok) throw new Error(data.error || "업로드 실패");
    } catch (e) {
      importLog.innerHTML = `<div class="upload-log-item err">${escapeHtml(e.message)}</div>`;
      importFile.value = "";
      return;
    }

    state = data;
    render();

    const r = data.import_result || { mode: "template", added: 0, skipped: [] };
    const summary = r.mode === "statement"
      ? `${r.queued}건이 "받은 결제 알림" 목록에 추가됨 - 아래에서 혜택을 골라주세요`
      : `${r.added}건 추가됨`;
    const lines = [`<div class="upload-log-item">${summary}</div>`];
    r.skipped.forEach((msg) => lines.push(`<div class="upload-log-item err">${escapeHtml(msg)}</div>`));
    importLog.innerHTML = lines.join("");
    importFile.value = "";
  });

  [cardModal, benefitModal, useModal, assignModal, webhookModal, perfModal].forEach((modal) => {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.classList.remove("open");
    });
  });

  async function init() {
    state = await api("/api/state");
    render();
  }
  init();
})();
