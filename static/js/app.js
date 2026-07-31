/* 이유마켓 CEO Margin Pro - frontend */
(() => {
  'use strict';

  const MARKETS = ["쿠팡", "네이버", "11번가", "지마켓", "옥션", "TOSS", "카카오"];
  const MARKET_COLOR = {
    "쿠팡": "var(--series-1)", "네이버": "var(--series-2)", "11번가": "var(--series-3)",
    "지마켓": "var(--series-4)", "옥션": "var(--series-5)", "TOSS": "var(--series-6)", "카카오": "var(--series-7)",
  };
  const MARKET_COLOR_HEX = {
    "쿠팡": "#3987e5", "네이버": "#d95926", "11번가": "#199e70",
    "지마켓": "#c98500", "옥션": "#d55181", "TOSS": "#2fbf3f", "카카오": "#9085e9",
  };

  const COLS = [
    ['bundle_no', '합배송\n코드', 'l'], ['order_id', '원장\n주문코드', 'l'], ['source', '공급사', 'l'],
    ['market', '마켓', 'l'], ['sell_status', '주문\n상태', 'l'], ['buy_status', '매입\n상태', 'l'],
    ['order_date', '주문\n일시', 'l'], ['prod_id', '상품ID', 'l'], ['prod_name', '상품명', 'l'],
    ['qty', '수량', 'r'], ['order_amt', '주문\n금액', 'r'], ['ship_fee', '배송비', 'r'],
    ['add_ship_fee', '추가\n배송비', 'r'], ['fee_rate_display', '수수료율', 'r'], ['fee_amt', '마켓\n수수료', 'r'],
    ['settle_amt', '정산\n예정액', 'r'], ['vendor_prod_id', '판매사\n상품코드', 'l'], ['buy_cost', '매입가', 'r'],
    ['buy_ship_fee', '매입\n배송비', 'r'], ['buy_total', '매입\n합계', 'r'], ['margin_amt', '최종\n마진', 'r'],
    ['margin_rate', '마진율', 'r'], ['margin_chk', '마진\n포함', 'c'], ['ad_chk', '광고', 'c'],
  ];

  const state = {
    tab: 'summary',
    summaryFilters: { year: '전체', month: '전체', market: '전체' },
    dashFilters: { year: '전체', month: '전체', market: '전체', search: '', hl_only: false, unpurchased_only: false, bundle_only: false },
    dashRows: [],
    dashSort: { col: 'order_date', dir: -1 },
    hlRows: [],
    fees: {},
    loadedTabs: new Set(),
  };

  const CUR_YEAR = new Date().getFullYear();
  const YEAR_OPTIONS = [
    { label: '작년', value: String(CUR_YEAR - 1) },
    { label: '올해', value: String(CUR_YEAR) },
    { label: '내년', value: String(CUR_YEAR + 1) },
  ];

  // ---------------- helpers ----------------
  const won = (n) => `${Math.round(n || 0).toLocaleString('ko-KR')} 원`;
  const num = (n) => Math.round(n || 0).toLocaleString('ko-KR');
  const pct = (n) => (n === null || n === undefined) ? '-' : `${Number(n).toFixed(1)}%`;
  const qs = (params) => Object.entries(params).filter(([, v]) => v !== '' && v !== false && v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v === true ? '1' : v)}`).join('&');

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.error || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }

  function toast(msg, type = '') {
    const wrap = document.getElementById('toast-wrap');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function renderChip(label, active, onClick, extraClass = '') {
    const b = document.createElement('button');
    b.className = `chip ${extraClass} ${active ? 'active' : ''}`.trim();
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function sep() { const d = document.createElement('div'); d.className = 'chip-sep'; return d; }

  function renderKPIRow(container, s, allTime) {
    const items = [
      ['accent-sales', '총 매출', won(s.sales)],
      ['accent-fee', '수수료 합계', won(s.fees)],
      ['accent-cost', '매입 합계', won(s.costs)],
      ['accent-margin', '순수익 마진', won(s.margin)],
      ['accent-rate', allTime ? '평균 마진율' : '마진율', pct(s.rate)],
    ];
    container.innerHTML = '';
    for (const [cls, label, value] of items) {
      const tile = document.createElement('div');
      tile.className = `kpi-tile ${cls}`;
      tile.innerHTML = `<div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>`;
      container.appendChild(tile);
    }
  }

  // ================= SUMMARY TAB =================
  function renderSummaryFilters() {
    const el = document.getElementById('summary-filters');
    el.innerHTML = '';
    const yearGroup = document.createElement('div'); yearGroup.className = 'filter-group';
    for (const y of YEAR_OPTIONS) {
      yearGroup.appendChild(renderChip(y.label, state.summaryFilters.year === y.value, () => {
        state.summaryFilters.year = (state.summaryFilters.year === y.value) ? '전체' : y.value;
        loadSummaryFiltered(); renderSummaryFilters();
      }));
    }
    el.appendChild(yearGroup); el.appendChild(sep());

    const monthGroup = document.createElement('div'); monthGroup.className = 'filter-group';
    for (let m = 1; m <= 12; m++) {
      const v = String(m);
      monthGroup.appendChild(renderChip(`${m}월`, state.summaryFilters.month === v, () => {
        state.summaryFilters.month = (state.summaryFilters.month === v) ? '전체' : v;
        loadSummaryFiltered(); renderSummaryFilters();
      }));
    }
    el.appendChild(monthGroup); el.appendChild(sep());

    const marketGroup = document.createElement('div'); marketGroup.className = 'filter-group';
    for (const mk of MARKETS) {
      marketGroup.appendChild(renderChip(mk, state.summaryFilters.market === mk, () => {
        state.summaryFilters.market = (state.summaryFilters.market === mk) ? '전체' : mk;
        loadSummaryFiltered(); renderSummaryFilters();
      }));
    }
    el.appendChild(marketGroup); el.appendChild(sep());
    el.appendChild(renderChip('초기화', false, () => {
      state.summaryFilters = { year: '전체', month: '전체', market: '전체' };
      loadSummaryFiltered(); renderSummaryFilters();
    }, 'ghost'));
  }

  async function loadSummaryAll() {
    const s = await api('/api/summary/all');
    renderKPIRow(document.getElementById('summary-kpi-all'), s, true);
    const tbody = document.querySelector('#market-summary-table tbody');
    tbody.innerHTML = '';
    for (const row of s.market_rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${row.market}</td><td>${num(row.sales)}</td><td>${num(row.fee)}</td>
        <td>${num(row.cost)}</td><td>${num(row.margin)}</td><td class="${row.rate > 0 ? 'rate-pos' : ''}">${pct(row.rate)}</td>`;
      tbody.appendChild(tr);
    }
    renderDonut(s.donut, s.market_rows);
  }

  function renderDonut(donutData, marketRows) {
    const wrap = document.getElementById('donut-wrap');
    wrap.innerHTML = '';
    const total = donutData.reduce((a, d) => a + d.value, 0);
    const size = 200, r = 74, cx = size / 2, cy = size / 2, circ = 2 * Math.PI * r;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', size); svg.setAttribute('height', size);
    svg.setAttribute('viewBox', `0 0 ${size} ${size}`);

    const bg = document.createElementNS(svg.namespaceURI, 'circle');
    bg.setAttribute('cx', cx); bg.setAttribute('cy', cy); bg.setAttribute('r', r);
    bg.setAttribute('fill', 'none'); bg.setAttribute('stroke', 'rgba(255,255,255,0.06)'); bg.setAttribute('stroke-width', 24);
    svg.appendChild(bg);

    let offset = 0;
    if (total > 0) {
      for (const d of donutData) {
        const frac = d.value / total;
        const len = Math.max(frac * circ - 2, 0);
        const circle = document.createElementNS(svg.namespaceURI, 'circle');
        circle.setAttribute('cx', cx); circle.setAttribute('cy', cy); circle.setAttribute('r', r);
        circle.setAttribute('fill', 'none');
        circle.setAttribute('stroke', MARKET_COLOR_HEX[d.market] || '#888');
        circle.setAttribute('stroke-width', 24);
        circle.setAttribute('stroke-dasharray', `${len} ${circ - len}`);
        circle.setAttribute('stroke-dashoffset', -offset);
        circle.setAttribute('transform', `rotate(-90 ${cx} ${cy})`);
        svg.appendChild(circle);
        offset += frac * circ;
      }
    }
    const label = document.createElementNS(svg.namespaceURI, 'text');
    label.setAttribute('x', cx); label.setAttribute('y', cy - 3);
    label.setAttribute('text-anchor', 'middle'); label.setAttribute('fill', 'var(--text-primary)');
    label.setAttribute('font-size', '13'); label.setAttribute('font-weight', '700');
    label.textContent = '순수익';
    svg.appendChild(label);
    const label2 = document.createElementNS(svg.namespaceURI, 'text');
    label2.setAttribute('x', cx); label2.setAttribute('y', cy + 16);
    label2.setAttribute('text-anchor', 'middle'); label2.setAttribute('fill', 'var(--text-secondary)');
    label2.setAttribute('font-size', '11');
    label2.textContent = `${num(total)}원`;
    svg.appendChild(label2);

    wrap.appendChild(svg);

    const legend = document.createElement('div'); legend.className = 'legend';
    for (const row of marketRows) {
      if (row.margin <= 0) continue;
      const item = document.createElement('div'); item.className = 'legend-item';
      const share = total > 0 ? (row.margin / total * 100).toFixed(1) : '0.0';
      item.innerHTML = `<span class="legend-swatch" style="background:${MARKET_COLOR_HEX[row.market]}"></span>
        <span class="legend-name">${row.market}</span><span class="legend-val">${share}%</span>`;
      legend.appendChild(item);
    }
    wrap.appendChild(legend);
  }

  async function loadSummaryFiltered() {
    const f = state.summaryFilters;
    const s = await api(`/api/summary/filtered?${qs(f)}`);
    renderKPIRow(document.getElementById('summary-kpi-filtered'), s, false);
    const title = (f.year !== '전체' || f.month !== '전체' || f.market !== '전체')
      ? `조건: [${f.year}]-[${f.month}]-[${f.market}]` : '현재 [전체 보기] 상태입니다.';
    document.getElementById('summary-filter-title').textContent = title;
    renderCalendar(s.daily, f);
  }

  function renderCalendar(daily, f) {
    const year = f.year !== '전체' ? parseInt(f.year, 10) : new Date().getFullYear();
    const month = f.month !== '전체' ? parseInt(f.month, 10) : (new Date().getMonth() + 1);
    document.getElementById('calendar-month-label').textContent = `${year}년 ${month}월`;

    const grid = document.getElementById('calendar-grid');
    grid.innerHTML = '';
    const dows = ['일', '월', '화', '수', '목', '금', '토'];
    dows.forEach((d, i) => {
      const el = document.createElement('div'); el.className = 'cal-dow';
      el.style.color = i === 0 ? '#f77' : (i === 6 ? '#6ea8fe' : '');
      el.textContent = d; grid.appendChild(el);
    });

    const firstDow = new Date(year, month - 1, 1).getDay();
    const lastDate = new Date(year, month, 0).getDate();
    for (let i = 0; i < firstDow; i++) {
      const c = document.createElement('div'); c.className = 'cal-cell empty'; grid.appendChild(c);
    }
    for (let d = 1; d <= lastDate; d++) {
      const dow = new Date(year, month - 1, d).getDay();
      const key = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const agg = daily[key];
      const cell = document.createElement('div'); cell.className = 'cal-cell';
      const dateCls = dow === 0 ? 'sun' : (dow === 6 ? 'sat' : '');
      cell.innerHTML = `<div class="cal-date ${dateCls}">${d}</div>` +
        (agg ? `<div class="cal-count">${agg.count}건</div><div class="cal-sales">${num(agg.sales)}</div><div class="cal-margin">${num(agg.margin)}</div>` : '');
      grid.appendChild(cell);
    }
  }

  // ================= DASHBOARD TAB =================
  function renderDashFilters() {
    const el = document.getElementById('dash-filters');
    el.innerHTML = '';
    const f = state.dashFilters;

    const yearGroup = document.createElement('div'); yearGroup.className = 'filter-group';
    for (const y of YEAR_OPTIONS) {
      yearGroup.appendChild(renderChip(y.label, f.year === y.value, () => {
        f.year = (f.year === y.value) ? '전체' : y.value; renderDashFilters(); loadDashOrders();
      }));
    }
    el.appendChild(yearGroup); el.appendChild(sep());

    const monthGroup = document.createElement('div'); monthGroup.className = 'filter-group';
    for (let m = 1; m <= 12; m++) {
      const v = String(m);
      monthGroup.appendChild(renderChip(`${m}월`, f.month === v, () => {
        f.month = (f.month === v) ? '전체' : v; renderDashFilters(); loadDashOrders();
      }));
    }
    el.appendChild(monthGroup); el.appendChild(sep());

    const marketGroup = document.createElement('div'); marketGroup.className = 'filter-group';
    for (const mk of MARKETS) {
      marketGroup.appendChild(renderChip(mk, f.market === mk, () => {
        f.market = (f.market === mk) ? '전체' : mk; renderDashFilters(); loadDashOrders();
      }));
    }
    el.appendChild(marketGroup); el.appendChild(sep());

    const toggleGroup = document.createElement('div'); toggleGroup.className = 'filter-group';
    toggleGroup.appendChild(renderChip('HL주문건', f.hl_only, () => {
      f.hl_only = !f.hl_only; renderDashFilters(); loadDashOrders();
    }, 'toggle'));
    toggleGroup.appendChild(renderChip('미매입(취소제외)', f.unpurchased_only, () => {
      f.unpurchased_only = !f.unpurchased_only; renderDashFilters(); loadDashOrders();
    }, 'toggle'));
    toggleGroup.appendChild(renderChip('합배송', f.bundle_only, () => {
      f.bundle_only = !f.bundle_only; renderDashFilters(); loadDashOrders();
    }, 'toggle'));
    el.appendChild(toggleGroup); el.appendChild(sep());

    el.appendChild(renderChip('필터 초기화', false, () => {
      state.dashFilters = { year: '전체', month: '전체', market: '전체', search: '', hl_only: false, unpurchased_only: false, bundle_only: false };
      document.getElementById('dash-search').value = '';
      renderDashFilters(); loadDashOrders();
    }, 'ghost'));
  }

  function rowClass(row) {
    if (row.margin_chk === 'Y') return '';
    if (row.is_returned) return 'row-return';
    if (row.is_cancelled) return 'row-cancel';
    if (row.is_included && row.margin_amt !== null && row.margin_amt < 0) return 'row-neg';
    if (row.is_bundled) {
      let h = 0; for (const c of row.group_id) h = (h * 31 + c.charCodeAt(0)) % 1000;
      return (h % 2 === 0) ? 'row-bundle-a' : 'row-bundle-b';
    }
    return '';
  }

  function cellValue(row, key) {
    switch (key) {
      case 'order_amt': case 'ship_fee': case 'add_ship_fee': case 'fee_amt': case 'settle_amt':
      case 'buy_cost': case 'buy_ship_fee': case 'buy_total':
        return num(row[key]);
      case 'margin_amt':
        return row.is_included ? num(row.margin_amt) : `<span class="pill excluded">${row.margin_label}</span>`;
      case 'margin_rate':
        return row.is_included ? pct(row.margin_rate) : '-';
      case 'buy_status': {
        const v = row.buy_status || '-';
        return v.includes('[HL]') ? `<span class="pill hl">${v}</span>` : v;
      }
      default:
        return row[key] || '-';
    }
  }

  function renderDashTableHead() {
    const thead = document.querySelector('#dash-table thead');
    const tr = document.createElement('tr');
    for (const [key, label, align] of COLS) {
      const th = document.createElement('th');
      th.className = align === 'l' ? 'al-l' : '';
      th.innerHTML = label.replace('\n', '<br>');
      if (key !== 'margin_chk' && key !== 'ad_chk') {
        th.addEventListener('click', () => {
          if (state.dashSort.col === key) state.dashSort.dir *= -1;
          else { state.dashSort.col = key; state.dashSort.dir = 1; }
          renderDashTableBody();
        });
      }
      tr.appendChild(th);
    }
    thead.innerHTML = ''; thead.appendChild(tr);
  }

  function sortedDashRows() {
    const { col, dir } = state.dashSort;
    const arr = state.dashRows.slice();
    arr.sort((a, b) => {
      let av = a[col], bv = b[col];
      if (typeof av === 'string' && typeof bv === 'string') {
        const an = parseFloat(av.replace(/,/g, '')), bn = parseFloat(bv.replace(/,/g, ''));
        if (!isNaN(an) && !isNaN(bn) && av.match(/^[\d.,%\-]+$/) && bv.match(/^[\d.,%\-]+$/)) { av = an; bv = bn; }
      }
      if (av === null || av === undefined) av = -Infinity;
      if (bv === null || bv === undefined) bv = -Infinity;
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
    return arr;
  }

  function renderDashTableBody() {
    const tbody = document.querySelector('#dash-table tbody');
    tbody.innerHTML = '';
    const rows = sortedDashRows();
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      const tr = document.createElement('tr');
      tr.className = rowClass(row);
      tr.dataset.orderId = row.order_id;
      for (const [key, , align] of COLS) {
        const td = document.createElement('td');
        td.className = align === 'l' ? 'al-l' : '';
        if (key === 'margin_chk') {
          td.innerHTML = `<input type="checkbox" class="chk-box margin" ${row.is_included ? 'checked' : ''}>`;
        } else if (key === 'ad_chk') {
          td.innerHTML = row.is_toss ? `<input type="checkbox" class="chk-box ad" ${row.ad_chk === 'Y' ? 'checked' : ''}>` : '-';
        } else {
          td.innerHTML = cellValue(row, key);
        }
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
    document.getElementById('dash-count').textContent = `${rows.length.toLocaleString('ko-KR')}건`;
  }

  async function toggleOrder(orderId, field, value) {
    const f = state.dashFilters;
    const data = await api(`/api/orders/${encodeURIComponent(orderId)}/toggle?${qs(f)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ field, value }),
    });
    const idx = state.dashRows.findIndex(r => r.order_id === orderId);
    if (idx >= 0 && data.row) state.dashRows[idx] = data.row;
    renderKPIRow(document.getElementById('dash-kpi'), data.live_summary, false);
    // update the single row in place
    const tr = document.querySelector(`#dash-table tbody tr[data-order-id="${CSS.escape(orderId)}"]`);
    if (tr && data.row) {
      tr.className = rowClass(data.row);
      COLS.forEach(([key, , align], i) => {
        const td = tr.children[i];
        if (key === 'margin_chk') { td.innerHTML = `<input type="checkbox" class="chk-box margin" ${data.row.is_included ? 'checked' : ''}>`; }
        else if (key === 'ad_chk') { td.innerHTML = data.row.is_toss ? `<input type="checkbox" class="chk-box ad" ${data.row.ad_chk === 'Y' ? 'checked' : ''}>` : '-'; }
        else { td.innerHTML = cellValue(data.row, key); }
      });
    }
  }

  document.addEventListener('change', (e) => {
    if (e.target.matches('.chk-box.margin')) {
      const tr = e.target.closest('tr');
      toggleOrder(tr.dataset.orderId, 'margin_chk', e.target.checked ? 'Y' : 'N').catch(err => toast(err.message, 'err'));
    } else if (e.target.matches('.chk-box.ad')) {
      const tr = e.target.closest('tr');
      toggleOrder(tr.dataset.orderId, 'ad_chk', e.target.checked ? 'Y' : 'N').catch(err => toast(err.message, 'err'));
    }
  });

  let searchDebounce;
  document.getElementById('dash-search').addEventListener('input', (e) => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.dashFilters.search = e.target.value;
      loadDashOrders();
    }, 250);
  });

  async function loadDashOrders() {
    const data = await api(`/api/orders?${qs(state.dashFilters)}`);
    state.dashRows = data.rows;
    renderKPIRow(document.getElementById('dash-kpi'), data.live_summary, false);
    renderDashTableBody();
  }

  // ================= UPLOAD TAB =================
  async function loadSettingsLinks() {
    const s = await api('/api/settings');
    document.getElementById('link-dapalza').href = s.dapalza_url || '#';
    document.getElementById('link-ownerclan').href = s.ownerclan_url || '#';
    document.getElementById('input-dapalza-url').value = s.dapalza_url || '';
    document.getElementById('input-ownerclan-url').value = s.ownerclan_url || '';
  }

  document.getElementById('btn-edit-links').addEventListener('click', () => {
    const row = document.getElementById('settings-row');
    row.style.display = row.style.display === 'none' ? 'flex' : 'none';
  });
  document.getElementById('btn-save-links').addEventListener('click', async () => {
    const dapalza_url = document.getElementById('input-dapalza-url').value.trim();
    const ownerclan_url = document.getElementById('input-ownerclan-url').value.trim();
    await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dapalza_url, ownerclan_url }),
    });
    await loadSettingsLinks();
    document.getElementById('settings-row').style.display = 'none';
    toast('바로가기 주소가 저장되었습니다.', 'ok');
  });

  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault(); dropzone.classList.remove('drag');
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) uploadFiles(fileInput.files);
    fileInput.value = '';
  });

  async function uploadFiles(fileList) {
    const fd = new FormData();
    for (const f of fileList) fd.append('files', f);
    const log = document.getElementById('upload-log');
    log.innerHTML = '<div class="hint">업로드 처리 중...</div>';
    try {
      const data = await api('/api/upload', { method: 'POST', body: fd });
      log.innerHTML = '';
      for (const r of data.results) {
        const item = document.createElement('div');
        item.className = `upload-log-item ${r.error ? 'err' : ''}`;
        item.innerHTML = r.error
          ? `<span>${r.filename}</span><span>${r.error}</span>`
          : `<span>${r.filename} <span class="tag">(${r.type})</span></span><span>신규 ${r.inserted}건 · 갱신 ${r.updated}건</span>`;
        log.appendChild(item);
      }
      toast('업로드 처리가 완료되었습니다.', 'ok');
      state.loadedTabs.delete('dashboard'); state.loadedTabs.delete('summary');
      if (state.tab === 'dashboard') loadDashOrders();
      if (state.tab === 'summary') { loadSummaryAll(); loadSummaryFiltered(); }
    } catch (err) {
      log.innerHTML = `<div class="upload-log-item err">${err.message}</div>`;
    }
  }

  document.getElementById('btn-hl-parse').addEventListener('click', async () => {
    const text = document.getElementById('hl-textarea').value;
    const data = await api('/api/hl/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
    });
    state.hlRows = data.rows;
    renderHlTable();
  });

  function renderHlTable() {
    const tbody = document.querySelector('#hl-table tbody');
    tbody.innerHTML = '';
    state.hlRows.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${r.order_date}</td><td>${r.hl_order_no}</td><td>${r.prod_name}</td>
        <td>${r.buy_status}</td><td>${num(r.buy_cost)}</td><td>${num(r.ship_fee)}</td>
        <td><input type="text" data-i="${i}" placeholder="상품주문번호 입력"></td>`;
      tbody.appendChild(tr);
    });
  }

  document.getElementById('btn-hl-save').addEventListener('click', async () => {
    const inputs = document.querySelectorAll('#hl-table input');
    const rows = state.hlRows.map((r, i) => ({
      hl_order_no: r.hl_order_no, buy_status: r.buy_status, buy_cost: r.buy_cost, ship_fee: r.ship_fee,
      manual_order_id: inputs[i] ? inputs[i].value : '',
    }));
    const data = await api('/api/hl/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }),
    });
    if (data.errors.length) {
      toast(`${data.updated}건 저장 성공. 실패 ${data.errors.length}건 (5자리 미만 규칙 위반)`, 'err');
    } else if (data.updated > 0) {
      toast(`${data.updated}건 매입 데이터로 반영되었습니다.`, 'ok');
    } else {
      toast('저장할 항목이 없습니다.', 'err');
    }
    state.loadedTabs.delete('dashboard'); state.loadedTabs.delete('summary');
  });

  // ================= FEES TAB =================
  function renderFeeTable(fees) {
    const tbody = document.querySelector('#fee-table tbody');
    tbody.innerHTML = '';
    for (const mk of MARKETS) {
      const v = fees[mk] || ['0', '0', '0', '0', '0', '0'];
      const tr = document.createElement('tr');
      tr.dataset.market = mk;
      tr.innerHTML = `<td>${mk}</td>` + v.map((val, i) => `<td><input data-i="${i}" value="${val}"></td>`).join('');
      tbody.appendChild(tr);
    }
  }

  function collectFeeTable() {
    const out = {};
    document.querySelectorAll('#fee-table tbody tr').forEach(tr => {
      const inputs = tr.querySelectorAll('input');
      const vals = Array.from(inputs).map(inp => {
        const v = inp.value.trim();
        if (String(v).includes('자동')) return v;
        const f = parseFloat(v);
        return isNaN(f) ? 0 : f;
      });
      out[tr.dataset.market] = vals;
    });
    return out;
  }

  document.getElementById('btn-fee-reset').addEventListener('click', async () => {
    const defaults = await api('/api/fees/default');
    renderFeeTable(defaults);
    toast('조사된 팩트 수수료율이 반영되었습니다. [저장]을 눌러야 적용됩니다.');
  });

  document.getElementById('btn-fee-save').addEventListener('click', async () => {
    const fees = collectFeeTable();
    await api('/api/fees', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fees) });
    toast('수수료율이 영구 저장되었습니다.', 'ok');
    state.loadedTabs.clear();
  });

  async function loadFees() {
    const fees = await api('/api/fees');
    renderFeeTable(fees);
  }

  // ================= TAB SWITCHING =================
  function activateTab(name) {
    state.tab = name;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `tab-${name}`));

    if (state.loadedTabs.has(name)) return;
    state.loadedTabs.add(name);
    if (name === 'summary') { renderSummaryFilters(); loadSummaryAll(); loadSummaryFiltered(); }
    if (name === 'dashboard') { renderDashFilters(); renderDashTableHead(); loadDashOrders(); }
    if (name === 'upload') { loadSettingsLinks(); }
    if (name === 'fees') { loadFees(); }
  }

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  activateTab('summary');
})();
