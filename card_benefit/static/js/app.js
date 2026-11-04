(() => {
  const cardList = document.getElementById("card-list");
  const emptyHint = document.getElementById("empty-hint");
  const inboxSection = document.getElementById("inbox-section");
  const inboxList = document.getElementById("inbox-list");
  const inboxCount = document.getElementById("inbox-count");
  const noBenefitSection = document.getElementById("no-benefit-section");
  const noBenefitList = document.getElementById("no-benefit-list");
  const noBenefitCount = document.getElementById("no-benefit-count");

  const CARD_COLORS = ["#a78bfa", "#ec4899", "#fab219", "#22c55e", "#3987e5", "#f97316"];

  let state = { cards: [], inbox: [], no_benefit: [], loans: [], recurring_payments: [], insurances: [], medical_expenses: [] };
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

  const CARD_NICKNAMES = [
    [/더모아|the more/i, "더모아"],
    [/discount plan|디스카운트/i, "디카플"],
    [/lady classic|레이디\s*클래식/i, "더레클"],
    [/네이버/i, "네이버"],
  ];

  function cardNickname(card) {
    if (card.short_name) return card.short_name;
    for (const [pattern, nick] of CARD_NICKNAMES) {
      if (pattern.test(card.name || "")) return nick;
    }
    return shortCardName(card.name);
  }

  function shortBenefitName(name) {
    return (name || "").replace(/\s*\([^)]*\)/g, "").trim();
  }

  function splitBenefitName(name) {
    const idx = (name || "").indexOf(" - ");
    if (idx < 0) return [name, ""];
    return [name.slice(0, idx).trim(), name.slice(idx + 3).trim()];
  }

  function statusClass(percent, overLimit, limitValue) {
    if (overLimit) return "bad";
    // limitValue가 0인데 무제한도 아니면(전월실적 미달로 이번 달 한도가 0원이 된
    // 경우) 아직 안 썼어도(percent 0) 초록으로 보이면 안 되니 빨강으로 표시.
    if (limitValue != null && limitValue <= 0) return "bad";
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

  // 가맹점명 뒤에 자동매칭용으로 붙는 "(배달의민족)"/"(토스)" 같은 안내는
  // 매칭에만 쓰고 화면에는 보여주지 않는다.
  function displayMerchant(m) {
    if (!m) return m;
    return m.replace(/\s*\((배달의민족|토스)\)\s*$/, "");
  }

  function render() {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    renderInbox();
    renderNoBenefit();
    renderCards();
    renderMonthlySummary();
    renderLoans();
    renderRecurring();
    renderInsurance();
    renderMedical();
    requestAnimationFrame(() => window.scrollTo(scrollX, scrollY));
  }

  // ---- 탭(카드/대출/고정지출/보험) ----
  const VIEW_NAMES = ["card", "loan", "recurring", "insurance"];
  let activeView = "card";
  function applyActiveView() {
    document.querySelectorAll(".view-tab").forEach((b) => b.classList.toggle("active", b.dataset.view === activeView));
    VIEW_NAMES.forEach((v) => {
      document.getElementById(`${v}-view`).style.display = activeView === v ? "" : "none";
      document.querySelectorAll(`.view-only-${v}`).forEach((el) => el.style.display = activeView === v ? "" : "none");
    });
  }
  document.querySelectorAll(".view-tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      activeView = btn.dataset.view;
      applyActiveView();
    })
  );

  // ---- 최상단 이번 달 적립/캐시백/혜택률 합계 ----
  function renderMonthlySummary() {
    const section = document.getElementById("monthly-summary");
    let earn = 0;
    let cashback = 0;
    let totalSpend = 0;
    state.cards.forEach((card) => {
      totalSpend += card.this_month_spend || 0;
      card.benefits.forEach((b) => {
        if (b.calc_mode === "change_under_1000") earn += b.used || 0;
        else if (b.calc_mode === "percent_discount") cashback += b.used || 0;
      });
    });
    const rate = totalSpend > 0 ? ((earn + cashback) / totalSpend * 100) : 0;
    section.style.display = state.cards.length ? "flex" : "none";
    const monthLabel = `${new Date().getMonth() + 1}월`;
    document.getElementById("summary-earn-label").textContent = `${monthLabel} 적립`;
    document.getElementById("summary-cashback-label").textContent = `${monthLabel} 할인·캐시백`;
    document.getElementById("summary-earn-value").textContent = `₩${fmt(earn)}`;
    document.getElementById("summary-cashback-value").textContent = `₩${fmt(cashback)}`;
    document.getElementById("summary-rate-value").textContent = `${rate.toFixed(1)}%`;

    const jumpRow = document.getElementById("summary-jump-row");
    jumpRow.innerHTML = state.cards.map((card, idx) => `
      <button class="jump-btn" data-card-id="${card.id}" style="border-color:${CARD_COLORS[idx % CARD_COLORS.length]};color:${CARD_COLORS[idx % CARD_COLORS.length]};">
        ${escapeHtml(cardNickname(card))}
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

  // 고정지출/보험 공용: 이번 달 것만 아니라 과거 달(엑셀로 몇 달치 한 번에
  // 올린 것 포함) 매칭 내역까지 "내역" 버튼으로 펼쳐서 보여준다.
  function matchHistoryTemplate(logKey, history) {
    const logsOpen = openLogPanels.has(logKey);
    const logsHtml = history.length
      ? history.map((h) => `
          <div class="log-item">
            <span>${h.occurred_at} · ${h.merchant ? escapeHtml(h.merchant) + " " : ""}${fmt(h.amount)}원</span>
            <button class="unlink-match" data-inbox-id="${h.id}">해제</button>
          </div>
        `).join("")
      : `<div class="log-item"><span>매칭된 결제 내역 없음</span></div>`;
    return `
      <div class="benefit-sub">
        <div class="benefit-buttons">
          <button class="btn small toggle-match-log" data-log-key="${logKey}">내역${history.length ? ` (${history.length})` : ""}</button>
        </div>
      </div>
      <div class="log-list ${logsOpen ? "open" : ""}" data-log-for="${logKey}">${logsHtml}</div>
    `;
  }

  function wireMatchHistory(el, logKey) {
    const toggleBtn = el.querySelector(`.toggle-match-log[data-log-key="${logKey}"]`);
    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        if (openLogPanels.has(logKey)) openLogPanels.delete(logKey);
        else openLogPanels.add(logKey);
        render();
      });
    }
    el.querySelectorAll(".unlink-match").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 매칭을 해제할까요?")) return;
        state = await api(`/api/match/${btn.dataset.inboxId}/unlink`, { method: "POST" });
        render();
      })
    );
  }

  // ---- 받은 알림 ----
  // ---- 여러 건 한 번에 선택/삭제(제외) 공용 - 몇 년치 카드내역을 한 번에
  // 업로드했을 때 필요없는 알림을 리스트별로 한 번에 치울 수 있게 한다 ----
  const bulkSelections = { inbox: new Set(), noBenefit: new Set(), medical: new Set() };

  function bulkBarHtml(key, actionLabel, itemIds) {
    if (!itemIds.length) return "";
    const selected = bulkSelections[key];
    for (const id of [...selected]) if (!itemIds.includes(id)) selected.delete(id);
    const allSelected = itemIds.every((id) => selected.has(id));
    return `
      <div class="bulk-bar">
        <label><input type="checkbox" class="bulk-select-all" ${allSelected ? "checked" : ""}> 전체선택</label>
        <span class="bulk-count">${selected.size}개 선택</span>
        <button class="btn secondary small bulk-action-btn" ${selected.size ? "" : "disabled"}>${actionLabel}</button>
      </div>
    `;
  }

  function wireBulkBar(container, key, itemIds, onAction) {
    const selected = bulkSelections[key];
    const allCb = container.querySelector(".bulk-select-all");
    if (allCb) {
      allCb.addEventListener("change", () => {
        if (allCb.checked) itemIds.forEach((id) => selected.add(id));
        else itemIds.forEach((id) => selected.delete(id));
        render();
      });
    }
    container.querySelectorAll(".bulk-item-check").forEach((cb) => {
      const id = Number(cb.dataset.id);
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(id);
        else selected.delete(id);
        render();
      });
    });
    const actionBtn = container.querySelector(".bulk-action-btn");
    if (actionBtn) {
      actionBtn.addEventListener("click", async () => {
        if (!selected.size) return;
        const proceeded = await onAction([...selected]);
        if (proceeded) {
          selected.clear();
          render();
        }
      });
    }
  }

  function renderInbox() {
    const items = state.inbox;
    const ids = items.map((it) => it.id);
    inboxSection.style.display = items.length ? "block" : "none";
    inboxCount.textContent = items.length ? `${items.length}건` : "";
    inboxList.innerHTML = bulkBarHtml("inbox", "선택 삭제", ids) + items.map((it) => `
      <div class="inbox-item" data-id="${it.id}">
        <input type="checkbox" class="bulk-item-check" data-id="${it.id}" ${bulkSelections.inbox.has(it.id) ? "checked" : ""}>
        <div class="inbox-item-info">
          <div class="inbox-item-amount">${it.amount != null ? fmt(it.amount) + "원" : "(금액 미확인)"}</div>
          <div class="inbox-item-sub">${it.occurred_at}${it.matched_card_name ? " · " + escapeHtml(shortCardName(it.matched_card_name)) : ""}${it.merchant ? " · " + escapeHtml(displayMerchant(it.merchant)) : ""}</div>
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
    wireBulkBar(inboxList, "inbox", ids, async (selectedIds) => {
      if (!confirm(`${selectedIds.length}건을 완전히 삭제할까요? 되돌릴 수 없습니다.`)) return false;
      state = await api("/api/inbox/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: selectedIds }),
      });
      return true;
    });
  }

  // ---- 혜택 없음 처리 내역 ----
  function renderNoBenefit() {
    const items = state.no_benefit || [];
    const ids = items.map((it) => it.id);
    noBenefitSection.style.display = items.length ? "block" : "none";
    noBenefitCount.textContent = items.length ? `${items.length}건` : "";
    noBenefitList.style.display = noBenefitOpen ? "flex" : "none";
    noBenefitList.innerHTML = bulkBarHtml("noBenefit", "선택 삭제", ids) + items.map((it) => `
      <div class="inbox-item" data-id="${it.id}">
        <input type="checkbox" class="bulk-item-check" data-id="${it.id}" ${bulkSelections.noBenefit.has(it.id) ? "checked" : ""}>
        <div class="inbox-item-info">
          <div class="inbox-item-amount">${it.amount != null ? fmt(it.amount) + "원" : "(금액 미확인)"}</div>
          <div class="inbox-item-sub">${it.occurred_at}${it.card_name ? " · " + escapeHtml(shortCardName(it.card_name)) : ""}${it.merchant ? " · " + escapeHtml(displayMerchant(it.merchant)) : ""}</div>
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
    wireBulkBar(noBenefitList, "noBenefit", ids, async (selectedIds) => {
      if (!confirm(`${selectedIds.length}건을 완전히 삭제할까요? 되돌릴 수 없습니다.`)) return false;
      state = await api("/api/inbox/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: selectedIds }),
      });
      return true;
    });
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
      el.innerHTML = cardTemplate(card, idx === 0, idx === state.cards.length - 1);
      el.querySelector(".card-title").style.color = CARD_COLORS[idx % CARD_COLORS.length];
      cardList.appendChild(el);
      wireCard(el, card, state.cards, idx);
    });
  }

  function cardTemplate(card, isFirst, isLast) {
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
          <div class="reorder-buttons">
            <button class="move-card-up" data-id="${card.id}" ${isFirst ? "disabled" : ""} title="위로">▲</button>
            <button class="move-card-down" data-id="${card.id}" ${isLast ? "disabled" : ""} title="아래로">▼</button>
          </div>
          <span class="card-title-scroll"><span class="card-title">${escapeHtml(shortCardName(card.name))}</span></span>
          ${card.last4 ? `<span class="loan-type-badge">${escapeHtml(card.last4)}</span>` : ""}
          ${card.perf_threshold ? `<span class="perf-badge ${card.perf_met ? "met" : "unmet"}">실적<br>${card.perf_met ? "충족" : "부족"}</span>` : ""}
        </div>
        <div class="card-actions">
          <button class="icon-btn edit-card" data-id="${card.id}" title="카드 수정">✏️</button>
          <button class="icon-btn delete-card" data-id="${card.id}" title="카드 삭제">🗑️</button>
        </div>
      </div>
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
          const merchantLabel = l.merchant ? `<strong>${escapeHtml(displayMerchant(l.merchant))}</strong> · ` : "";
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
    const cls = statusClass(b.percent, b.over_limit, b.limit_value);
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
      ? `<div class="benefit-remaining remaining-good">당월 ${fmt(b.used)}${unit} 적립</div>`
      : (() => {
          const cls = statusClass(b.percent, b.over_limit, b.limit_value);
          const remainingText = b.over_limit
            ? `초과 ${fmt(Math.abs(b.remaining))}${unit}`
            : `잔여 ${fmt(b.remaining)}${unit}`;
          return `<div class="benefit-remaining remaining-${cls}">${remainingText}</div>`;
        })();

    const progressHtml = b.unlimited
      ? ""
      : (() => {
          const cls = statusClass(b.percent, b.over_limit, b.limit_value);
          return `<div class="progress-track"><div class="progress-fill ${cls}" style="width:${b.percent}%"></div></div>`;
        })();

    const subText = b.unlimited
      ? ``
      : `한도 ${fmt(b.limit_value)}${unit} 중 ${fmt(b.used)}${unit} 사용`;

    const categoryUsageHtml = (b.category_usage && b.category_usage.length)
      ? `<div class="category-usage">${b.category_usage.map((c) => {
          let text;
          if (c.monthly_point_cap) text = `${escapeHtml(c.label)} ₩${fmt(c.month_earned)}/${fmt(c.monthly_point_cap)}`;
          else if (c.monthly_limit) text = `${escapeHtml(c.label)} 월${fmt(c.count)}/${fmt(c.monthly_limit)}`;
          else if (c.daily_limit) text = `${escapeHtml(c.label)} 일${fmt(c.today_count)}/${fmt(c.daily_limit)}`;
          else text = `${escapeHtml(c.label)} ${fmt(c.count)}`;
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

  function wireCard(el, card, allCards, idx) {
    el.querySelector(".edit-card").addEventListener("click", () => openCardModal(card));
    const swapCardWith = async (otherIdx) => {
      const other = allCards[otherIdx];
      const a = card.sort_order, b = other.sort_order;
      state = await api(`/api/cards/${card.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sort_order: b }),
      });
      state = await api(`/api/cards/${other.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sort_order: a }),
      });
      render();
    };
    const moveUpBtn = el.querySelector(".move-card-up");
    if (moveUpBtn && !moveUpBtn.disabled) moveUpBtn.addEventListener("click", () => swapCardWith(idx - 1));
    const moveDownBtn = el.querySelector(".move-card-down");
    if (moveDownBtn && !moveDownBtn.disabled) moveDownBtn.addEventListener("click", () => swapCardWith(idx + 1));
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

  // ---- 대출 목록 ----
  let loanSort = "day";
  const loanSortSelect = document.getElementById("loan-sort-select");
  loanSortSelect.addEventListener("change", () => {
    loanSort = loanSortSelect.value;
    render();
  });

  function sortedLoans() {
    const loans = state.loans.slice();
    if (loanSort === "balance") {
      loans.sort((a, b) => (b.current_balance || 0) - (a.current_balance || 0));
    } else if (loanSort === "rate") {
      loans.sort((a, b) => (b.interest_rate || 0) - (a.interest_rate || 0));
    } else if (loanSort === "manual") {
      loans.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    } else if (loanSort === "day") {
      loans.sort((a, b) => {
        if (!a.payment_day && !b.payment_day) return 0;
        if (!a.payment_day) return 1;
        if (!b.payment_day) return -1;
        return a.payment_day - b.payment_day;
      });
    } else {
      // 만기임박순(기본): 만기일 없는 건 맨 뒤로
      loans.sort((a, b) => {
        if (!a.end_date && !b.end_date) return 0;
        if (!a.end_date) return 1;
        if (!b.end_date) return -1;
        return a.end_date.localeCompare(b.end_date);
      });
    }
    return loans;
  }

  function renderLoans() {
    const loanList = document.getElementById("loan-list");
    const emptyLoanHint = document.getElementById("empty-loan-hint");
    loanList.querySelectorAll(".card").forEach((el) => el.remove());
    emptyLoanHint.style.display = state.loans.length ? "none" : "block";

    document.getElementById("loan-total-balance-value").textContent =
      `₩${fmt(state.loans.reduce((sum, l) => sum + (l.current_balance || 0), 0))}`;
    document.getElementById("loan-total-monthly-value").textContent =
      `₩${fmt(state.loans.filter((l) => l.due_this_month).reduce((sum, l) => sum + (l.monthly_payment || 0), 0))}`;
    document.getElementById("loan-unpaid-total").textContent =
      `미체크 ₩${fmt(state.loans.filter((l) => l.due_this_month && !l.paid_this_month).reduce((sum, l) => sum + (l.monthly_payment || 0), 0))}`;

    const loans = sortedLoans();
    loans.forEach((loan, idx) => {
      const el = document.createElement("div");
      el.className = "card";
      el.id = `loan-${loan.id}`;
      const isManual = loanSort === "manual";
      el.innerHTML = loanTemplate(loan, isManual, idx === 0, idx === loans.length - 1);
      loanList.appendChild(el);
      wireLoan(el, loan, isManual ? loans : null, idx);
    });
  }

  function shortRepayType(t) {
    return t ? t.replace(/상환$/, "") : t;
  }

  function loanTemplate(loan, showReorder, isFirst, isLast) {
    const logKey = `loan-${loan.id}`;
    const logsOpen = openLogPanels.has(logKey);
    const percent = loan.percent_paid != null ? loan.percent_paid : 0;
    const isBullet = (loan.repayment_type || "").includes("만기일시");
    const ddayText = loan.days_left != null
      ? (loan.days_left >= 0 ? ` (D-${loan.days_left})` : ` (${fmt(Math.abs(loan.days_left))}일 지남)`)
      : "";
    const balancePercentLabel = (!isBullet && loan.principal > 0) ? `${percent}% 상환` : "";
    const balanceValue = `${fmt(loan.current_balance)}/${fmt(loan.principal)}원`;
    const infoLine = [
      loan.interest_rate ? `${loan.interest_rate}%` : "",
      loan.repayment_type ? escapeHtml(shortRepayType(loan.repayment_type)) : "",
      loan.end_date ? `만기 ${loan.end_date}${ddayText}` : "",
      loan.withdrawal_account ? escapeHtml(loan.withdrawal_account) : "",
    ].filter(Boolean).join(" / ");
    const dueText = loan.due_this_month
      ? (loan.monthly_payment ? fmt(loan.monthly_payment) + "원" : "미입력")
      : "당월 해당 없음";
    const quarterMonths = (loan.payment_months || "").split(",").map((m) => m.trim()).filter(Boolean);
    const isMaturingSoon = loan.days_left != null && loan.days_left <= 30;

    const logsHtml = loan.payments.length
      ? loan.payments.map((p) => `
          <div class="log-item">
            <span>${p.paid_at} · ${p.amount >= 0 ? "상환" : "증가"} ${fmt(Math.abs(p.amount))}원 → 잔액 ${fmt(p.balance_after)}원${p.memo ? " · " + escapeHtml(p.memo) : ""}</span>
            <button class="delete-loan-payment" data-id="${p.id}">삭제</button>
          </div>
        `).join("")
      : `<div class="log-item"><span>상환 기록 없음</span></div>`;

    const reorderHtml = showReorder ? `
      <div class="reorder-buttons">
        <button class="move-loan-up" data-id="${loan.id}" ${isFirst ? "disabled" : ""} title="위로">▲</button>
        <button class="move-loan-down" data-id="${loan.id}" ${isLast ? "disabled" : ""} title="아래로">▼</button>
      </div>
    ` : "";

    return `
      <div class="card-head">
        <div class="card-head-left">
          ${loan.payment_day ? `<div class="loan-day-badge" title="매월 상환일">${loan.payment_day}</div>` : ""}
          ${reorderHtml}
          <div class="card-title-row">
            <span class="card-title-scroll"><span class="card-title" style="${isMaturingSoon ? "color:var(--critical);" : ""}">${escapeHtml(loan.name)}</span></span>
            ${loan.loan_type ? `<span class="loan-type-badge">${escapeHtml(loan.loan_type)}</span>` : ""}
            ${quarterMonths.length ? `<span class="loan-type-badge" style="color:var(--accent-purple);background:rgba(179,71,255,0.14);">분기납(${quarterMonths.join(",")}월)</span>` : ""}
          </div>
        </div>
      </div>
      <div class="benefit-top">
        <span class="benefit-name">
          ${loan.due_this_month ? `<input type="checkbox" class="loan-paid-check" data-id="${loan.id}" ${loan.paid_this_month ? "checked" : ""}>` : ""}
          당월 상환 금액
        </span>
        <span class="benefit-remaining amount-accent">${dueText}</span>
      </div>
      <div class="benefit-top">
        <span class="benefit-name" style="${balancePercentLabel ? "color:var(--neon-green);" : ""}">${balancePercentLabel}</span>
        <span class="benefit-remaining">${balanceValue}</span>
      </div>
      ${(!isBullet && loan.principal > 0) ? `<div class="progress-track"><div class="progress-fill" style="width:${percent}%;background:var(--neon-green);"></div></div>` : ""}
      <div class="perf-row-sub">${infoLine}</div>
      ${memoToggleTemplate(logKey, loan.memo)}
      <div class="benefit-sub">
        <div class="benefit-buttons">
          <button class="icon-btn add-loan-payment" data-id="${loan.id}" title="상환 기록">➕</button>
          <button class="btn small toggle-loan-log" data-id="${loan.id}">내역</button>
        </div>
        <div class="benefit-buttons">
          <button class="icon-btn edit-loan" data-id="${loan.id}" title="대출 수정">✏️</button>
          <button class="icon-btn delete-loan" data-id="${loan.id}" title="대출 삭제">🗑️</button>
        </div>
      </div>
      <div class="log-list ${logsOpen ? "open" : ""}" data-log-for="${logKey}">${logsHtml}</div>
    `;
  }

  function wireLoan(el, loan, manualOrderList, idx) {
    el.querySelector(".edit-loan").addEventListener("click", () => openLoanModal(loan));
    el.querySelector(".delete-loan").addEventListener("click", async () => {
      if (!confirm(`"${loan.name}" 대출을 삭제할까요? 상환 기록도 모두 삭제됩니다.`)) return;
      state = await api(`/api/loans/${loan.id}`, { method: "DELETE" });
      render();
    });
    el.querySelector(".add-loan-payment").addEventListener("click", () => openLoanPaymentModal(loan));
    const paidCheck = el.querySelector(".loan-paid-check");
    if (paidCheck) {
      paidCheck.addEventListener("change", async () => {
        state = await api(`/api/loans/${loan.id}/paid-check`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ checked: paidCheck.checked }),
        });
        render();
      });
    }
    el.querySelector(".toggle-loan-log").addEventListener("click", () => {
      const key = `loan-${loan.id}`;
      if (openLogPanels.has(key)) openLogPanels.delete(key);
      else openLogPanels.add(key);
      render();
    });
    el.querySelectorAll(".memo-toggle").forEach((btn) =>
      btn.addEventListener("click", () => {
        const key = btn.dataset.memoKey;
        if (openMemoPanels.has(key)) openMemoPanels.delete(key);
        else openMemoPanels.add(key);
        render();
      })
    );
    el.querySelectorAll(".delete-loan-payment").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 상환 기록을 삭제할까요?")) return;
        state = await api(`/api/loan-payments/${btn.dataset.id}`, { method: "DELETE" });
        render();
      })
    );
    if (manualOrderList) {
      const swapWith = async (otherIdx) => {
        const other = manualOrderList[otherIdx];
        const a = loan.sort_order, b = other.sort_order;
        state = await api(`/api/loans/${loan.id}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sort_order: b }),
        });
        state = await api(`/api/loans/${other.id}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sort_order: a }),
        });
        render();
      };
      const upBtn = el.querySelector(".move-loan-up");
      if (upBtn && !upBtn.disabled) upBtn.addEventListener("click", () => swapWith(idx - 1));
      const downBtn = el.querySelector(".move-loan-down");
      if (downBtn && !downBtn.disabled) downBtn.addEventListener("click", () => swapWith(idx + 1));
    }
  }

  // ---- 대출 추가/수정 모달 ----
  const loanModal = document.getElementById("loan-modal");
  let editingLoanId = null;

  const withdrawalSelect = document.getElementById("loan-withdrawal-account-select");
  const withdrawalInput = document.getElementById("loan-withdrawal-account");
  const CUSTOM_WITHDRAWAL = "__custom__";

  withdrawalSelect.addEventListener("change", () => {
    if (withdrawalSelect.value === CUSTOM_WITHDRAWAL) {
      withdrawalInput.style.display = "";
      withdrawalInput.value = "";
      withdrawalInput.focus();
    } else {
      withdrawalInput.style.display = "none";
      withdrawalInput.value = "";
    }
  });

  function buildWithdrawalOptions() {
    const seen = new Set();
    const result = [];
    for (const l of state.loans) {
      const v = (l.withdrawal_account || "").trim();
      if (!v || seen.has(v)) continue;
      seen.add(v);
      result.push(v);
    }
    return result;
  }

  function openLoanModal(loan) {
    editingLoanId = loan ? loan.id : null;
    document.getElementById("loan-modal-title").textContent = loan ? "대출 수정" : "대출 추가";
    document.getElementById("loan-name").value = loan ? loan.name : "";
    document.getElementById("loan-type").value = loan ? loan.loan_type : "";
    document.getElementById("loan-principal").value = loan ? loan.principal : "";
    document.getElementById("loan-balance").value = loan ? loan.current_balance : "";
    document.getElementById("loan-rate").value = loan && loan.interest_rate ? loan.interest_rate : "";
    document.getElementById("loan-repay-type").value = loan ? loan.repayment_type : "";
    document.getElementById("loan-start-date").value = loan ? loan.start_date : "";
    document.getElementById("loan-end-date").value = loan ? loan.end_date : "";
    document.getElementById("loan-payment-day").value = loan && loan.payment_day ? loan.payment_day : "";
    document.getElementById("loan-monthly-payment").value = loan && loan.monthly_payment ? loan.monthly_payment : "";
    document.getElementById("loan-payment-months").value = loan ? loan.payment_months : "";

    const withdrawalOptions = buildWithdrawalOptions();
    const currentWithdrawal = loan ? (loan.withdrawal_account || "") : "";
    withdrawalSelect.innerHTML = [
      `<option value="">선택 안 함</option>`,
      ...withdrawalOptions.map((o) => `<option value="${escapeHtml(o)}">${escapeHtml(o)}</option>`),
      `<option value="${CUSTOM_WITHDRAWAL}">직접 입력</option>`,
    ].join("");
    if (currentWithdrawal && !withdrawalOptions.includes(currentWithdrawal)) {
      withdrawalSelect.value = CUSTOM_WITHDRAWAL;
      withdrawalInput.style.display = "";
      withdrawalInput.value = currentWithdrawal;
    } else {
      withdrawalSelect.value = currentWithdrawal;
      withdrawalInput.style.display = "none";
      withdrawalInput.value = "";
    }

    document.getElementById("loan-memo").value = loan ? loan.memo : "";
    loanModal.classList.add("open");
  }
  document.getElementById("btn-add-loan").addEventListener("click", () => openLoanModal(null));
  document.getElementById("loan-cancel").addEventListener("click", () => loanModal.classList.remove("open"));
  document.getElementById("loan-save").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("loan-name").value.trim(),
      loan_type: document.getElementById("loan-type").value.trim(),
      principal: document.getElementById("loan-principal").value || 0,
      current_balance: document.getElementById("loan-balance").value,
      interest_rate: document.getElementById("loan-rate").value || 0,
      repayment_type: document.getElementById("loan-repay-type").value.trim(),
      start_date: document.getElementById("loan-start-date").value,
      end_date: document.getElementById("loan-end-date").value,
      payment_day: document.getElementById("loan-payment-day").value || 0,
      monthly_payment: document.getElementById("loan-monthly-payment").value || 0,
      payment_months: document.getElementById("loan-payment-months").value.trim(),
      withdrawal_account: withdrawalSelect.value === CUSTOM_WITHDRAWAL ? withdrawalInput.value.trim() : withdrawalSelect.value,
      memo: document.getElementById("loan-memo").value.trim(),
    };
    if (!payload.name) { alert("대출 이름을 입력하세요."); return; }
    const url = editingLoanId ? `/api/loans/${editingLoanId}` : "/api/loans";
    const method = editingLoanId ? "PUT" : "POST";
    state = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    loanModal.classList.remove("open");
    render();
  });

  // ---- 대출 상환 기록 모달 ----
  const loanPaymentModal = document.getElementById("loan-payment-modal");
  let payingLoan = null;

  function openLoanPaymentModal(loan) {
    payingLoan = loan;
    document.getElementById("loan-payment-current-balance-label").textContent = `현재 잔액: ${fmt(loan.current_balance)}원`;
    document.getElementById("loan-payment-balance").value = "";
    document.getElementById("loan-payment-date").value = new Date().toISOString().slice(0, 10);
    document.getElementById("loan-payment-memo").value = "";
    loanPaymentModal.classList.add("open");
  }
  document.getElementById("loan-payment-cancel").addEventListener("click", () => loanPaymentModal.classList.remove("open"));
  document.getElementById("loan-payment-save").addEventListener("click", async () => {
    const balance = document.getElementById("loan-payment-balance").value;
    if (balance === "" || Number(balance) < 0) { alert("상환 후 남은 잔액을 입력하세요."); return; }
    const payload = {
      balance_after: balance,
      paid_at: document.getElementById("loan-payment-date").value,
      memo: document.getElementById("loan-payment-memo").value.trim(),
    };
    state = await api(`/api/loans/${payingLoan.id}/payments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    loanPaymentModal.classList.remove("open");
    render();
  });

  // ---- 고정지출(구독/자동이체) 목록 ----
  let recurringSort = "day";
  const recurringSortSelect = document.getElementById("recurring-sort-select");
  recurringSortSelect.addEventListener("change", () => {
    recurringSort = recurringSortSelect.value;
    render();
  });

  function sortedRecurring() {
    const items = state.recurring_payments.slice();
    if (recurringSort === "amount") {
      items.sort((a, b) => (b.amount || 0) - (a.amount || 0));
    } else {
      items.sort((a, b) => {
        if (!a.payment_day && !b.payment_day) return 0;
        if (!a.payment_day) return 1;
        if (!b.payment_day) return -1;
        return a.payment_day - b.payment_day;
      });
    }
    return items;
  }

  function fmt100(n) {
    return fmt(Math.floor(Number(n) / 100) * 100);
  }

  function renderRecurring() {
    const list = document.getElementById("recurring-list");
    const emptyHint = document.getElementById("empty-recurring-hint");
    list.querySelectorAll(".card").forEach((el) => el.remove());
    emptyHint.style.display = state.recurring_payments.length ? "none" : "block";

    const total = state.recurring_payments.filter((r) => r.active).reduce((sum, r) => sum + (r.amount || 0), 0);
    const loanTotal = state.loans.filter((l) => l.due_this_month).reduce((sum, l) => sum + (l.monthly_payment || 0), 0);
    document.getElementById("recurring-total-value").textContent = `₩${fmt(total)}`;
    document.getElementById("recurring-grand-total-value").textContent = `₩${fmt(total + loanTotal)}`;

    document.getElementById("recurring-unmatched-total").textContent =
      `미확인 ₩${fmt(state.recurring_payments.filter((r) => r.active && !r.matched_this_month).reduce((sum, r) => sum + (r.amount || 0), 0))}`;

    sortedRecurring().forEach((item) => {
      const el = document.createElement("div");
      el.className = "card";
      el.id = `recurring-${item.id}`;
      el.innerHTML = recurringTemplate(item);
      list.appendChild(el);
      wireRecurring(el, item);
    });
  }

  function recurringTemplate(item) {
    const isVariable = item.category === "주거/관리비";
    const amountText = isVariable ? fmt100(item.amount) : fmt(item.amount);
    return `
      <div class="card-head">
        <div class="card-head-left">
          ${item.payment_day ? `<div class="loan-day-badge" title="매월 결제일">${item.payment_day}</div>` : ""}
          <div class="card-text-stack">
            <div class="card-text-row">
              <span class="card-title-scroll"><span class="card-title" style="${item.active ? "" : "opacity:.5;text-decoration:line-through;"}">${escapeHtml(item.name)}</span></span>
              <span class="card-head-right">
                ${item.category ? `<span class="loan-type-badge">${escapeHtml(item.category)}</span>` : ""}
                ${item.active ? "" : `<span class="loan-type-badge" style="color:var(--text-muted);background:var(--surface-3);">중단</span>`}
                ${item.active ? `<span class="match-badge" data-id="${item.id}" title="${item.matched_this_month ? `확인됨: ${item.matched_merchant ? escapeHtml(item.matched_merchant) + " " : ""}${fmt(item.matched_amount)}원` : "이번 달 결제 확인 안 됨 - 눌러서 매칭"}">${item.matched_this_month ? "✅" : "❓"}</span>` : ""}
                <span class="benefit-remaining amount-accent">${amountText}원</span>
              </span>
            </div>
            <div class="card-text-row">
              <span class="card-method-dim">${item.payment_method ? escapeHtml(item.payment_method) : "결제수단 미입력"}</span>
              <span class="card-head-right">
                <button class="icon-btn edit-recurring" data-id="${item.id}" title="수정">✏️</button>
                <button class="icon-btn delete-recurring" data-id="${item.id}" title="삭제">🗑️</button>
              </span>
            </div>
          </div>
        </div>
      </div>
      ${memoToggleTemplate(`recurring-${item.id}`, item.memo)}
      ${matchHistoryTemplate(`recurring-${item.id}`, item.match_history)}
    `;
  }

  function wireRecurring(el, item) {
    el.querySelector(".edit-recurring").addEventListener("click", () => openRecurringModal(item));
    el.querySelector(".delete-recurring").addEventListener("click", async () => {
      if (!confirm(`"${item.name}"을(를) 삭제할까요?`)) return;
      state = await api(`/api/recurring/${item.id}`, { method: "DELETE" });
      render();
    });
    const matchBadge = el.querySelector(".match-badge");
    if (matchBadge) matchBadge.addEventListener("click", () => openMatchModal("recurring", item, item.name));
    wireMatchHistory(el, `recurring-${item.id}`);
    el.querySelectorAll(".memo-toggle").forEach((btn) =>
      btn.addEventListener("click", () => {
        const key = btn.dataset.memoKey;
        if (openMemoPanels.has(key)) openMemoPanels.delete(key);
        else openMemoPanels.add(key);
        render();
      })
    );
  }

  // ---- 고정지출/보험 - 받은 알림 매칭 확인(공용) ----
  const matchModal = document.getElementById("match-modal");
  document.getElementById("match-modal-cancel").addEventListener("click", () => {
    matchModal.classList.remove("open");
  });
  document.getElementById("btn-recurring-automatch").addEventListener("click", async () => {
    const result = await api("/api/recurring/auto-match", { method: "POST" });
    state = result;
    render();
    alert(`${result.auto_match_count || 0}건 자동 매칭됐습니다.`);
  });

  async function openMatchModal(entityType, item, label) {
    document.getElementById("match-modal-title").textContent = `"${label}" 이번 달 결제 확인`;
    const currentEl = document.getElementById("match-modal-current");
    const listEl = document.getElementById("match-modal-list");
    listEl.innerHTML = "";
    matchModal.classList.add("open");

    if (item.matched_this_month) {
      currentEl.textContent = `${item.matched_merchant ? item.matched_merchant + " · " : ""}${fmt(item.matched_amount)}원 · ${item.matched_occurred_at || ""} 으로 확인됨`;
      listEl.innerHTML = `<button class="btn secondary" id="match-modal-undo">매칭 취소</button>`;
      document.getElementById("match-modal-undo").addEventListener("click", async () => {
        state = await api(`/api/${entityType}/${item.id}/unmatch`, { method: "POST" });
        matchModal.classList.remove("open");
        render();
      });
      return;
    }

    currentEl.textContent = "아직 이번 달 결제 확인 안 됨. 아래에서 해당하는 받은 알림을 고르세요.";
    listEl.innerHTML = `<p class="hint">불러오는 중...</p>`;
    const candidates = await api(`/api/${entityType}/${item.id}/match-candidates`);
    if (!candidates.length) {
      listEl.innerHTML = `<p class="hint">이번 달 들어온 알림 중 후보가 없습니다.</p>`;
      return;
    }
    const statusLabel = { pending: "", assigned: " (혜택배정됨)", no_benefit: " (혜택없음 처리)" };
    listEl.innerHTML = candidates.map((c) => `
      <button class="btn secondary match-candidate" data-id="${c.id}">
        ${fmt(c.amount)}원${c.merchant ? " · " + escapeHtml(c.merchant) : ""}${c.last4 ? " · " + escapeHtml(c.last4) : ""}${statusLabel[c.status] || ""}<br>
        <span style="font-size:11px;color:var(--text-muted);">${c.occurred_at}</span>
      </button>
    `).join("");
    listEl.querySelectorAll(".match-candidate").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state = await api(`/api/${entityType}/${item.id}/confirm-match`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ inbox_item_id: Number(btn.dataset.id) }),
        });
        matchModal.classList.remove("open");
        render();
      });
    });
  }

  // ---- 고정지출 추가/수정 모달 ----
  const recurringModal = document.getElementById("recurring-modal");
  let editingRecurringId = null;

  const methodSelect = document.getElementById("recurring-method-select");
  const methodInput = document.getElementById("recurring-method");
  const CUSTOM_METHOD = "__custom__";

  methodSelect.addEventListener("change", () => {
    if (methodSelect.value === CUSTOM_METHOD) {
      methodInput.style.display = "";
      methodInput.value = "";
      methodInput.focus();
    } else {
      methodInput.style.display = "none";
      methodInput.value = "";
    }
  });

  function normMethod(s) {
    return (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  }
  function methodBaseName(s) {
    return normMethod((s || "").replace(/\([^)]*\)\s*$/, ""));
  }
  // 결제수단 선택지: 등록된 카드는 {value:"card:<id>", label:"이름(뒷자리)"}로 만들어서
  // 카드탭의 카드를 그대로 가리키게 한다(카드탭에서 이름 바꾸면 자동으로 같이 바뀜).
  // "현금"처럼 카드가 아닌 기존 입력값은 텍스트 그대로 옵션이 된다.
  function buildMethodOptions(sourceList) {
    const cardOptions = state.cards.map((c) => ({
      value: `card:${c.id}`,
      label: `${c.name}${c.last4 ? `(${c.last4})` : ""}`,
    }));
    const cardBases = cardOptions.map((o) => methodBaseName(o.label));
    const usedOptions = (sourceList || state.recurring_payments)
      .map((r) => (r.payment_method || "").trim())
      .filter((o) => {
        if (!o) return false;
        const ob = methodBaseName(o);
        if (ob.length < 2) return true;
        // 카드탭 항목과 "신한" 접두어 유무, 뒷자리 유무만 다르고 실질적으로 같은 카드를
        // 가리키는 값은(부분 문자열로 포함되면) 중복으로 보고 제외
        return !cardBases.some((cb) => cb.includes(ob) || ob.includes(cb));
      })
      .map((label) => ({ value: label, label }));
    const seen = new Set();
    const result = [];
    for (const o of [...cardOptions, ...usedOptions]) {
      const key = normMethod(o.label);
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(o);
    }
    return result;
  }

  function openRecurringModal(item) {
    editingRecurringId = item ? item.id : null;
    document.getElementById("recurring-modal-title").textContent = item ? "고정지출 수정" : "고정지출 추가";
    document.getElementById("recurring-name").value = item ? item.name : "";
    document.getElementById("recurring-category").value = item ? item.category : "";
    document.getElementById("recurring-amount").value = item ? item.amount : "";

    const options = buildMethodOptions();
    const current = item ? (item.payment_method || "") : "";
    const cardOption = item && item.card_id ? options.find((o) => o.value === `card:${item.card_id}`) : null;
    const textMatched = !cardOption && options.find((o) => o.value !== undefined && normMethod(o.label) === normMethod(current));
    const matched = cardOption || textMatched;
    methodSelect.innerHTML = [
      `<option value="">선택 안 함</option>`,
      ...options.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`),
      `<option value="${CUSTOM_METHOD}">직접 입력</option>`,
    ].join("");
    if (current && !matched) {
      methodSelect.value = CUSTOM_METHOD;
      methodInput.style.display = "";
      methodInput.value = current;
    } else {
      methodSelect.value = matched ? matched.value : "";
      methodInput.style.display = "none";
      methodInput.value = "";
    }

    document.getElementById("recurring-day").value = item && item.payment_day ? item.payment_day : "";
    document.getElementById("recurring-active").checked = item ? !!item.active : true;
    document.getElementById("recurring-memo").value = item ? item.memo : "";
    recurringModal.classList.add("open");
  }
  document.getElementById("btn-add-recurring").addEventListener("click", () => openRecurringModal(null));
  document.getElementById("recurring-cancel").addEventListener("click", () => recurringModal.classList.remove("open"));
  document.getElementById("recurring-save").addEventListener("click", async () => {
    const isCardChoice = methodSelect.value.startsWith("card:");
    const payload = {
      name: document.getElementById("recurring-name").value.trim(),
      category: document.getElementById("recurring-category").value.trim(),
      amount: document.getElementById("recurring-amount").value || 0,
      card_id: isCardChoice ? Number(methodSelect.value.slice(5)) : null,
      payment_method: isCardChoice ? "" : (methodSelect.value === CUSTOM_METHOD ? methodInput.value.trim() : methodSelect.value),
      payment_day: document.getElementById("recurring-day").value || 0,
      active: document.getElementById("recurring-active").checked,
      memo: document.getElementById("recurring-memo").value.trim(),
    };
    if (!payload.name) { alert("이름을 입력하세요."); return; }
    const url = editingRecurringId ? `/api/recurring/${editingRecurringId}` : "/api/recurring";
    const method = editingRecurringId ? "PUT" : "POST";
    state = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    recurringModal.classList.remove("open");
    render();
  });

  // ---- 보험 목록 ----
  let insuranceSort = "day";
  const insuranceSortSelect = document.getElementById("insurance-sort-select");
  insuranceSortSelect.addEventListener("change", () => {
    insuranceSort = insuranceSortSelect.value;
    render();
  });

  function sortedInsurances() {
    const items = state.insurances.slice();
    if (insuranceSort === "amount") {
      items.sort((a, b) => (b.monthly_premium || 0) - (a.monthly_premium || 0));
    } else {
      items.sort((a, b) => {
        if (!a.payment_day && !b.payment_day) return 0;
        if (!a.payment_day) return 1;
        if (!b.payment_day) return -1;
        return a.payment_day - b.payment_day;
      });
    }
    return items;
  }

  function renderInsurance() {
    const list = document.getElementById("insurance-list");
    const emptyHint = document.getElementById("empty-insurance-hint");
    list.querySelectorAll(".card").forEach((el) => el.remove());
    emptyHint.style.display = state.insurances.length ? "none" : "block";

    const total = state.insurances.filter((r) => r.active).reduce((sum, r) => sum + (r.monthly_premium || 0), 0);
    document.getElementById("insurance-total-value").textContent = `₩${fmt(total)}`;

    document.getElementById("insurance-unmatched-total").textContent =
      `미확인 ₩${fmt(state.insurances.filter((r) => r.active && !r.matched_this_month).reduce((sum, r) => sum + (r.monthly_premium || 0), 0))}`;

    sortedInsurances().forEach((item) => {
      const el = document.createElement("div");
      el.className = "card";
      el.id = `insurance-${item.id}`;
      el.innerHTML = insuranceTemplate(item);
      list.appendChild(el);
      wireInsurance(el, item);
    });
  }

  function insuranceTemplate(item) {
    const infoLine = [
      item.insured_name && item.insured_name !== "본인" ? `피보험자 ${item.insured_name}` : "",
      item.start_date ? `가입 ${item.start_date}` : "",
      item.payment_end_date ? `납입완료 ${item.payment_end_date}` : "",
      item.maturity_date ? `만기 ${item.maturity_date}` : "",
    ].filter(Boolean).join(" / ");
    return `
      <div class="card-head">
        <div class="card-head-left">
          ${item.payment_day ? `<div class="loan-day-badge" title="매월 결제일">${item.payment_day}</div>` : ""}
          <div class="card-text-stack">
            <div class="card-text-row">
              <span class="card-title-scroll"><span class="card-title" style="${item.active ? "" : "opacity:.5;text-decoration:line-through;"}">${escapeHtml(item.product_name)}</span></span>
              <span class="card-head-right">
                ${item.active ? "" : `<span class="loan-type-badge" style="color:var(--text-muted);background:var(--surface-3);">해지</span>`}
                ${item.active ? `<span class="match-badge" data-id="${item.id}" title="${item.matched_this_month ? `확인됨: ${item.matched_merchant ? escapeHtml(item.matched_merchant) + " " : ""}${fmt(item.matched_amount)}원` : "이번 달 결제 확인 안 됨 - 눌러서 매칭"}">${item.matched_this_month ? "✅" : "❓"}</span>` : ""}
                <span class="benefit-remaining amount-accent">${fmt(item.monthly_premium)}원</span>
              </span>
            </div>
            <div class="card-text-row">
              <span class="card-method-dim">${item.company ? escapeHtml(item.company) : "보험사 미입력"}</span>
              <span class="card-head-right">
                <button class="icon-btn edit-insurance" data-id="${item.id}" title="수정">✏️</button>
                <button class="icon-btn delete-insurance" data-id="${item.id}" title="삭제">🗑️</button>
              </span>
            </div>
          </div>
        </div>
      </div>
      ${infoLine ? `<div class="perf-row-sub">${escapeHtml(infoLine)}</div>` : ""}
      ${memoToggleTemplate(`insurance-${item.id}`, item.memo)}
      ${matchHistoryTemplate(`insurance-${item.id}`, item.match_history)}
    `;
  }

  function wireInsurance(el, item) {
    el.querySelector(".edit-insurance").addEventListener("click", () => openInsuranceModal(item));
    el.querySelector(".delete-insurance").addEventListener("click", async () => {
      if (!confirm(`"${item.product_name}"을(를) 삭제할까요?`)) return;
      state = await api(`/api/insurances/${item.id}`, { method: "DELETE" });
      render();
    });
    const matchBadge = el.querySelector(".match-badge");
    if (matchBadge) matchBadge.addEventListener("click", () => openMatchModal("insurances", item, item.product_name));
    wireMatchHistory(el, `insurance-${item.id}`);
    el.querySelectorAll(".memo-toggle").forEach((btn) =>
      btn.addEventListener("click", () => {
        const key = btn.dataset.memoKey;
        if (openMemoPanels.has(key)) openMemoPanels.delete(key);
        else openMemoPanels.add(key);
        render();
      })
    );
  }

  document.getElementById("btn-insurance-automatch").addEventListener("click", async () => {
    const result = await api("/api/insurances/auto-match", { method: "POST" });
    state = result;
    render();
    alert(`${result.auto_match_count || 0}건 자동 매칭됐습니다.`);
  });

  // ---- 보험 추가/수정 모달 ----
  const insuranceModal = document.getElementById("insurance-modal");
  let editingInsuranceId = null;

  const insuranceMethodSelect = document.getElementById("insurance-method-select");
  const insuranceMethodInput = document.getElementById("insurance-method");
  const CUSTOM_METHOD_INS = "__custom__";

  insuranceMethodSelect.addEventListener("change", () => {
    if (insuranceMethodSelect.value === CUSTOM_METHOD_INS) {
      insuranceMethodInput.style.display = "";
      insuranceMethodInput.value = "";
      insuranceMethodInput.focus();
    } else {
      insuranceMethodInput.style.display = "none";
      insuranceMethodInput.value = "";
    }
  });

  function openInsuranceModal(item) {
    editingInsuranceId = item ? item.id : null;
    document.getElementById("insurance-modal-title").textContent = item ? "보험 수정" : "보험 추가";
    document.getElementById("insurance-company").value = item ? item.company : "";
    document.getElementById("insurance-product-name").value = item ? item.product_name : "";
    document.getElementById("insurance-insured-name").value = item ? item.insured_name : "본인";
    document.getElementById("insurance-premium").value = item ? item.monthly_premium : "";

    const options = buildMethodOptions(state.insurances);
    const current = item ? (item.payment_method || "") : "";
    const cardOption = item && item.card_id ? options.find((o) => o.value === `card:${item.card_id}`) : null;
    const textMatched = !cardOption && options.find((o) => normMethod(o.label) === normMethod(current));
    const matched = cardOption || textMatched;
    insuranceMethodSelect.innerHTML = [
      `<option value="">선택 안 함</option>`,
      ...options.map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`),
      `<option value="${CUSTOM_METHOD_INS}">직접 입력</option>`,
    ].join("");
    if (current && !matched) {
      insuranceMethodSelect.value = CUSTOM_METHOD_INS;
      insuranceMethodInput.style.display = "";
      insuranceMethodInput.value = current;
    } else {
      insuranceMethodSelect.value = matched ? matched.value : "";
      insuranceMethodInput.style.display = "none";
      insuranceMethodInput.value = "";
    }

    document.getElementById("insurance-day").value = item && item.payment_day ? item.payment_day : "";
    document.getElementById("insurance-start-date").value = item ? item.start_date : "";
    document.getElementById("insurance-payment-end-date").value = item ? item.payment_end_date : "";
    document.getElementById("insurance-maturity-date").value = item ? item.maturity_date : "";
    document.getElementById("insurance-active").checked = item ? !!item.active : true;
    document.getElementById("insurance-memo").value = item ? item.memo : "";
    insuranceModal.classList.add("open");
  }
  document.getElementById("btn-add-insurance").addEventListener("click", () => openInsuranceModal(null));
  document.getElementById("insurance-cancel").addEventListener("click", () => insuranceModal.classList.remove("open"));
  document.getElementById("insurance-save").addEventListener("click", async () => {
    const isCardChoice = insuranceMethodSelect.value.startsWith("card:");
    const payload = {
      company: document.getElementById("insurance-company").value.trim(),
      product_name: document.getElementById("insurance-product-name").value.trim(),
      insured_name: document.getElementById("insurance-insured-name").value.trim() || "본인",
      monthly_premium: document.getElementById("insurance-premium").value || 0,
      card_id: isCardChoice ? Number(insuranceMethodSelect.value.slice(5)) : null,
      payment_method: isCardChoice ? "" : (insuranceMethodSelect.value === CUSTOM_METHOD_INS ? insuranceMethodInput.value.trim() : insuranceMethodSelect.value),
      payment_day: document.getElementById("insurance-day").value || 0,
      start_date: document.getElementById("insurance-start-date").value.trim(),
      payment_end_date: document.getElementById("insurance-payment-end-date").value.trim(),
      maturity_date: document.getElementById("insurance-maturity-date").value.trim(),
      active: document.getElementById("insurance-active").checked,
      memo: document.getElementById("insurance-memo").value.trim(),
    };
    if (!payload.product_name) { alert("상품명을 입력하세요."); return; }
    const url = editingInsuranceId ? `/api/insurances/${editingInsuranceId}` : "/api/insurances";
    const method = editingInsuranceId ? "PUT" : "POST";
    state = await api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    insuranceModal.classList.remove("open");
    render();
  });

  // ---- 병원비·약국 청구 체크 ----
  const medicalSection = document.getElementById("medical-section");
  const medicalList = document.getElementById("medical-list");
  let medicalOpen = false;
  document.getElementById("medical-toggle").addEventListener("click", () => {
    medicalOpen = !medicalOpen;
    render();
  });

  function renderMedical() {
    const items = state.medical_expenses || [];
    const ids = items.map((m) => m.id);
    medicalSection.style.display = items.length ? "block" : "none";
    document.getElementById("medical-count").textContent = items.length ? `${items.length}건` : "";
    medicalList.style.display = medicalOpen ? "flex" : "none";

    const unclaimedTotal = items.filter((m) => !m.claimed).reduce((sum, m) => sum + (m.amount || 0), 0);
    document.getElementById("medical-unclaimed-total").textContent = `청구 안 한 병원비 합계 ₩${fmt(unclaimedTotal)}`;

    medicalList.innerHTML = bulkBarHtml("medical", "선택 제외(병원비 아님)", ids) + items.map((m) => `
      <div class="inbox-item">
        <input type="checkbox" class="bulk-item-check" data-id="${m.id}" ${bulkSelections.medical.has(m.id) ? "checked" : ""}>
        <div class="inbox-item-info">
          <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
            <input type="checkbox" class="medical-claim-check" data-id="${m.id}" ${m.claimed ? "checked" : ""}>
            <span class="inbox-item-amount">${fmt(m.amount)}원</span>
            ${m.merchant ? " · " + escapeHtml(m.merchant) : ""}
          </label>
          <div class="inbox-item-sub">${m.occurred_at}${m.claimed ? " · 청구함" : ""}</div>
        </div>
      </div>
    `).join("");
    medicalList.querySelectorAll(".medical-claim-check").forEach((cb) => {
      cb.addEventListener("change", async () => {
        state = await api(`/api/medical-expenses/${cb.dataset.id}/claim`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ claimed: cb.checked }),
        });
        render();
      });
    });
    wireBulkBar(medicalList, "medical", ids, async (selectedIds) => {
      state = await api("/api/medical-expenses/bulk-exclude", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: selectedIds }),
      });
      return true;
    });
  }

  // ---- 카드 모달 ----
  const cardModal = document.getElementById("card-modal");
  let editingCardId = null;

  function openCardModal(card) {
    editingCardId = card ? card.id : null;
    document.getElementById("card-modal-title").textContent = card ? "카드 수정" : "카드 추가";
    document.getElementById("card-name").value = card ? card.name : "";
    document.getElementById("card-short-name").value = card ? card.short_name : "";
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
      short_name: document.getElementById("card-short-name").value.trim(),
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
      if (!Array.isArray(entry) || entry.length < 2 || entry.length > 6) continue;
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
      if (!Array.isArray(entry) || entry.length < 2 || entry.length > 6) continue;
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

  [cardModal, benefitModal, useModal, assignModal, webhookModal, perfModal, loanModal, loanPaymentModal, recurringModal].forEach((modal) => {
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
