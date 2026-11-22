(() => {
  let state = { documents: [], aggregate: [], salary: [] };
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
    if (!issueDate) return "";
    const parts = issueDate.split("-");
    const yymmdd = parts.length === 3 ? parts[0].slice(2) + parts[1] + parts[2] : issueDate;
    return `<span class="issue-date-tag">${escapeHtml(yymmdd)}</span>`;
  }

  // 종합소득세 신고서는 급여를 준 회사가 여러 곳일 수 있어서(근로소득
  // 지급자별 명세), "어떤 회사에서 얼마"를 바로 볼 수 있게 짧게 보여준다.
  function incomeSourcesLine(incomeSourcesJson) {
    if (!incomeSourcesJson) return "";
    let payers;
    try {
      payers = JSON.parse(incomeSourcesJson);
    } catch {
      return "";
    }
    if (!Array.isArray(payers) || !payers.length) return "";
    const parts = payers.map((p) => `${escapeHtml(p.name)} ${fmt(p.income)}`);
    return `<div class="income-sources">${parts.join(" · ")}</div>`;
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
    renderSalary();
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
    const docTypes = [...byType.keys()];
    let html = "";
    docTypes.forEach((docType, idx) => {
      const rows = byType.get(docType);
      const cols = columnsForType(docType);
      const trs = rows.map((r) => `
        <tr>
          <td>${shortYear(r.year)}년${r.half ? ` ${r.half}기` : ""}</td>
          ${cols.map((c) => `<td>${r[c.key] !== null ? fmt(r[c.key]) : "-"}</td>`).join("")}
        </tr>
      `).join("");
      html += `
        <div class="year-group-title">
          <div class="reorder-buttons">
            <button class="move-doctype-up" data-doctype="${escapeHtml(docType)}" ${idx === 0 ? "disabled" : ""} title="위로">▲</button>
            <button class="move-doctype-down" data-doctype="${escapeHtml(docType)}" ${idx === docTypes.length - 1 ? "disabled" : ""} title="아래로">▼</button>
          </div>
          ${escapeHtml(docType)}
        </div>
        <div class="table-scroll">
          <table class="agg-table">
            <thead><tr><th>연도</th>${cols.map((c) => `<th>${escapeHtml(c.label)}</th>`).join("")}</tr></thead>
            <tbody>${trs}</tbody>
          </table>
        </div>
      `;
    });
    area.innerHTML = html;

    area.querySelectorAll(".move-doctype-up, .move-doctype-down").forEach((btn) => {
      if (btn.disabled) return;
      const direction = btn.classList.contains("move-doctype-up") ? "up" : "down";
      btn.addEventListener("click", async () => {
        state = { ...state, ...await api(`/api/doc_type_order/${encodeURIComponent(btn.dataset.doctype)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction }),
        }) };
        render();
      });
    });
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
    // 소득금액증명끼리) - 버튼을 누르면 바로 다운로드. 연도 구분이 없는
    // 보관용 서류(사업자등록증 등)는 종류별로 따로따로 안 나누고 "사업자
    // 서류" 하나로 묶어서 보여준다 - 연도별로 갈라지는 서류와 성격이
    // 달라서 섞어 보고 싶다는 요청.
    const NO_YEAR_GROUP_TYPES = new Set([
      "사업자등록증", "통신판매업 신고증", "건강기능식품 영업 신고증",
      "의료기기 판매업 신고증", "사업자 통장 사본",
    ]);
    const NO_YEAR_GROUP_LABEL = "사업자 서류";
    const groups = new Map();
    for (const d of state.documents) {
      const key = NO_YEAR_GROUP_TYPES.has(d.doc_type) ? NO_YEAR_GROUP_LABEL : (d.doc_type || "기타");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(d);
    }
    let html = "";
    for (const [docType, docs] of groups) {
      const isOpen = openDocTypeGroups.has(docType);
      // 같은 연도끼리는 기(1기/2기)가 갈려도 한 줄에 같이 나오게 묶는다
      // - 라벨이 완전히 같을 때만 묶으면 1기/2기처럼 라벨이 다른 건
      // 안 묶여서 요청과 어긋났다. 연도 단위로 묶되, 표시되는 라벨은
      // 각자(1기/2기) 그대로 보여준다.
      const yearOrder = [];
      const byYear = new Map();
      for (const d of docs) {
        const key = d.year != null ? String(d.year) : (d.period_label || d.name);
        if (!byYear.has(key)) { byYear.set(key, []); yearOrder.push(key); }
        byYear.get(key).push(d);
      }
      const btns = yearOrder.map((key) => {
        const groupDocs = byYear.get(key);
        const items = groupDocs.map((d) => {
          const label = d.period_label || (d.year ? `${shortYear(d.year)}년` : d.name);
          return `
          <div class="period-item">
            ${d.file_name
              ? `<a class="period-btn" href="/api/documents/${d.id}/download" title="${escapeHtml(d.name)}">${escapeHtml(label)}</a>`
              : `<span class="period-btn" style="opacity:.6;" title="파일 없음">${escapeHtml(label)}</span>`}
            ${issueDateBadge(d.issue_date)}
            <button class="icon-btn edit-doc" data-id="${d.id}" title="수정">✏️</button>
            <button class="icon-btn delete-doc" data-id="${d.id}" title="삭제">🗑️</button>
          </div>
        `;
        }).join("");
        return `
          <div class="period-btn-wrap">${items}</div>
          ${groupDocs.map((d) => incomeSourcesLine(d.income_sources)).join("")}
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
        state = { ...state, ...await api(`/api/documents/${btn.dataset.id}`, { method: "DELETE" }) };
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
    state = { ...state, ...await api(url, { method, body: formData }) };
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

      state = { ...state, documents: data.documents, aggregate: data.aggregate };
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

  // ---- 탭 전환(서류 / 월급여) ----
  const tabBtnDocuments = document.getElementById("tab-btn-documents");
  const tabBtnSalary = document.getElementById("tab-btn-salary");
  const documentsTab = document.getElementById("documents-tab");
  const salaryTab = document.getElementById("salary-tab");
  function showTab(name) {
    const isDocs = name === "documents";
    documentsTab.style.display = isDocs ? "" : "none";
    salaryTab.style.display = isDocs ? "none" : "";
    tabBtnDocuments.classList.toggle("active", isDocs);
    tabBtnSalary.classList.toggle("active", !isDocs);
  }
  tabBtnDocuments.addEventListener("click", () => showTab("documents"));
  tabBtnSalary.addEventListener("click", () => showTab("salary"));

  // ---- 월급여 탭 ----
  const SALARY_COLUMNS = [
    // 제일 중요한 실지급액/소계를 맨 앞에, 세부 지급·공제 항목은 뒤에
    // (모바일에서 가로로 잘려도 중요한 숫자부터 보이게).
    { key: "net_pay", label: "실지급액" },
    { key: "gross_pay", label: "지급소계" },
    { key: "total_deduction", label: "공제소계" },
    { key: "base_pay", label: "기본급" },
    { key: "overtime_pay", label: "연장수당" },
    { key: "position_pay", label: "직책수당" },
    { key: "night_pay", label: "야근수당" },
    { key: "other_pay", label: "기타" },
    { key: "income_tax", label: "갑근세" },
    { key: "local_tax", label: "주민세" },
    { key: "national_pension", label: "국민연금" },
    { key: "health_insurance", label: "건강보험" },
    { key: "employment_insurance", label: "고용보험" },
    { key: "loan_repayment", label: "학자금상환" },
  ];
  const MONTH_SELECT_IDS = ["salary-upload-month", "salary-month"];
  for (const id of MONTH_SELECT_IDS) {
    const sel = document.getElementById(id);
    for (let m = 1; m <= 12; m++) {
      const opt = document.createElement("option");
      opt.value = String(m);
      opt.textContent = `${m}월`;
      sel.appendChild(opt);
    }
  }
  const today = new Date();
  {
    const sel = document.getElementById("salary-upload-year");
    const thisYear = today.getFullYear();
    for (let y = thisYear - 3; y <= thisYear; y++) {
      const opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = `${y}년`;
      sel.appendChild(opt);
    }
    sel.value = String(thisYear);
  }
  document.getElementById("salary-upload-month").value = String(today.getMonth() + 1);

  let salaryYear = null;
  function renderSalary() {
    const label = document.getElementById("salary-year-label");
    const area = document.getElementById("salary-table-area");
    if (!state.salary.length) {
      if (salaryYear === null) salaryYear = today.getFullYear();
      label.textContent = `${salaryYear}년 급여`;
      area.innerHTML = `<div class="empty-hint">아직 등록된 급여 기록이 없습니다.</div>`;
      return;
    }
    const years = [...new Set(state.salary.map((s) => s.year))];
    if (salaryYear === null || !years.includes(salaryYear)) {
      salaryYear = Math.max(...years);
    }
    label.textContent = `${salaryYear}년 급여`;

    const byMonth = new Map(state.salary.filter((s) => s.year === salaryYear).map((s) => [s.month, s]));
    const yearRows = [...byMonth.values()];
    const totals = SALARY_COLUMNS.map((c) =>
      yearRows.reduce((sum, s) => (s[c.key] != null ? sum + s[c.key] : sum), 0)
    );
    const totalRow = `
      <tr class="salary-total-row">
        <td>합계</td>
        ${totals.map((v) => `<td>${fmt(v)}</td>`).join("")}
        <td></td>
      </tr>
    `;
    const rows = [];
    for (let m = 1; m <= 12; m++) {
      const s = byMonth.get(m);
      rows.push(`
        <tr>
          <td>${m}월</td>
          ${SALARY_COLUMNS.map((c) => `<td>${s && s[c.key] != null ? fmt(s[c.key]) : "-"}</td>`).join("")}
          <td><div class="salary-actions">${s ? `
            ${s.file_name ? `<a class="icon-btn" href="/api/salary/${s.id}/download" title="파일 보기">📎</a>` : ""}
            <button class="icon-btn edit-salary" data-id="${s.id}" title="수정">✏️</button>
            <button class="icon-btn delete-salary" data-id="${s.id}" title="삭제">🗑️</button>
          ` : ""}</div></td>
        </tr>
      `);
    }
    area.innerHTML = `
      <div class="table-scroll">
        <table class="agg-table">
          <thead><tr><th>월</th>${SALARY_COLUMNS.map((c) => `<th>${escapeHtml(c.label)}</th>`).join("")}<th></th></tr></thead>
          <tbody>${totalRow}${rows.join("")}</tbody>
        </table>
      </div>
    `;
    area.querySelectorAll(".edit-salary").forEach((btn) =>
      btn.addEventListener("click", () => openSalaryModal(state.salary.find((s) => s.id === Number(btn.dataset.id))))
    );
    area.querySelectorAll(".delete-salary").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("이 급여 기록을 삭제할까요?")) return;
        state = { ...state, ...await api(`/api/salary/${btn.dataset.id}`, { method: "DELETE" }) };
        renderSalary();
      })
    );
  }
  document.getElementById("salary-year-prev").addEventListener("click", () => {
    salaryYear = (salaryYear ?? today.getFullYear()) - 1;
    renderSalary();
  });
  document.getElementById("salary-year-next").addEventListener("click", () => {
    salaryYear = (salaryYear ?? today.getFullYear()) + 1;
    renderSalary();
  });

  const SALARY_FIELD_IDS = [
    ["net_pay", "salary-net-pay"], ["gross_pay", "salary-gross-pay"],
    ["total_deduction", "salary-total-deduction"], ["base_pay", "salary-base-pay"],
    ["overtime_pay", "salary-overtime-pay"], ["position_pay", "salary-position-pay"],
    ["night_pay", "salary-night-pay"],
    ["other_pay", "salary-other-pay"], ["income_tax", "salary-income-tax"],
    ["local_tax", "salary-local-tax"], ["national_pension", "salary-national-pension"],
    ["health_insurance", "salary-health-insurance"], ["employment_insurance", "salary-employment-insurance"],
    ["loan_repayment", "salary-loan-repayment"],
  ];
  const salaryModal = document.getElementById("salary-modal");
  let editingSalaryId = null;
  function openSalaryModal(item) {
    editingSalaryId = item ? item.id : null;
    document.getElementById("salary-modal-title").textContent = item ? "급여 수정" : "급여 추가";
    document.getElementById("salary-year").value = item ? item.year : (salaryYear ?? today.getFullYear());
    document.getElementById("salary-month").value = item ? item.month : (today.getMonth() + 1);
    for (const [key, id] of SALARY_FIELD_IDS) {
      document.getElementById(id).value = item && item[key] != null ? item[key] : "";
    }
    document.getElementById("salary-file").value = "";
    document.getElementById("salary-memo").value = item ? (item.memo || "") : "";
    salaryModal.classList.add("open");
  }
  document.getElementById("btn-add-salary").addEventListener("click", () => openSalaryModal(null));
  document.getElementById("salary-cancel").addEventListener("click", () => salaryModal.classList.remove("open"));
  document.getElementById("salary-save").addEventListener("click", async () => {
    const formData = new FormData();
    formData.append("year", document.getElementById("salary-year").value);
    formData.append("month", document.getElementById("salary-month").value);
    for (const [key, id] of SALARY_FIELD_IDS) {
      formData.append(key, document.getElementById(id).value);
    }
    formData.append("memo", document.getElementById("salary-memo").value.trim());
    const file = document.getElementById("salary-file").files[0];
    if (file) formData.append("file", file);

    const url = editingSalaryId ? `/api/salary/${editingSalaryId}` : "/api/salary";
    const method = editingSalaryId ? "PUT" : "POST";
    state = { ...state, ...await api(url, { method, body: formData }) };
    salaryModal.classList.remove("open");
    renderSalary();
  });

  const salaryUploadLog = document.getElementById("salary-upload-log");
  document.getElementById("salary-upload-file").addEventListener("change", async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    // 한 번에 여러 장 고르면, 선택한 연/월부터 시작해서 파일 순서대로
    // 한 달씩 넘어가며 자동 배정한다(1월치 고르고 1~12월 전부 골라
    // 올리는 식의 사용을 가정) - 틀리게 배정된 건 나중에 수정하면 됨.
    let year = Number(document.getElementById("salary-upload-year").value);
    let month = Number(document.getElementById("salary-upload-month").value);

    const lines = [];
    const renderLog = () => { salaryUploadLog.innerHTML = lines.join(""); };

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const prefix = files.length > 1 ? `[${i + 1}/${files.length}] ` : "";
      lines.push(`<div class="upload-log-item">${prefix}${escapeHtml(file.name)} - 읽는 중... (처음 한 번은 오래 걸릴 수 있음)</div>`);
      renderLog();

      const formData = new FormData();
      formData.append("file", file);
      formData.append("year", String(year));
      formData.append("month", String(month));
      let data;
      try {
        const res = await fetch("/api/salary/auto", { method: "POST", body: formData });
        data = await res.json();
        if (!res.ok) throw new Error(data.error || "등록 실패");
      } catch (err) {
        lines[lines.length - 1] = `<div class="upload-log-item err">${prefix}${escapeHtml(file.name)} - ${escapeHtml(err.message)}</div>`;
        renderLog();
        month += 1;
        if (month > 12) { month = 1; year += 1; }
        continue;
      }
      state = { ...state, salary: data.salary };
      // 명세서 안에서 실제 일한 월을 알아낸 경우 그 값을 쓰고(다음
      // 파일의 기본값도 거기 이어서), 못 알아냈을 때만 내가 고른
      // 연/월을 그대로 쓴다.
      const actualYear = data.year ?? year;
      const actualMonth = data.month ?? month;
      salaryYear = actualYear;
      renderSalary();
      const periodNote = data.period_detected ? "(서류에서 자동 인식)" : "";
      const summary = data.text_found
        ? `${actualYear}년 ${actualMonth}월로 등록됨${periodNote} - 읽은 금액 확인해줘`
        : "글자를 못 읽음 - 직접 수정 필요";
      lines[lines.length - 1] = `<div class="upload-log-item">${prefix}${escapeHtml(file.name)} - ${summary}</div>`;
      renderLog();
      year = actualYear;
      month = actualMonth + 1;
      if (month > 12) { month = 1; year += 1; }
    }
    e.target.value = "";
  });

  loadFull();
})();
