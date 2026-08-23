/* 이유상점 Margin Board - frontend */
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

  // [key, label(줄바꿈은 \n), align('l'=left/'r'=right/'c'=center-checkbox), default width(%)]
  const COLS = [
    ['bundle_no', '합배송\n코드', 'l', 4.7], ['order_id', '원장\n주문코드', 'l', 5.8],
    ['market', '마켓', 'l', 3.3], ['sell_status', '주문\n상태', 'l', 3.6], ['buy_status', '매입\n상태', 'l', 4.3],
    ['order_date', '주문\n일시', 'l', 6.5], ['prod_id', '상품ID', 'l', 4.3], ['prod_name', '상품명', 'l', 9.4],
    ['qty', '수량', 'r', 2.5], ['order_amt', '주문\n금액', 'r', 4.3], ['discount_amt', '할인액', 'r', 3.3],
    ['ship_fee', '배송비', 'r', 3.3],
    ['add_ship_fee', '추가\n배송비', 'r', 3.6], ['fee_rate_display', '수수료율', 'r', 4.0], ['fee_amt', '마켓\n수수료', 'r', 4.0],
    ['settle_amt', '정산\n예정액', 'r', 4.3], ['settle_amt_dpj', '실정산가', 'r', 3.6],
    ['vendor_prod_id', '판매사\n상품코드', 'l', 4.7],
    ['stock_status', '재고\n상태', 'c', 3.0], ['est_buy_price', '매입\n예상가', 'r', 3.6],
    ['order_chk', '발주', 'c', 2.6], ['buy_cost', '매입가', 'r', 3.6],
    ['buy_ship_fee', '매입\n배송비', 'r', 3.6], ['buy_total', '매입\n합계', 'r', 3.6], ['margin_amt', '최종\n마진', 'r', 4.0],
    ['margin_rate', '마진율', 'r', 3.3], ['margin_chk', '마진\n포함', 'c', 3.3], ['ad_chk', '광고', 'c', 2.9],
  ];

  const COL_WIDTH_STORAGE_KEY = 'marginboard_dash_col_widths_v1';
  function loadColWidths() {
    try {
      const saved = JSON.parse(localStorage.getItem(COL_WIDTH_STORAGE_KEY) || 'null');
      if (saved && saved.length === COLS.length) return saved;
    } catch (_) {}
    return COLS.map(c => c[3]);
  }
  function saveColWidths(widths) {
    try { localStorage.setItem(COL_WIDTH_STORAGE_KEY, JSON.stringify(widths)); } catch (_) {}
  }
  const colWidths = loadColWidths();

  const NOW = new Date();
  const state = {
    tab: 'summary',
    calState: { year: NOW.getFullYear(), month: NOW.getMonth() + 1 },
    dashFilters: { year: '전체', month: '전체', market: '전체', search: '', hl_only: false, unpurchased_only: false, bundle_only: false, ready_only: false, issue_only: false },
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
    // 잠깐 떴다 사라지는 게 너무 빨라서 못 읽고 지나간다는 지적이 있었다 -
    // 표시시간을 늘리고, 클릭하면 바로 닫히게 하고, 마우스를 올려놓는 동안은
    // 안 사라지게(hover하면 타이머 정지) 해서 원하는 만큼 읽을 수 있게 한다.
    const wrap = document.getElementById('toast-wrap');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    el.style.cursor = 'pointer';
    el.title = '클릭하면 닫힙니다';
    wrap.appendChild(el);
    let timer = null;
    const remove = () => { if (el.parentNode) el.remove(); };
    const start = () => { timer = setTimeout(remove, 20000); };
    const stop = () => { if (timer) clearTimeout(timer); };
    el.addEventListener('click', remove);
    el.addEventListener('mouseenter', stop);
    el.addEventListener('mouseleave', start);
    start();
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
    const size = 220, r = 82, cx = size / 2, cy = size / 2, circ = 2 * Math.PI * r;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', size); svg.setAttribute('height', size);
    svg.setAttribute('viewBox', `0 0 ${size} ${size}`);

    const bg = document.createElementNS(svg.namespaceURI, 'circle');
    bg.setAttribute('cx', cx); bg.setAttribute('cy', cy); bg.setAttribute('r', r);
    bg.setAttribute('fill', 'none'); bg.setAttribute('stroke', 'rgba(255,255,255,0.06)'); bg.setAttribute('stroke-width', 27);
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
        circle.setAttribute('stroke-width', 27);
        circle.setAttribute('stroke-dasharray', `${len} ${circ - len}`);
        circle.setAttribute('stroke-dashoffset', -offset);
        circle.setAttribute('transform', `rotate(-90 ${cx} ${cy})`);
        svg.appendChild(circle);
        offset += frac * circ;
      }
    }
    const label = document.createElementNS(svg.namespaceURI, 'text');
    label.setAttribute('x', cx); label.setAttribute('y', cy - 6);
    label.setAttribute('text-anchor', 'middle'); label.setAttribute('fill', 'var(--text-secondary)');
    label.setAttribute('font-size', '12'); label.setAttribute('font-weight', '700');
    label.textContent = '순수익';
    svg.appendChild(label);
    const label2 = document.createElementNS(svg.namespaceURI, 'text');
    label2.setAttribute('x', cx); label2.setAttribute('y', cy + 16);
    label2.setAttribute('text-anchor', 'middle'); label2.setAttribute('fill', 'var(--text-primary)');
    label2.setAttribute('font-size', '17'); label2.setAttribute('font-weight', '700');
    label2.textContent = total >= 10000 ? `${Math.round(total / 10000)}만원` : `${num(total)}원`;
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

  async function loadCalendar() {
    const { year, month } = state.calState;
    const s = await api(`/api/summary/filtered?${qs({ year: String(year), month: String(month), market: '전체' })}`);
    renderCalendar(s.daily, year, month);
  }

  function renderCalendar(daily, year, month) {
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
    // 달마다 주 수가 달라 카드 높이가 흔들리지 않도록 항상 6주(42칸)로 고정
    const totalCells = firstDow + lastDate;
    for (let i = totalCells; i < 42; i++) {
      const c = document.createElement('div'); c.className = 'cal-cell empty'; grid.appendChild(c);
    }
  }

  function navigateCalendarMonth(delta) {
    let { year, month } = state.calState;
    month += delta;
    if (month < 1) { month = 12; year -= 1; }
    else if (month > 12) { month = 1; year += 1; }
    state.calState = { year, month };
    loadCalendar();
  }
  document.getElementById('cal-prev').addEventListener('click', () => navigateCalendarMonth(-1));
  document.getElementById('cal-next').addEventListener('click', () => navigateCalendarMonth(1));

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
    const selectExclusive = (name) => {
      f.hl_only = name === 'hl_only' ? !f.hl_only : false;
      f.unpurchased_only = name === 'unpurchased_only' ? !f.unpurchased_only : false;
      f.bundle_only = name === 'bundle_only' ? !f.bundle_only : false;
      renderDashFilters(); loadDashOrders();
    };
    toggleGroup.appendChild(renderChip('HL주문건', f.hl_only, () => selectExclusive('hl_only'), 'toggle'));
    toggleGroup.appendChild(renderChip('미매입', f.unpurchased_only, () => selectExclusive('unpurchased_only'), 'toggle'));
    toggleGroup.appendChild(renderChip('합배송', f.bundle_only, () => selectExclusive('bundle_only'), 'toggle'));
    el.appendChild(toggleGroup); el.appendChild(sep());

    el.appendChild(renderChip('필터 초기화', false, () => {
      state.dashFilters = { year: '전체', month: '전체', market: '전체', search: '', hl_only: false, unpurchased_only: false, bundle_only: false, ready_only: false, issue_only: false };
      document.getElementById('dash-search').value = '';
      renderDashFilters(); loadDashOrders();
    }, 'ghost'));

    // READY(신규주문+배송준비)/ISSUE(취소요청+반품요청+주문확인) - 헤더
    // 오른쪽 DPJ/OC/STOCK 옆에 별도로 둔 상태 필터. 서로는 배타적이지만
    // 위 토글 그룹(HL주문건/미매입/합배송)과는 독립적으로 같이 켤 수 있다.
    const statusEl = document.getElementById('dash-status-toggle');
    statusEl.innerHTML = '';
    const selectStatusExclusive = (name) => {
      f.ready_only = name === 'ready_only' ? !f.ready_only : false;
      f.issue_only = name === 'issue_only' ? !f.issue_only : false;
      renderDashFilters(); loadDashOrders();
    };
    statusEl.appendChild(renderChip('READY', f.ready_only, () => selectStatusExclusive('ready_only'), 'toggle'));
    statusEl.appendChild(renderChip('ISSUE', f.issue_only, () => selectStatusExclusive('issue_only'), 'toggle'));
  }

  // bundleCls: 화면에 표시된 순서상 합배송 그룹이 바뀔 때마다 번갈아 계산된
  // row-bundle-a/row-bundle-b (renderDashTableBody에서 계산해 row._bundleCls에 저장).
  // 합배송 행은 같은 묶음이면 무조건 같은 색이 나와야 하므로, 취소/반품/마진
  // 음수 같은 상태보다 우선한다(상태는 매입상태·최종마진 텍스트로 이미 표시됨).
  function rowClass(row, bundleCls) {
    if (row.is_bundled) return bundleCls || row._bundleCls || '';
    if (row.margin_chk === 'Y') return '';
    if (row.is_returned) return 'row-return';
    if (row.is_cancelled) return 'row-cancel';
    if (row.is_included && row.margin_amt !== null && row.margin_amt < 0) return 'row-neg';
    return '';
  }

  function cellValue(row, key) {
    switch (key) {
      case 'order_amt': case 'ship_fee': case 'add_ship_fee': case 'fee_amt': case 'settle_amt':
      case 'buy_cost': case 'buy_ship_fee': case 'buy_total': case 'discount_amt':
        return num(row[key]);
      case 'settle_amt_dpj':
        // 다팔자가 아닌 소스(TOSS 파일 직접 업로드 등)는 이 값이 아예 없을 수
        // 있다 - 0원으로 보이면 실제로 0원 정산된 것처럼 오해할 수 있어서,
        // 값이 없을 땐 0이 아니라 '-'로 구분해서 보여준다.
        return row.settle_amt_dpj ? num(row.settle_amt_dpj) : '-';
      case 'est_buy_price':
        // STOCK 확인을 아직 안 했거나 확인실패면 값이 비어있다 - 0원으로
        // 보이면 실제로 무료인 것처럼 오해할 수 있어 '-'로 구분한다.
        return row.est_buy_price ? num(row.est_buy_price) : '-';
      case 'margin_amt':
        return row.is_included ? num(row.margin_amt) : `<span class="pill excluded">${row.margin_label}</span>`;
      case 'margin_rate':
        return row.is_included ? pct(row.margin_rate) : '-';
      case 'buy_status': {
        const v = row.buy_status || '-';
        return v.includes('[HL]') ? `<span class="pill hl">${v}</span>` : v;
      }
      case 'stock_status': {
        const v = row.stock_status || '';
        if (v === '정상') return `<span class="pill instock">판매중</span>`;
        if (v === '품절') return `<span class="pill soldout">품절</span>`;
        if (v === '확인실패') return `<span class="pill unchecked" title="자동 확인에 실패했습니다">확인실패</span>`;
        return '-';
      }
      default:
        return row[key] || '-';
    }
  }

  function applyColWidths() {
    const cols = document.querySelectorAll('#dash-table colgroup col');
    cols.forEach((c, i) => { c.style.width = `${colWidths[i]}%`; });
  }

  function renderDashColgroup() {
    const table = document.getElementById('dash-table');
    let colgroup = table.querySelector('colgroup');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      table.insertBefore(colgroup, table.firstChild);
    }
    colgroup.innerHTML = '';
    COLS.forEach(() => colgroup.appendChild(document.createElement('col')));
    applyColWidths();
  }

  // 리사이즈 직후 발생하는 합성 click(정렬 오발동) 억제용 타임스탬프
  let resizeJustEndedAt = 0;

  // pointer capture를 써서 핸들 자신에게만 이벤트를 묶는다 - 예전 document
  // 레벨 mousemove/mouseup 리스너 방식은 mouseup을 놓치면(창 밖에서 놓거나,
  // 다른 엘리먼트가 가로채는 경우) 리스너가 정리되지 않고 계속 쌓여서,
  // 이후 스크롤/마우스 이동만 해도 매번 재계산이 실행되며 페이지가 멈추는
  // 원인이 됐다. pointer capture는 pointerup/cancel 시 자동 해제되고
  // 리스너도 핸들 엘리먼트에만 붙어 있어 이런 누수가 구조적으로 불가능하다.
  function startColResize(e, index, handleEl) {
    e.preventDefault();
    handleEl.setPointerCapture(e.pointerId);
    const table = document.getElementById('dash-table');
    const tableWidth = table.getBoundingClientRect().width;
    const startX = e.clientX;
    const startA = colWidths[index], startB = colWidths[index + 1];
    const MIN = 2.2;
    let pendingDx = null;
    let rafId = null;

    function apply() {
      rafId = null;
      if (pendingDx === null) return;
      const deltaPct = (pendingDx / tableWidth) * 100;
      let a = startA + deltaPct, b = startB - deltaPct;
      if (a < MIN) { b -= (MIN - a); a = MIN; }
      if (b < MIN) { a -= (MIN - b); b = MIN; }
      colWidths[index] = a; colWidths[index + 1] = b;
      applyColWidths();
    }
    function onMove(ev) {
      if (ev.pointerId !== e.pointerId) return;
      pendingDx = ev.clientX - startX;
      if (rafId === null) rafId = requestAnimationFrame(apply);
    }
    function onUp(ev) {
      if (ev.pointerId !== e.pointerId) return;
      handleEl.removeEventListener('pointermove', onMove);
      handleEl.removeEventListener('pointerup', onUp);
      handleEl.removeEventListener('pointercancel', onUp);
      if (rafId !== null) cancelAnimationFrame(rafId);
      saveColWidths(colWidths);
      resizeJustEndedAt = Date.now();
    }
    handleEl.addEventListener('pointermove', onMove);
    handleEl.addEventListener('pointerup', onUp);
    handleEl.addEventListener('pointercancel', onUp);
  }

  function renderDashTableHead() {
    renderDashColgroup();
    const thead = document.querySelector('#dash-table thead');
    const tr = document.createElement('tr');
    COLS.forEach(([key, label, align], i) => {
      const th = document.createElement('th');
      th.className = align === 'l' ? 'al-l' : '';
      th.innerHTML = label.replace('\n', '<br>');
      if (key !== 'margin_chk' && key !== 'ad_chk' && key !== 'order_chk') {
        th.addEventListener('click', (e) => {
          if (e.target.classList.contains('col-resize-handle')) return;
          if (Date.now() - resizeJustEndedAt < 250) return; // 리사이즈 직후 합성 click 무시
          if (state.dashSort.col === key) state.dashSort.dir *= -1;
          else { state.dashSort.col = key; state.dashSort.dir = 1; }
          renderDashTableBody();
        });
      }
      if (i < COLS.length - 1) {
        const handle = document.createElement('span');
        handle.className = 'col-resize-handle';
        handle.addEventListener('pointerdown', (e) => { e.stopPropagation(); startColResize(e, i, handle); });
        th.appendChild(handle);
      }
      tr.appendChild(th);
    });
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
    let lastGroupId = null, bundleToggle = 0;
    for (const row of rows) {
      let bundleCls = '';
      if (row.is_bundled) {
        if (row.group_id !== lastGroupId) { bundleToggle = 1 - bundleToggle; lastGroupId = row.group_id; }
        bundleCls = bundleToggle === 0 ? 'row-bundle-a' : 'row-bundle-b';
      } else {
        lastGroupId = null;
      }
      row._bundleCls = bundleCls;
      const tr = document.createElement('tr');
      tr.className = rowClass(row, bundleCls);
      tr.dataset.orderId = row.order_id;
      for (const [key, , align] of COLS) {
        const td = document.createElement('td');
        td.className = align === 'l' ? 'al-l' : '';
        if (key === 'margin_chk') {
          td.innerHTML = `<input type="checkbox" class="chk-box margin" ${row.is_included ? 'checked' : ''}>`;
        } else if (key === 'ad_chk') {
          td.innerHTML = row.is_toss ? `<input type="checkbox" class="chk-box ad" ${row.ad_chk === 'Y' ? 'checked' : ''}>` : '-';
        } else if (key === 'order_chk') {
          td.innerHTML = `<input type="checkbox" class="chk-box order" ${row.order_chk === 'Y' ? 'checked' : ''}>`;
        } else {
          td.innerHTML = cellValue(row, key);
          if (align === 'l' && row[key]) td.title = String(row[key]);
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
    if (idx >= 0 && data.row) {
      data.row._bundleCls = state.dashRows[idx]._bundleCls; // 그룹 소속은 안 바뀌므로 이전 값 유지
      state.dashRows[idx] = data.row;
    }
    renderKPIRow(document.getElementById('dash-kpi'), data.live_summary, false);
    // update the single row in place
    const tr = document.querySelector(`#dash-table tbody tr[data-order-id="${CSS.escape(orderId)}"]`);
    if (tr && data.row) {
      tr.className = rowClass(data.row, data.row._bundleCls);
      COLS.forEach(([key, , align], i) => {
        const td = tr.children[i];
        if (key === 'margin_chk') { td.innerHTML = `<input type="checkbox" class="chk-box margin" ${data.row.is_included ? 'checked' : ''}>`; }
        else if (key === 'ad_chk') { td.innerHTML = data.row.is_toss ? `<input type="checkbox" class="chk-box ad" ${data.row.ad_chk === 'Y' ? 'checked' : ''}>` : '-'; }
        else if (key === 'order_chk') { td.innerHTML = `<input type="checkbox" class="chk-box order" ${data.row.order_chk === 'Y' ? 'checked' : ''}>`; }
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
    } else if (e.target.matches('.chk-box.order')) {
      const tr = e.target.closest('tr');
      toggleOrder(tr.dataset.orderId, 'order_chk', e.target.checked ? 'Y' : 'N').catch(err => toast(err.message, 'err'));
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

  function renderCollectLog(logId, data) {
    const log = document.getElementById(logId);
    log.innerHTML = '';
    for (const line of (data.log || [])) {
      const item = document.createElement('div');
      item.className = 'upload-log-item';
      item.innerHTML = `<span>${line}</span>`;
      log.appendChild(item);
    }
  }

  document.getElementById('btn-dapalza-collect').addEventListener('click', async () => {
    const btn = document.getElementById('btn-dapalza-collect');
    const log = document.getElementById('collect-log');
    btn.disabled = true;
    btn.textContent = '⏳';
    log.innerHTML = '<div class="hint">다팔자 자동화를 시작합니다... (다팔자 창을 건드리지 마세요)</div>';
    // DPJ는 몇 분씩 걸릴 수 있는데, 예전엔 다 끝나야만 로그가 한 번에
    // 통째로 보여서 그동안 화면이 그대로라 "멈췄다"는 오해를 계속 샀다 -
    // 진행 중에도 1.5초마다 지금까지 쌓인 로그를 물어봐서 실시간으로
    // 보여준다(요청이 끝나면 아래 finally에서 멈춘다).
    const progressTimer = setInterval(async () => {
      try {
        const p = await api('/api/dapalza/progress');
        if (p.log && p.log.length) renderCollectLog('collect-log', p);
      } catch (_) { /* 진행상황 조회 실패는 무시 - 본 요청 결과가 우선 */ }
    }, 1500);
    try {
      const data = await api('/api/dapalza/collect', { method: 'POST' });
      if (data.ok && data.upload && !data.upload.error) {
        // 성공했을 때는 로그를 지저분하게 남기지 않고 깔끔하게 비운다 -
        // 실패했을 때만 원인 파악용으로 로그를 보여준다. 옵션/합배송코드가
        // 실제로 몇 건이나 채워졌는지는 로그 대신 완료 토스트에 짧게
        // 같이 보여준다(다팔자 파일의 옵션 컬럼을 제대로 찾고 있는지
        // 매번 확인할 수 있게).
        const u = data.upload;
        const missingCols = u.raw_columns_found
          ? Object.entries(u.raw_columns_found).filter(([, found]) => !found).map(([name]) => name)
          : [];
        if (missingCols.length) {
          // 할인액/실정산가/연락처/우편번호가 계속 빈 값으로 보이는 게
          // 파싱 문제인지 이번 파일에 그 컬럼 자체가 없는 건지 바로
          // 알 수 있게, 컬럼이 없을 땐 로그를 지우지 않고 보여준다.
          renderCollectLog('collect-log', data);
        } else {
          log.innerHTML = '';
        }
        const extra = (typeof u.total_rows === 'number')
          ? ` (옵션 ${u.option_filled || 0}/${u.total_rows}건, 합배송코드 ${u.bundle_filled || 0}/${u.total_rows}건, 주문상태 빈값 ${u.status_blank || 0}건)`
          : '';
        const colWarn = missingCols.length ? ` ※ 이번 파일에 없는 컬럼: ${missingCols.join(', ')}` : '';
        toast(`다팔자 자동 수집 및 반영이 완료되었습니다.${extra}${colWarn}`, missingCols.length ? 'err' : 'ok');
        state.loadedTabs.delete('summary');
        loadDashOrders();
        // 신규주문이 있으면(사용자 요청) STOCK(재고확인)을 이어서 자동으로 실행한다.
        if (data.has_new_orders) {
          runCheckStock(true);
        }
      } else if (data.ok && data.upload && data.upload.error) {
        renderCollectLog('collect-log', data);
        toast(`파일은 저장했지만 반영 중 오류: ${data.upload.error}`, 'err');
      } else {
        renderCollectLog('collect-log', data);
        toast('자동 수집이 중간에 멈췄어요. 아래 로그를 확인해주세요.', 'err');
      }
    } catch (e) {
      log.innerHTML = `<div class="upload-log-item err"><span>${e.message}</span></div>`;
      toast('자동 수집 요청이 실패했습니다.', 'err');
    } finally {
      clearInterval(progressTimer);
    }
    btn.disabled = false;
    btn.textContent = 'DPJ';
  });

  document.getElementById('btn-ownerclan-login').addEventListener('click', async () => {
    const btn = document.getElementById('btn-ownerclan-login');
    const log = document.getElementById('ownerclan-collect-log');
    btn.disabled = true;
    btn.textContent = '⏳ 로그인 창 대기 중... (브라우저 창에서 로그인해주세요)';
    log.innerHTML = '<div class="hint">로그인용 브라우저 창을 여는 중...</div>';
    try {
      const data = await api('/api/ownerclan/setup_login', { method: 'POST' });
      renderCollectLog('ownerclan-collect-log', data);
      toast(data.ok ? '오너클랜 로그인이 저장됐습니다.' : '로그인 완료를 확인 못했어요. 아래 로그를 확인해주세요.', data.ok ? 'ok' : 'err');
    } catch (e) {
      log.innerHTML = `<div class="upload-log-item err"><span>${e.message}</span></div>`;
      toast('로그인 설정 요청이 실패했습니다.', 'err');
    }
    btn.disabled = false;
    btn.textContent = '🔑 오너클랜 로그인 설정 (최초 1회)';
  });

  document.getElementById('btn-ownerclan-collect').addEventListener('click', async () => {
    const btn = document.getElementById('btn-ownerclan-collect');
    const log = document.getElementById('collect-log');
    btn.disabled = true;
    btn.textContent = '⏳';
    log.innerHTML = '<div class="hint">오너클랜 자동화를 시작합니다... (백그라운드, 화면은 안 뜹니다)</div>';
    try {
      const data = await api('/api/ownerclan/collect', { method: 'POST' });
      if (data.ok && data.upload && !data.upload.error) {
        // STOCK/DPJ와 마찬가지로 문제없이 끝났을 땐 로그를 지저분하게
        // 남기지 않고 토스트로만 결과를 알린다 (실패했을 때만 원인
        // 파악용으로 로그를 남긴다).
        log.innerHTML = '';
        const u = data.upload;
        const extra = (typeof u.total_rows === 'number')
          ? ` (${u.inserted || 0}건 신규, ${u.updated || 0}건 갱신)`
          : '';
        toast(`오너클랜 자동 수집 및 반영이 완료되었습니다.${extra}`, 'ok');
        state.loadedTabs.delete('summary');
        loadDashOrders();
      } else if (data.ok && data.upload && data.upload.error) {
        renderCollectLog('collect-log', data);
        toast(`파일은 저장했지만 반영 중 오류: ${data.upload.error}`, 'err');
      } else {
        renderCollectLog('collect-log', data);
        toast('자동 수집이 중간에 멈췄어요. 아래 로그를 확인해주세요.', 'err');
      }
    } catch (e) {
      log.innerHTML = `<div class="upload-log-item err"><span>${e.message}</span></div>`;
      toast('자동 수집 요청이 실패했습니다.', 'err');
    }
    btn.disabled = false;
    btn.textContent = 'OC';
  });

  async function runCheckStock(auto) {
    const btn = document.getElementById('btn-check-stock');
    const log = document.getElementById('collect-log');
    btn.disabled = true;
    btn.textContent = '⏳';
    log.innerHTML = auto
      ? '<div class="hint">방금 수집으로 신규주문이 생겨서, 재고상태를 자동으로 이어서 확인하는 중... (건수에 따라 시간이 걸릴 수 있어요)</div>'
      : '<div class="hint">신규주문 건들의 재고상태를 오너클랜에서 백그라운드로 확인하는 중... (건수에 따라 시간이 걸릴 수 있어요)</div>';
    // DPJ와 같은 이유로 - 건수가 많으면 몇 분씩 걸릴 수 있는데 그동안 화면이
    // 안 바뀌면 "멈췄다"는 오해로 이어진다. 진행 중 로그를 실시간으로 보여준다.
    const progressTimer = setInterval(async () => {
      try {
        const p = await api('/api/ownerclan/progress');
        if (p.log && p.log.length) renderCollectLog('collect-log', p);
      } catch (_) { /* 진행상황 조회 실패는 무시 - 본 요청 결과가 우선 */ }
    }, 1500);
    try {
      const data = await api('/api/ownerclan/check_stock', { method: 'POST' });
      if (data.ok) {
        const results = data.results || [];
        let ok = 0, soldout = 0, fail = 0, priceMiss = 0;
        results.forEach((r) => {
          if (r.status === '정상') ok++;
          else if (r.status === '품절') soldout++;
          else fail++;
          if ((r.status === '정상' || r.status === '품절') && !r.price) priceMiss++;
        });
        // 매입예상가를 계속 못 가져오는 게 우리 코드 문제인지 화면 구조가
        // 다른 건지 바로 확인할 수 있게, 가격을 하나라도 못 읽었으면
        // 로그를 지우지 않고 그대로 보여준다(실패 이유가 로그에 남아있음).
        // 확인한 건수가 아예 0건일 때도(예: 확인 대상 상품코드가 없어서
        // 조용히 "완료"로 끝나버리는 경우) 로그를 지워버리면 왜 0건인지
        // 이유(로그에 남는 "확인할 판매사상품코드가 없습니다" 등)를 사용자가
        // 못 보고 "STOCK이 아예 안 돈다"고 오해하게 된다 - 0건일 때도 로그를 남긴다.
        if (priceMiss > 0 || results.length === 0) {
          renderCollectLog('collect-log', data);
        } else {
          log.innerHTML = '';
        }
        const detail = results.length
          ? ` - 정상 ${ok}건, 품절 ${soldout}건${fail ? `, 확인실패 ${fail}건` : ''}${priceMiss ? `, 매입예상가 확인 실패 ${priceMiss}건` : ''}`
          : ' - 확인할 대상이 없었습니다(아래 로그 확인).';
        toast(`재고상태 확인 완료 (${data.checked || 0}건)${detail}.`, (priceMiss || results.length === 0) ? 'err' : 'ok');
        loadDashOrders();
      } else {
        const reason = (data.log && data.log.length) ? ` - ${data.log[data.log.length - 1]}` : '';
        toast(`재고상태 확인이 중간에 멈췄어요.${reason}`, 'err');
      }
    } catch (e) {
      toast('재고상태 확인 요청이 실패했습니다.', 'err');
    } finally {
      clearInterval(progressTimer);
    }
    btn.disabled = false;
    btn.textContent = 'STOCK';
  }

  document.getElementById('btn-check-stock').addEventListener('click', () => runCheckStock(false));

  document.getElementById('btn-place-order').addEventListener('click', async () => {
    const btn = document.getElementById('btn-place-order');
    const log = document.getElementById('collect-log');
    const checkedCount = (state.dashRows || []).filter(r => r.order_chk === 'Y').length;
    if (checkedCount === 0) {
      toast('발주할 주문이 없습니다 - 표에서 "발주" 체크박스를 먼저 켜주세요.', 'err');
      return;
    }
    if (!confirm(`체크된 ${checkedCount}건을 오너클랜에서 자동으로 발주(결제하기 클릭까지) 진행할까요?\n카드 정보 입력과 최종 결제는 직접 하셔야 합니다.`)) {
      return;
    }
    btn.disabled = true;
    btn.textContent = '⏳';
    log.innerHTML = '<div class="hint">체크된 주문을 오너클랜에서 한 건씩 자동으로 발주하는 중... (건마다 결제창이 뜨면 직접 카드결제를 완료해주세요)</div>';
    try {
      const data = await api('/api/ownerclan/place_orders', { method: 'POST' });
      renderCollectLog('collect-log', data);
      if (data.ok) {
        const results = data.results || [];
        const okCount = results.filter(r => r.ok).length;
        const failCount = results.length - okCount;
        toast(`발주 자동화 완료 - 결제하기까지 진행 ${okCount}건${failCount ? `, 실패 ${failCount}건` : ''} (총 ${data.attempted || 0}건 시도).`, failCount ? 'err' : 'ok');
        loadDashOrders();
      } else {
        toast('발주 자동화가 중간에 멈췄어요. 아래 로그를 확인해주세요.', 'err');
      }
    } catch (e) {
      toast('발주 요청이 실패했습니다.', 'err');
    }
    btn.disabled = false;
    btn.textContent = 'ORDER';
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
      if (state.tab === 'summary') { loadSummaryAll(); loadCalendar(); }
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

  // ================= TAB 3: 부가세 통합 =================
  const vatState = { year: NOW.getFullYear(), half: (NOW.getMonth() + 1) <= 6 ? 1 : 2 };
  let vatMarkets = [];
  let vatStagedFiles = []; // File[]

  async function loadVatMarkets() {
    vatMarkets = await api('/api/vat/markets');
  }

  function guessVatMarket(filename) {
    for (const m of vatMarkets) {
      if (filename.includes(m)) return m;
    }
    if (filename.includes('지마켓') || filename.toUpperCase().includes('G마켓') || filename.toUpperCase().includes('GMARKET')) return '지마켓';
    return '';
  }

  function renderVatStageTable() {
    const wrap = document.getElementById('vat-stage-wrap');
    const tbody = document.querySelector('#vat-stage-table tbody');
    tbody.innerHTML = '';
    wrap.style.display = vatStagedFiles.length ? '' : 'none';
    vatStagedFiles.forEach((file, idx) => {
      const tr = document.createElement('tr');
      const nameTd = document.createElement('td');
      nameTd.textContent = file.name;
      nameTd.title = file.name;
      nameTd.style.maxWidth = '360px';
      nameTd.style.overflow = 'hidden';
      nameTd.style.textOverflow = 'ellipsis';
      nameTd.style.whiteSpace = 'nowrap';
      tr.appendChild(nameTd);

      const selectTd = document.createElement('td');
      const select = document.createElement('select');
      select.dataset.idx = String(idx);
      const blankOpt = document.createElement('option');
      blankOpt.value = ''; blankOpt.textContent = '마켓 선택';
      select.appendChild(blankOpt);
      for (const m of vatMarkets) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        select.appendChild(opt);
      }
      const guessed = guessVatMarket(file.name);
      if (guessed) select.value = guessed;
      selectTd.appendChild(select);
      tr.appendChild(selectTd);

      const removeTd = document.createElement('td');
      const removeBtn = document.createElement('button');
      removeBtn.className = 'stage-remove-btn';
      removeBtn.textContent = '✕';
      removeBtn.addEventListener('click', () => {
        vatStagedFiles.splice(idx, 1);
        renderVatStageTable();
      });
      removeTd.appendChild(removeBtn);
      tr.appendChild(removeTd);

      tbody.appendChild(tr);
    });
  }

  function addVatStagedFiles(fileList) {
    for (const f of fileList) {
      if (!vatStagedFiles.some(existing => existing.name === f.name && existing.size === f.size)) {
        vatStagedFiles.push(f);
      }
    }
    renderVatStageTable();
  }

  const VAT_CAT_KEYS = ['credit', 'cash', 'mobile', 'other'];
  const VAT_CAT_SHORT = { credit: '카드', cash: '현금', mobile: '폰', other: '기타' };

  function renderVatHalfTable(data) {
    const thead = document.querySelector('#vat-half-table thead');
    const tbody = document.querySelector('#vat-half-table tbody');
    thead.innerHTML = '';
    tbody.innerHTML = '';

    const headTr = document.createElement('tr');
    headTr.innerHTML = `<th style="text-align:left;">구분</th>` +
      data.market_groups.map(g => `<th>${g.market}</th>`).join('') + `<th>합계</th>`;
    thead.appendChild(headTr);

    function subtotalOf(monthIdx) {
      return monthIdx === null ? data.grand_total : data.grand_month_totals[monthIdx];
    }
    function catTotalOf(catKey, monthIdx) {
      return data.market_groups.reduce((sum, g) =>
        sum + (monthIdx === null ? g.categories[catKey].row_total : g.categories[catKey].months[monthIdx]), 0);
    }

    function buildGroup(label, monthIdx) {
      const subTr = document.createElement('tr');
      subTr.style.borderTop = '2px solid var(--border-strong)';
      const subCells = data.market_groups.map(g => {
        const v = monthIdx === null ? g.row_total : g.months[monthIdx];
        return `<td style="font-weight:700;">${num(v)}</td>`;
      }).join('');
      subTr.innerHTML = `<td style="font-weight:700;">${label}</td>${subCells}<td style="font-weight:700; color:var(--good);">${num(subtotalOf(monthIdx))}</td>`;
      tbody.appendChild(subTr);

      for (const catKey of VAT_CAT_KEYS) {
        const tr = document.createElement('tr');
        const cells = data.market_groups.map(g => {
          const v = monthIdx === null ? g.categories[catKey].row_total : g.categories[catKey].months[monthIdx];
          return `<td>${num(v)}</td>`;
        }).join('');
        tr.innerHTML = `<td style="padding-left:16px; color:var(--text-secondary); font-weight:400;">${VAT_CAT_SHORT[catKey]}</td>${cells}<td>${num(catTotalOf(catKey, monthIdx))}</td>`;
        tbody.appendChild(tr);
      }
    }

    buildGroup('합계', null);
    data.months.forEach((m, idx) => buildGroup(`${m}월`, idx));
  }

  async function loadVatView() {
    const emptyHint = document.getElementById('vat-empty-hint');
    const table = document.getElementById('vat-half-table');
    document.getElementById('vat-half-label').textContent = `${vatState.year}년 ${vatState.half === 1 ? '상반기 (1~6월)' : '하반기 (7~12월)'}`;
    const data = await api(`/api/vat/half?${qs({ year: vatState.year, half: vatState.half })}`);
    if (data.market_groups.length === 0) {
      emptyHint.style.display = '';
      table.style.display = 'none';
      return;
    }
    emptyHint.style.display = 'none';
    table.style.display = '';
    renderVatHalfTable(data);
  }

  function navigateVatHalf(dir) {
    if (dir > 0) {
      if (vatState.half === 1) { vatState.half = 2; } else { vatState.half = 1; vatState.year += 1; }
    } else {
      if (vatState.half === 2) { vatState.half = 1; } else { vatState.half = 2; vatState.year -= 1; }
    }
    loadVatView();
  }
  document.getElementById('vat-half-prev').addEventListener('click', () => navigateVatHalf(-1));
  document.getElementById('vat-half-next').addEventListener('click', () => navigateVatHalf(1));

  async function uploadOneVatFile(file, market) {
    const fd = new FormData();
    fd.append('market', market);
    fd.append('files', file);
    const data = await api('/api/vat/upload', { method: 'POST', body: fd });
    return { market, result: data.results[0] };
  }

  async function submitVatStage() {
    const selects = Array.from(document.querySelectorAll('#vat-stage-table select'));
    const missing = selects.filter(s => !s.value);
    if (missing.length) {
      toast(`마켓을 선택하지 않은 파일이 ${missing.length}개 있어요.`, 'err');
      return;
    }
    const log = document.getElementById('vat-upload-log');
    log.innerHTML = '';
    // 여러 파일을 한꺼번에 병렬로 올리면 윈도우 백신이 방금 생성된 임시파일들을
    // 동시에 스캔하려다 서로 경합해서 잠금이 오래가는 경우가 있어, 한 번에 하나씩
    // 순서대로 올린다 (느리지만 훨씬 안정적).
    for (let idx = 0; idx < vatStagedFiles.length; idx++) {
      const file = vatStagedFiles[idx];
      const market = selects[idx].value;
      const item = document.createElement('div');
      item.className = 'upload-log-item';
      item.innerHTML = `<span>${file.name}</span><span>처리 중...</span>`;
      log.appendChild(item);
      try {
        const { result } = await uploadOneVatFile(file, market);
        item.className = `upload-log-item ${result.error ? 'err' : ''}`;
        item.innerHTML = result.error
          ? `<span>${result.filename}</span><span>${result.error}</span>`
          : `<span>${result.filename} <span class="tag">(${market})</span></span><span>인식: ${result.months.join(', ')}</span>`;
      } catch (e) {
        item.className = 'upload-log-item err';
        item.innerHTML = `<span>${file.name}</span><span>${e.message}</span>`;
      }
    }

    toast('부가세 자료가 반영되었습니다.', 'ok');
    vatStagedFiles = [];
    renderVatStageTable();
    loadVatView();
  }

  const vatDropzone = document.getElementById('vat-dropzone');
  vatDropzone.addEventListener('click', () => document.getElementById('vat-file-input').click());
  vatDropzone.addEventListener('dragover', (e) => { e.preventDefault(); vatDropzone.classList.add('drag'); });
  vatDropzone.addEventListener('dragleave', () => vatDropzone.classList.remove('drag'));
  vatDropzone.addEventListener('drop', (e) => {
    e.preventDefault(); vatDropzone.classList.remove('drag');
    if (e.dataTransfer.files.length) addVatStagedFiles(e.dataTransfer.files);
  });
  document.getElementById('vat-file-input').addEventListener('change', (e) => {
    if (e.target.files.length) addVatStagedFiles(e.target.files);
    e.target.value = '';
  });
  document.getElementById('vat-stage-upload-btn').addEventListener('click', submitVatStage);
  document.getElementById('vat-stage-clear-btn').addEventListener('click', () => {
    vatStagedFiles = [];
    renderVatStageTable();
  });

  // ================= TAB SWITCHING =================
  function activateTab(name) {
    state.tab = name;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `tab-${name}`));

    if (state.loadedTabs.has(name)) return;
    state.loadedTabs.add(name);
    if (name === 'summary') { loadSummaryAll(); loadCalendar(); }
    if (name === 'dashboard') { renderDashFilters(); renderDashTableHead(); loadDashOrders(); }
    if (name === 'vat') { loadVatMarkets(); loadVatView(); }
    if (name === 'upload') { loadSettingsLinks(); }
    if (name === 'fees') { loadFees(); }
  }

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  const appLogo = document.getElementById('app-logo');
  appLogo.addEventListener('click', () => activateTab('summary'));
  appLogo.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activateTab('summary'); }
  });

  activateTab('summary');
})();
