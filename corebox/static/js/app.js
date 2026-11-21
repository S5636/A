(() => {
  let state = { documents: [], aggregate: [] };
  let editingId = null;

  function fmt(n) {
    if (n === null || n === undefined || n === "") return "";
    return Number(n).toLocaleString("ko-KR");
  }
  function shortYear(y) {
    return y ? String(y).slice(2) : "";
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // 문서 종류마다 실제로 있는 항목만 표/목록에 보여준다 - 부가세에는
  // 수입금액/소득금액 자체가 없고, 소득금액증명에는 "과세표준액" 같은
  // 게 없다. 목록에 없는 종류는 기본값(금액 하나)으로 처리.
  const DOC_TYPE_COLUMNS = {
    // 제일 중요한 합계(수입금액/소득금액)를 맨 앞에 두고, 사업/근로/기타
    // 소득별 세부 내역은 뒤에 붙인다.
    "소득금액증명": [
      { key: "income_total", itemKey: "income_amount", label: "수입금액" },
      { key: "taxable_total", itemKey: "taxable_income_amount", label: "소득금액" },
      { key: "income_business_total", itemKey: "income_business", label: "사업수입" },
      { key: "income_wage_total", itemKey: "income_wage", label: "근로수입" },
      { key: "income_other_total", itemKey: "income_other", label: "기타수입" },
      { key: "taxable_business_total", itemKey: "taxable_business", label: "사업소득" },
      { key: "taxable_wage_total", itemKey: "taxable_wage", label: "근로소득" },
      { key: "taxable_other_total", itemKey: "taxable_other", label: "기타소득" },
    ],
    "부가가치세 과세표준증명": [
      { key: "amount_total", itemKey: "amount", label: "과세표준액" },
      { key: "tax_total", itemKey: "tax_amount", label: "세액" },
    ],
    "부가가치세 신고서": [
      { key: "amount_total", itemKey: "amount", label: "과세표준액" },
      { key: "tax_total", itemKey: "tax_amount", label: "세액" },
    ],
    "종합소득세 신고서": [
      { key: "amount_total", itemKey: "amount", label: "종합소득금액" },
      { key: "tax_total", itemKey: "tax_amount", label: "납부(환급)세액" },
    ],
  };
  function columnsForType(docType) {
    return DOC_TYPE_COLUMNS[docType] || [{ key: "amount_total", itemKey: "amount", label: "금액" }];
  }

  function issueDateBadge(issueDate) {
    // 발급일 기준 서류, 보통 한 달 지나면 제출처에서 안 받아주므로
    // 30일 넘으면 눈에 띄게 경고 표시(평소엔 조용히 발급일만 작게 표시).
    if (!issueDate) return "";
    const days = Math.floor((Date.now() - new Date(issueDate).getTime()) / 86400000);
    const stale = days > 30;
    return `<span class="issue-date-tag" style="${stale ? "color:var(--critical);font-weight:700;" : ""}">${escapeHtml(issueDate)}${stale ? ` (${days}일 지남)` : ""}</span>`;
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
      const cols = columnsForType(docType);
      const trs = rows.map((r) => `
        <tr>
          <td>${shortYear(r.year)}년</td>
          ${cols.map((c) => `<td>${r[c.key] !== null ? fmt(r[c.key]) : "-"}</td>`).join("")}
        </tr>
      `).join("");
      html += `
        <div class="year-group-title">${escapeHtml(docType)}</div>
        <div class="table-scroll">
          <table class="agg-table">
            <thead><tr><th>연도</th>${cols.map((c) => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
            <tbody>${trs}</tbody>
          </table>
        </div>
      `;
    }
    area.innerHTML = html;
  }

  const openDocTypeGroups = new Set();

  function renderDocuments() {
    const area = document.getElementById("documents-area");
    if (!state.documents.length) {
      area.innerHTML = `<div class="empty-hint">아직 등록된 서류가 없습니다.</div>`;
      return;
    }
    // 문서 종류별로 제목을 누르면 열리고 닫히는 작은 박스 하나씩 만들고,
    // 그 안에 기수/연도 버튼을 나열한다(부가세는 부가세끼리, 소득금액증명은
    // 소득금액증명끼리) - 버튼을 누르면 바로 다운로드.
    const groups = new Map();
    for (const d of state.documents) {
      const key = d.doc_type || "기타";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    }
    let html = "";
    for (const [docType, docs] of groups) {
      const isOpen = openDocTypeGroups.has(docType);
      const btns = docs.map((d) => {
        const label = d.period_label || (d.year ? `${shortYear(d.year)}년` : d.name);
        const issueBadge = issueDateBadge(d.issue_date);
        return `
          <div class="period-btn-wrap">
            ${d.file_name
              ? `<a class="period-btn" href="/api/documents/${d.id}/download" title="${escapeHtml(d.name)}">${escapeHtml(label)}</a>`
              : `<span class="period-btn" style="opacity:.6;" title="파일 없음">${escapeHtml(label)}</span>`}
            ${issueBadge}
            <button class="icon-btn edit-doc" data-id="${d.id}" title="수정">✏️</button>
            <button class="icon-btn delete-doc" data-id="${d.id}" title="삭제">🗑️</button>
          </div>
        `;
      }).join("");
      html += `
        <div class="doctype-header" data-doctype="${escapeHtml(docType)}">${isOpen ? "▾" : "▸"} ${escapeHtml(docType)} <span class="doctype-count">${docs.length}</span></div>
        <div class="doctype-body" style="display:${isOpen ? "flex" : "none"};">${btns}</div>
      `;
    }
    area.innerHTML = html;
    area.querySelectorAll(".doctype-header").forEach((h) =>
      h.addEventListener("click", () => {
        const key = h.dataset.doctype;
        if (openDocTypeGroups.has(key)) openDocTypeGroups.delete(key);
        else openDocTypeGroups.add(key);
        renderDocuments();
      })
    );
    area.querySelectorAll(".edit-doc").forEach((btn) =>
      btn.addEventListener("click", () => openDocModal(state.documents.find((d) => d.id === Number(btn.dataset.id))))
    );
    area.querySelectorAll(".delete-doc").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 서류를 삭제할까요?")) return;
        state = await api(`/api/documents/${btn.dataset.id}`, { method: "DELETE" });
        renderDocuments();
      })
    );
  }

  const modal = document.getElementById("doc-modal");

  // 수정/추가 모달도 아는 문서 종류면 그 종류에 실제 있는 항목 칸만
  // 보여준다(부가세엔 수입금액/소득금액 칸, 소득금액증명엔 과세표준액/
  // 세액 칸 같은 게 비어있는 채로 안 뜨게). 모르는 종류거나 비어있으면
  // 전부 보여줘서(뭘로 쓸지 모르니) 자유롭게 입력 가능.
  const FIELD_VISIBILITY = {
    "소득금액증명": { income: true, taxable: true, breakdown: true, amount: false, taxAmount: false },
    "부가가치세 과세표준증명": { income: false, taxable: false, breakdown: false, amount: true, taxAmount: true },
    "부가가치세 신고서": { income: false, taxable: false, breakdown: false, amount: true, taxAmount: true },
    "종합소득세 신고서": { income: false, taxable: false, breakdown: false, amount: true, taxAmount: true },
  };
  const BREAKDOWN_ROW_IDS = [
    "doc-income-business-row", "doc-income-wage-row", "doc-income-other-row",
    "doc-taxable-business-row", "doc-taxable-wage-row", "doc-taxable-other-row",
  ];
  function applyFieldVisibility(docType) {
    const v = FIELD_VISIBILITY[docType] || { income: true, taxable: true, breakdown: true, amount: true, taxAmount: true };
    document.getElementById("doc-income-row").style.display = v.income ? "" : "none";
    document.getElementById("doc-taxable-row").style.display = v.taxable ? "" : "none";
    for (const rowId of BREAKDOWN_ROW_IDS) {
      document.getElementById(rowId).style.display = v.breakdown ? "" : "none";
    }
    document.getElementById("doc-amount-row").style.display = v.amount ? "" : "none";
    document.getElementById("doc-tax-amount-row").style.display = v.taxAmount ? "" : "none";
  }
  document.getElementById("doc-type").addEventListener("input", (e) => applyFieldVisibility(e.target.value.trim()));

  function openDocModal(item) {
    editingId = item ? item.id : null;
    document.getElementById("doc-modal-title").textContent = item ? "서류 수정" : "서류 추가";
    document.getElementById("doc-name").value = item ? item.name : "";
    document.getElementById("doc-category").value = item ? (item.category || "") : "";
    document.getElementById("doc-year").value = item && item.year ? item.year : "";
    document.getElementById("doc-type").value = item ? (item.doc_type || "") : "";
    document.getElementById("doc-issue-date").value = item ? (item.issue_date || "") : "";
    document.getElementById("doc-period").value = item ? (item.period || "") : "";
    document.getElementById("doc-period-label").value = item ? (item.period_label || "") : "";
    document.getElementById("doc-income").value = item && item.income_amount !== null && item.income_amount !== undefined ? item.income_amount : "";
    document.getElementById("doc-taxable").value = item && item.taxable_income_amount !== null && item.taxable_income_amount !== undefined ? item.taxable_income_amount : "";
    for (const key of ["income_business", "income_wage", "income_other", "taxable_business", "taxable_wage", "taxable_other"]) {
      const fieldId = `doc-${key.replace(/_/g, "-")}`;
      document.getElementById(fieldId).value = item && item[key] !== null && item[key] !== undefined ? item[key] : "";
    }
    document.getElementById("doc-amount").value = item && item.amount !== null && item.amount !== undefined ? item.amount : "";
    document.getElementById("doc-tax-amount").value = item && item.tax_amount !== null && item.tax_amount !== undefined ? item.tax_amount : "";
    document.getElementById("doc-file").value = "";
    document.getElementById("doc-memo").value = item ? (item.memo || "") : "";
    applyFieldVisibility(item ? (item.doc_type || "") : "");
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
    formData.append("period_label", document.getElementById("doc-period-label").value.trim());
    formData.append("income_amount", document.getElementById("doc-income").value);
    formData.append("taxable_income_amount", document.getElementById("doc-taxable").value);
    for (const key of ["income_business", "income_wage", "income_other", "taxable_business", "taxable_wage", "taxable_other"]) {
      const fieldId = `doc-${key.replace(/_/g, "-")}`;
      formData.append(key, document.getElementById(fieldId).value);
    }
    formData.append("amount", document.getElementById("doc-amount").value);
    formData.append("tax_amount", document.getElementById("doc-tax-amount").value);
    formData.append("memo", document.getElementById("doc-memo").value.trim());
    const file = document.getElementById("doc-file").files[0];
    if (file) formData.append("file", file);

    const url = editingId ? `/api/documents/${editingId}` : "/api/documents";
    const method = editingId ? "PUT" : "POST";
    state = await api(url, { method, body: formData });
    modal.classList.remove("open");
    render();
  });

  const autoUploadLog = document.getElementById("auto-upload-log");
  async function handleAutoUpload(inputEl) {
    const files = Array.from(inputEl.files || []);
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
      const multi = data.detected_count > 1 ? ` (한 장에 기수 ${data.detected_count}개 발견 - 따로 등록됨)` : "";
      const summary = d.doc_type
        ? `${escapeHtml(d.doc_type)}(으)로 분류됨${multi}${d.issue_date ? ` · 발급 ${d.issue_date}` : ""}`
        : (data.text_found ? "글자는 읽었지만 어떤 서류인지 분류 못함 - 직접 수정 필요" : "글자를 못 읽음(스캔 PDF 등) - 직접 수정 필요");
      lines[lines.length - 1] = `<div class="upload-log-item">${prefix}${escapeHtml(file.name)} - ${summary}</div>`;
      renderLog();
    }
    inputEl.value = "";
  }
  document.getElementById("auto-upload-image").addEventListener("change", (e) => handleAutoUpload(e.target));
  document.getElementById("auto-upload-pdf").addEventListener("change", (e) => handleAutoUpload(e.target));

  loadFull();
})();
