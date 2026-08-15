(() => {
  const cardList = document.getElementById("card-list");
  const emptyHint = document.getElementById("empty-hint");
  const inboxSection = document.getElementById("inbox-section");
  const inboxList = document.getElementById("inbox-list");
  const inboxCount = document.getElementById("inbox-count");

  let state = { cards: [], inbox: [] };
  const openLogPanels = new Set(); // benefit id 목록 - 펼쳐진 사용내역 기억

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

  function render() {
    renderInbox();
    renderCards();
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
          <div class="inbox-item-sub">${it.occurred_at}${it.matched_card_name ? " · " + escapeHtml(it.matched_card_name) : ""}${it.merchant ? " · " + escapeHtml(it.merchant) : ""}</div>
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

  // ---- 카드 목록 ----
  function renderCards() {
    cardList.querySelectorAll(".card").forEach((el) => el.remove());
    emptyHint.style.display = state.cards.length ? "none" : "block";

    state.cards.forEach((card) => {
      const el = document.createElement("div");
      el.className = "card";
      el.innerHTML = cardTemplate(card);
      cardList.appendChild(el);
      wireCard(el, card);
    });
  }

  function cardTemplate(card) {
    const benefitsHtml = card.benefits.map((b) => benefitTemplate(card, b)).join("") ||
      `<p class="benefit-memo">등록된 혜택이 없습니다.</p>`;
    return `
      <div class="card-head">
        <div>
          <div class="card-title-row">
            <span class="card-title">${escapeHtml(card.name)}</span>
            ${card.issuer ? `<span class="card-issuer">${escapeHtml(card.issuer)}</span>` : ""}
          </div>
          <div class="card-cycle">이번 혜택 기간: ${card.cycle_start} ~ ${card.cycle_end} (D-${card.days_left})</div>
          ${perfRowTemplate(card)}
          ${card.memo ? `<div class="card-memo">${escapeHtml(card.memo)}</div>` : ""}
        </div>
        <div class="card-actions">
          <button class="icon-btn edit-card" data-id="${card.id}" title="카드 수정">✏️</button>
          <button class="icon-btn delete-card" data-id="${card.id}" title="카드 삭제">🗑️</button>
        </div>
      </div>
      <div class="benefit-list">${benefitsHtml}</div>
      <div class="add-benefit-row">
        <button class="btn secondary small add-benefit" data-card-id="${card.id}">+ 혜택 추가</button>
      </div>
    `;
  }

  function perfRowTemplate(card) {
    if (!card.perf_threshold) return "";
    const cls = card.perf_met ? "met" : "unmet";
    const label = card.perf_met ? "실적 충족" : "실적 부족";
    return `
      <div class="perf-row">
        <span class="perf-badge ${cls}">${label}</span>
        <span>이번 달 ${fmt(card.perf_spend)}원 / ${fmt(card.perf_threshold)}원</span>
        <button class="perf-edit" data-id="${card.id}" data-spend="${card.perf_spend}">수정</button>
      </div>
    `;
  }

  function benefitTemplate(card, b) {
    const cls = statusClass(b.percent, b.over_limit);
    const unit = b.limit_type === "count" ? "회" : "원";
    const remainingText = b.over_limit
      ? `초과 ${fmt(Math.abs(b.remaining))}${unit}`
      : `${fmt(b.remaining)}${unit} 남음`;
    const logsOpen = openLogPanels.has(b.id);
    const logsHtml = b.logs.length
      ? b.logs.map((l) => `
          <div class="log-item">
            <span>${l.used_at} · ${fmt(l.used_value)}${unit}${l.memo ? " · " + escapeHtml(l.memo) : ""}</span>
            <button class="delete-log" data-id="${l.id}">삭제</button>
          </div>`).join("")
      : `<div class="log-item"><span>이번 기간 사용 기록 없음</span></div>`;

    return `
      <div class="benefit" data-benefit-id="${b.id}">
        <div class="benefit-top">
          <div>
            <div class="benefit-name">${escapeHtml(b.name)}</div>
            ${b.memo ? `<div class="benefit-memo">${escapeHtml(b.memo)}</div>` : ""}
          </div>
          <div class="benefit-remaining remaining-${cls}">${remainingText}</div>
        </div>
        <div class="progress-track"><div class="progress-fill ${cls}" style="width:${b.percent}%"></div></div>
        <div class="benefit-sub">
          <span>한도 ${fmt(b.limit_value)}${unit} 중 ${fmt(b.used)}${unit} 사용</span>
          <div class="benefit-buttons">
            <button class="btn small use-benefit" data-id="${b.id}" data-card-id="${card.id}" data-type="${b.limit_type}">사용 기록</button>
            <button class="btn secondary small toggle-log" data-id="${b.id}">내역</button>
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

    el.querySelectorAll(".use-benefit").forEach((btn) =>
      btn.addEventListener("click", () => openUseModal(btn.dataset.id, btn.dataset.type))
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

  // ---- 전월실적 이번 달 사용액 입력 모달 ----
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

  function openBenefitModal(cardId, benefit) {
    editingBenefit = { cardId, benefitId: benefit ? benefit.id : null };
    document.getElementById("benefit-modal-title").textContent = benefit ? "혜택 수정" : "혜택 추가";
    document.getElementById("benefit-name").value = benefit ? benefit.name : "";
    document.getElementById("benefit-type").value = benefit ? benefit.limit_type : "amount";
    document.getElementById("benefit-limit").value = benefit ? benefit.limit_value : "";
    document.getElementById("benefit-memo").value = benefit ? benefit.memo : "";
    benefitModal.classList.add("open");
  }
  document.getElementById("benefit-cancel").addEventListener("click", () => benefitModal.classList.remove("open"));
  document.getElementById("benefit-save").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("benefit-name").value.trim(),
      limit_type: document.getElementById("benefit-type").value,
      limit_value: document.getElementById("benefit-limit").value,
      memo: document.getElementById("benefit-memo").value.trim(),
    };
    if (!payload.name) { alert("혜택 이름을 입력하세요."); return; }
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
  let usingBenefitId = null;

  function openUseModal(benefitId, limitType) {
    usingBenefitId = benefitId;
    document.getElementById("use-value-label").firstChild.textContent =
      limitType === "count" ? "사용 횟수 (회) " : "사용 금액 (원) ";
    document.getElementById("use-value").value = limitType === "count" ? 1 : "";
    document.getElementById("use-date").value = new Date().toISOString().slice(0, 10);
    document.getElementById("use-memo").value = "";
    useModal.classList.add("open");
  }
  document.getElementById("use-cancel").addEventListener("click", () => useModal.classList.remove("open"));
  document.getElementById("use-save").addEventListener("click", async () => {
    const payload = {
      used_value: document.getElementById("use-value").value,
      used_at: document.getElementById("use-date").value,
      memo: document.getElementById("use-memo").value.trim(),
    };
    if (!payload.used_value || Number(payload.used_value) <= 0) {
      alert("사용 금액(또는 횟수)을 입력하세요.");
      return;
    }
    state = await api(`/api/benefits/${usingBenefitId}/use`, {
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

  function fillAssignBenefitOptions(cardId) {
    const card = state.cards.find((c) => c.id == cardId);
    const benefits = card ? card.benefits : [];
    assignBenefitSelect.innerHTML = benefits.length
      ? benefits.map((b) => `<option value="${b.id}">${escapeHtml(b.name)} (${fmt(b.remaining)}${b.limit_type === "count" ? "회" : "원"} 남음)</option>`).join("")
      : `<option value="">등록된 혜택이 없습니다</option>`;
  }

  function openAssignModal(item) {
    if (!state.cards.length) {
      alert("먼저 카드와 혜택을 등록해주세요.");
      return;
    }
    assigningItem = item;
    document.getElementById("assign-raw-text").textContent = item.raw_text;
    assignCardSelect.innerHTML = state.cards
      .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
      .join("");
    const preselectCardId = item.matched_card_id || state.cards[0].id;
    assignCardSelect.value = preselectCardId;
    fillAssignBenefitOptions(preselectCardId);

    document.getElementById("assign-amount").value = item.amount != null ? item.amount : "";
    document.getElementById("assign-date").value = (item.occurred_at || "").slice(0, 10) || new Date().toISOString().slice(0, 10);
    assignModal.classList.add("open");
  }

  assignCardSelect.addEventListener("change", () => fillAssignBenefitOptions(assignCardSelect.value));

  document.getElementById("assign-cancel").addEventListener("click", () => assignModal.classList.remove("open"));

  document.getElementById("assign-discard").addEventListener("click", async () => {
    if (!confirm("이 알림을 무시할까요? (광고 등 결제와 무관한 알림일 때)")) return;
    state = await api(`/api/inbox/${assigningItem.id}`, { method: "DELETE" });
    assignModal.classList.remove("open");
    render();
  });

  document.getElementById("assign-save").addEventListener("click", async () => {
    const benefitId = assignBenefitSelect.value;
    if (!benefitId) { alert("혜택을 선택하세요."); return; }
    const payload = {
      benefit_id: benefitId,
      amount: document.getElementById("assign-amount").value,
      used_at: document.getElementById("assign-date").value,
    };
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
