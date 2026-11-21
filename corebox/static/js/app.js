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
    const rows = state.aggregate.map((r) => `
      <tr>
        <td>${r.year}</td>
        <td>${escapeHtml(r.doc_type)}</td>
        <td>${r.income_total !== null ? fmt(r.income_total) : "-"}</td>
        <td>${r.taxable_total !== null ? fmt(r.taxable_total) : "-"}</td>
      </tr>
    `).join("");
    area.innerHTML = `
      <table class="agg-table">
        <thead><tr><th>연도</th><th>문서 종류</th><th>수입금액 합계</th><th>소득금액 합계</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  function renderDocuments() {
    const area = document.getElementById("documents-area");
    if (!state.documents.length) {
      area.innerHTML = `<div class="empty-hint">아직 등록된 서류가 없습니다.</div>`;
      return;
    }
    const groups = new Map();
    for (const d of state.documents) {
      const key = d.year ? String(d.year) : "연도 없음";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    }
    let html = "";
    for (const [year, docs] of groups) {
      html += `<div class="year-group-title">${escapeHtml(year)}</div>`;
      for (const d of docs) {
        const meta = [d.category, d.doc_type].filter(Boolean).join(" · ");
        const amounts = [
          d.income_amount !== null && d.income_amount !== undefined ? `수입 ${fmt(d.income_amount)}원` : "",
          d.taxable_income_amount !== null && d.taxable_income_amount !== undefined ? `소득 ${fmt(d.taxable_income_amount)}원` : "",
        ].filter(Boolean).join(" / ");
        html += `
          <div class="card">
            <div class="doc-row">
              <div class="doc-main">
                <div class="doc-name">${escapeHtml(d.name)}</div>
                ${meta ? `<div class="doc-meta">${escapeHtml(meta)}</div>` : ""}
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
    document.getElementById("doc-income").value = item && item.income_amount !== null && item.income_amount !== undefined ? item.income_amount : "";
    document.getElementById("doc-taxable").value = item && item.taxable_income_amount !== null && item.taxable_income_amount !== undefined ? item.taxable_income_amount : "";
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
    formData.append("income_amount", document.getElementById("doc-income").value);
    formData.append("taxable_income_amount", document.getElementById("doc-taxable").value);
    formData.append("memo", document.getElementById("doc-memo").value.trim());
    const file = document.getElementById("doc-file").files[0];
    if (file) formData.append("file", file);

    const url = editingId ? `/api/documents/${editingId}` : "/api/documents";
    const method = editingId ? "PUT" : "POST";
    state = await api(url, { method, body: formData });
    modal.classList.remove("open");
    render();
  });

  loadFull();
})();
