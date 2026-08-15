(() => {
  const cardList = document.getElementById("card-list");
  const emptyHint = document.getElementById("empty-hint");

  let state = [];
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

  function render() {
    cardList.querySelectorAll(".card").forEach((el) => el.remove());
    emptyHint.style.display = state.length ? "none" : "block";

    state.forEach((card) => {
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
          ${card.memo ? `<div class="card-memo">${escapeHtml(card.memo)}</div>` : ""}
        </div>
        <div class="card-actions">
          <button class="icon-btn edit-card" data-id="${card.id}" title="카드 수정">✏️</button>
          <button class="icon-btn delete-card" data-id="${card.id}" title="카드 삭제">🗑️</button>
        </div>
      </div>
      <div class="benefit-list">${benefitsHtml}</div>
      <div class="add-benefit-row">
        <button class="btn btn-small add-benefit" data-card-id="${card.id}">+ 혜택 추가</button>
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
            <button class="btn btn-small use-benefit" data-id="${b.id}" data-card-id="${card.id}" data-type="${b.limit_type}">사용 기록</button>
            <button class="btn btn-small toggle-log" data-id="${b.id}">내역</button>
            <button class="icon-btn edit-benefit" data-id="${b.id}" data-card-id="${card.id}">✏️</button>
            <button class="icon-btn delete-benefit" data-id="${b.id}">🗑️</button>
          </div>
        </div>
        <div class="log-list ${logsOpen ? "open" : ""}" data-log-for="${b.id}">${logsHtml}</div>
      </div>
    `;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function wireCard(el, card) {
    el.querySelector(".edit-card").addEventListener("click", () => openCardModal(card));
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
        const c = state.find((x) => x.id == btn.dataset.cardId);
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
    document.getElementById("card-reset-day").value = card ? card.reset_day : 1;
    document.getElementById("card-memo").value = card ? card.memo : "";
    cardModal.classList.add("open");
  }
  document.getElementById("btn-add-card").addEventListener("click", () => openCardModal(null));
  document.getElementById("card-cancel").addEventListener("click", () => cardModal.classList.remove("open"));
  document.getElementById("card-save").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("card-name").value.trim(),
      issuer: document.getElementById("card-issuer").value.trim(),
      reset_day: document.getElementById("card-reset-day").value,
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

  // ---- 사용 기록 모달 ----
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

  [cardModal, benefitModal, useModal].forEach((modal) => {
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
