(() => {
  let state = { documents: [], aggregate: [] };
  let editingId = null;

  function fmt(n) {
    if (n === null || n === undefined || n === "") return "";
    return Number(n).toLocaleString("ko-KR");
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function issueDateBadge(issueDate) {
    // 발급일 기준 서류, 보통 한 달 지나면 제출처에서 안 받아주므로
    // 30일 넘으면 눈에 띄게 경고 표시.
    if (!issueDate) return "";
    const days = Math.floor((Date.now() - new Date(issueDate).getTime()) / 86400000);
    const stale = days > 30;
    return `<div class="doc-meta" style="${stale ? "color:var(--critical);font-weight:700;" : ""}">발급일 ${escapeHtml(issueDate)}${stale ? ` (발급 ${days}일 지남 - 재발급 필요할 수 있음)` : ""}</div>`;
  }

  async function api(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      alert(body.error || "요청에 실패했습니다.");
      throw new Error(body.error || "request failed");
    }
    return resp.json();
  }

  async function loadFull() {
    state = await api("/api/full");
    render();
  }

  function render() {
    renderAggregate();
    renderDocuments();
  }

  function renderAggregate() {
    const area = document.getElementById("aggregate-area");
    if (!state.aggregate.length) {
      area.innerHTML = `<div class="empty-hint">집계용 문서 종류를 적은 서류가 아직 없습니다.</div>`;
      return;
    }
    // 같은 문서 종류끼리(부가세는 부가세끼리, 소득금액증명은 소득금액증명끼리)
    // 표를 따로 만든다 - 연도가 섞인 한 표에 문서종류별로 끼어있으면 비교하기
    // 불편하다는 지적 반영. 집계 결과는 이미 doc_type, year DESC 순으로 온다.
    const byType = new Map();
    for (const r of state.aggregate) {
      if (!byType.has(r.doc_type)) byType.set(r.doc_type, []);
      byType.get(r.doc_type).push(r);
    }
    let html = "";
    for (const [docType, rows] of byType) {
      const trs = rows.map((r) => `
        <tr>
          <td>${r.year}</td>
          <td>${r.income_total !== null ? fmt(r.income_total) : "-"}</td>
          <td>${r.taxable_total !== null ? fmt(r.taxable_total) : "-"}</td>
          <td>${r.amount_total !== null ? fmt(r.amount_total) : "-"}</td>
        </tr>
      `).join("");
      html += `
        <div class="year-group-title">${escapeHtml(docType)}</div>
        <table class="agg-table">
          <thead><tr><th>연도</th><th>수입금액</th><th>소득금액</th><th>금액</th></tr></thead>
          <tbody>${trs}</tbody>
        </table>
      `;
    }
    area.innerHTML = html;
  }

  function renderDocuments() {
    const area = document.getElementById("documents-area");
    if (!state.documents.length) {
      area.innerHTML = `<div class="empty-hint">아직 등록된 서류가 없습니다.</div>`;
      return;
    }
    // 서류 목록도 집계표랑 같은 기준(문서 종류)으로 먼저 묶고, 그 안에서
    // 연도순으로 보여준다 - 부가세끼리, 소득금액증명끼리 모여있게.
    const groups = new Map();
    for (const d of state.documents) {
      const key = d.doc_type || "기타";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    }
    let html = "";
    for (const [docType, docs] of groups) {
      html += `<div class="year-group-title">${escapeHtml(docType)}</div>`;
      for (const d of docs) {
        const meta = [d.year ? `${d.year}년` : "", d.period].filter(Boolean).join(" · ");
        const amounts = [
          d.income_amount !== null && d.income_amount !== undefined ? `수입 ${fmt(d.income_amount)}원` : "",
          d.taxable_income_amount !== null && d.taxable_income_amount !== undefined ? `소득 ${fmt(d.taxable_income_amount)}원` : "",
          d.amount !== null && d.amount !== undefined ? `금액 ${fmt(d.amount)}원` : "",
        ].filter(Boolean).join(" / ");
        const issueBadge = issueDateBadge(d.issue_date);
        html += `
          <div class="card">
            <div class="doc-row">
              <div class="doc-main">
                <div class="doc-name">${escapeHtml(d.name)}</div>
                ${meta ? `<div class="doc-meta">${escapeHtml(meta)}</div>` : ""}
                ${issueBadge ? issueBadge : ""}
                ${amounts ? `<div class="doc-amounts">${amounts}</div>` : ""}
              </div>
              <div class="doc-actions">
                ${d.file_name ? `<a class="icon-btn" href="/api/documents/${d.id}/download" title="다운로드">⬇️</a>` : ""}
                <button class="icon-btn edit-doc" data-id="${d.id}" title="수정">✏️</button>
                <button class="icon-btn delete-doc" data-id="${d.id}" title="삭제">🗑️</button>
              </div>
            </div>
          </div>
        `;
      }
    }
    area.innerHTML = html;
    area.querySelectorAll(".edit-doc").forEach((btn) =>
      btn.addEventListener("click", () => openDocModal(state.documents.find((d) => d.id === Number(btn.dataset.id))))
    );
    area.querySelectorAll(".delete-doc").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 서류를 삭제할까요?")) return;
        state = await api(`/api/documents/${btn.dataset.id}`, { method: "DELETE" });
        render();
      })
    );
  }

  const modal = document.getElementById("doc-modal");
  function openDocModal(item) {
    editingId = item ? item.id : null;
    document.getElementById("doc-modal-title").textContent = item ? "서류 수정" : "서류 추가";
    document.getElementById("doc-name").value = item ? item.name : "";
    document.getElementById("doc-category").value = item ? (item.category || "") : "";
    document.getElementById("doc-year").value = item && item.year ? item.year : "";
    document.getElementById("doc-type").value = item ? (item.doc_type || "") : "";
    document.getElementById("doc-issue-date").value = item ? (item.issue_date || "") : "";
    document.getElementById("doc-period").value = item ? (item.period || "") : "";
    document.getElementById("doc-income").value = item && item.income_amount !== null && item.income_amount !== undefined ? item.income_amount : "";
    document.getElementById("doc-taxable").value = item && item.taxable_income_amount !== null && item.taxable_income_amount !== undefined ? item.taxable_income_amount : "";
    document.getElementById("doc-amount").value = item && item.amount !== null && item.amount !== undefined ? item.amount : "";
    document.getElementById("doc-file").value = "";
    document.getElementById("doc-memo").value = item ? (item.memo || "") : "";
    modal.classList.add("open");
  }
  document.getElementById("btn-add-doc").addEventListener("click", () => openDocModal(null));
  document.getElementById("doc-cancel").addEventListener("click", () => modal.classList.remove("open"));
  document.getElementById("doc-save").addEventListener("click", async () => {
    const name = document.getElementById("doc-name").value.trim();
    if (!name) { alert("서류명을 입력하세요."); return; }
    const formData = new FormData();
    formData.append("name", name);
    formData.append("category", document.getElementById("doc-category").value.trim());
    formData.append("year", document.getElementById("doc-year").value);
    formData.append("doc_type", document.getElementById("doc-type").value.trim());
    formData.append("issue_date", document.getElementById("doc-issue-date").value);
    formData.append("period", document.getElementById("doc-period").value.trim());
    formData.append("income_amount", document.getElementById("doc-income").value);
    formData.append("taxable_income_amount", document.getElementById("doc-taxable").value);
    formData.append("amount", document.getElementById("doc-amount").value);
    formData.append("memo", document.getElementById("doc-memo").value.trim());
    const file = document.getElementById("doc-file").files[0];
    if (file) formData.append("file", file);

    const url = editingId ? `/api/documents/${editingId}` : "/api/documents";
    const method = editingId ? "PUT" : "POST";
    state = await api(url, { method, body: formData });
    modal.classList.remove("open");
    render();
  });

  const autoUploadFile = document.getElementById("auto-upload-file");
  const autoUploadLog = document.getElementById("auto-upload-log");
  autoUploadFile.addEventListener("change", async () => {
    const files = Array.from(autoUploadFile.files || []);
    if (!files.length) return;

    // 여러 장 골라도 서버 OCR이 한 번에 하나씩 처리하도록 짜여있어서
    // 순서대로 처리한다 - 한 장 끝날 때마다 로그 줄을 쌓아서 보여준다.
    const lines = [];
    const renderLog = () => { autoUploadLog.innerHTML = lines.join(""); };

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const prefix = files.length > 1 ? `[${i + 1}/${files.length}] ` : "";
      lines.push(`<div class="upload-log-item">${prefix}${escapeHtml(file.name)} - 읽는 중... (처음 한 번은 오래 걸릴 수 있음)</div>`);
      renderLog();

      const formData = new FormData();
      formData.append("file", file);
      let data;
      try {
        const res = await fetch("/api/documents/auto", { method: "POST", body: formData });
        data = await res.json();
        if (!res.ok) throw new Error(data.error || "등록 실패");
      } catch (e) {
        lines[lines.length - 1] = `<div class="upload-log-item err">${prefix}${escapeHtml(file.name)} - ${escapeHtml(e.message)}</div>`;
        renderLog();
        continue;
      }

      state = { documents: data.documents, aggregate: data.aggregate };
      render();

      const d = data.detected || {};
      const summary = d.doc_type
        ? `${escapeHtml(d.name)}(으)로 분류됨${d.year ? ` · ${d.year}년` : ""}${d.issue_date ? ` · 발급 ${d.issue_date}` : ""}`
        : (data.text_found ? "글자는 읽었지만 어떤 서류인지 분류 못함 - 직접 수정 필요" : "글자를 못 읽음(스캔 PDF 등) - 직접 수정 필요");
      lines[lines.length - 1] = `<div class="upload-log-item">${prefix}${escapeHtml(file.name)} - ${summary}</div>`;
      renderLog();
    }
    autoUploadFile.value = "";
  });

  loadFull();
})();
