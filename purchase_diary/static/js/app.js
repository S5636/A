// 구매 다이어리 - 클라이언트 로직 (프레임워크 없이 순수 JS)

const state = { items: [], purchaseTargetId: null };

const $ = (sel) => document.querySelector(sel);

function toast(msg, kind) {
  const wrap = $('#toast-wrap');
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function fmtWon(n) {
  if (n === null || n === undefined) return '-';
  return Number(n).toLocaleString('ko-KR') + '원';
}

function fmtDate(s) {
  return s || '-';
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || '요청이 실패했습니다.');
  return data;
}

// ---------------------------------------------------------------------------
// Load / render
// ---------------------------------------------------------------------------

async function loadItems() {
  try {
    const data = await api('/api/items');
    state.items = data.items;
    renderKpi();
    renderItems();
  } catch (e) {
    toast(e.message, 'err');
  }
}

function renderKpi() {
  const items = state.items;
  const totalItems = items.length;

  const now = new Date();
  const ym = now.toISOString().slice(0, 7);
  let monthSpend = 0;
  for (const it of items) {
    for (const p of it.purchases) {
      if (p.purchase_date.slice(0, 7) === ym) monthSpend += p.price;
    }
  }

  const dueSoon = items.filter((it) => it.days_until_next !== null && it.days_until_next <= 3).length;
  const priceUp = items.filter((it) => it.price_diff !== null && it.price_diff > 0).length;

  const tiles = [
    { label: '등록 품목 수', value: totalItems + '개', accent: 'var(--series-1)' },
    { label: '이번 달 지출', value: fmtWon(monthSpend), accent: 'var(--accent-pink)' },
    { label: '재구매 임박(3일 이내)', value: dueSoon + '개', accent: 'var(--warning)' },
    { label: '가격 오른 품목', value: priceUp + '개', accent: 'var(--critical)' },
  ];

  $('#kpi-row').innerHTML = tiles.map((t) => `
    <div class="kpi-tile" style="--tile-accent:${t.accent}">
      <div class="kpi-label">${t.label}</div>
      <div class="kpi-value">${t.value}</div>
    </div>
  `).join('');
}

function nextBadge(item) {
  if (item.days_until_next === null) return '';
  const d = item.days_until_next;
  let cls = '';
  let text = '';
  if (d < 0) { cls = 'overdue'; text = `재구매 예상일 ${Math.abs(d)}일 지남`; }
  else if (d <= 3) { cls = 'due'; text = d === 0 ? '오늘이 재구매 예상일' : `재구매 예상 ${d}일 후`; }
  else { text = `재구매 예상 ${d}일 후 (${item.next_expected_date})`; }
  return `<div class="next-badge ${cls}">${text}</div>`;
}

function diffClass(diff) {
  if (diff === null || diff === undefined) return 'diff-flat';
  if (diff > 0) return 'diff-up';
  if (diff < 0) return 'diff-down';
  return 'diff-flat';
}

function renderItems() {
  const grid = $('#item-grid');
  const empty = $('#empty-hint');
  if (state.items.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  grid.innerHTML = state.items.map((it) => {
    const hasPurchase = it.purchase_count > 0;
    const hasCompare = it.purchase_count >= 2;

    const diffSign = it.price_diff > 0 ? '▲' : it.price_diff < 0 ? '▼' : '-';
    const diffText = hasCompare
      ? `${diffSign} ${fmtWon(Math.abs(it.price_diff))} (${it.price_diff_pct > 0 ? '+' : ''}${it.price_diff_pct}%)`
      : '-';

    const historyRows = it.purchases.map((p) => `
      <div class="history-row" data-purchase-id="${p.id}">
        <span>${p.purchase_date} · ${fmtWon(p.price)}${p.store ? ' · ' + escapeHtml(p.store) : ''}</span>
        <button class="hr-del" data-del-purchase="${p.id}" title="삭제">✕</button>
      </div>
    `).join('');

    return `
      <div class="item-card" data-item-id="${it.id}">
        <div class="item-card-head">
          <div>
            <div class="item-name">${escapeHtml(it.name)}</div>
            ${it.category ? `<div class="item-category">${escapeHtml(it.category)}</div>` : ''}
          </div>
          <button class="item-del-btn" data-del-item="${it.id}" title="품목 삭제">🗑</button>
        </div>

        ${hasPurchase ? `
          <div class="stat-row">
            <div class="stat-box">
              <div class="stat-box-label">지난 구매</div>
              <div class="stat-box-value">${hasCompare ? fmtDate(it.prev_date) : '-'}</div>
            </div>
            <div class="stat-box">
              <div class="stat-box-label">이번 구매</div>
              <div class="stat-box-value">${fmtDate(it.last_date)}</div>
            </div>
            <div class="stat-box">
              <div class="stat-box-label">구매 간격</div>
              <div class="stat-box-value">${hasCompare ? it.interval_days + '일 (평균 ' + it.avg_interval_days + '일)' : '-'}</div>
            </div>
            <div class="stat-box">
              <div class="stat-box-label">가격 차이</div>
              <div class="stat-box-value ${diffClass(it.price_diff)}">${diffText}</div>
            </div>
          </div>
          ${nextBadge(it)}
        ` : `<div class="no-data-hint">아직 구매 기록이 없어요. 아래 버튼으로 첫 구매를 기록해보세요.</div>`}

        <div class="item-card-actions">
          <button class="btn small" data-add-purchase="${it.id}">+ 구매 기록 추가</button>
          ${hasPurchase ? `<button class="btn secondary small history-toggle-btn" data-toggle-history="${it.id}">기록 보기 (${it.purchase_count})</button>` : ''}
        </div>

        ${hasPurchase ? `<div class="history-list" id="history-${it.id}">${historyRows}</div>` : ''}
      </div>
    `;
  }).join('');

  bindItemCardEvents();
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s || '';
  return div.innerHTML;
}

function bindItemCardEvents() {
  document.querySelectorAll('[data-del-item]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('이 품목과 구매 기록을 모두 삭제할까요?')) return;
      try {
        await api(`/api/items/${btn.dataset.delItem}`, { method: 'DELETE' });
        toast('삭제했습니다.', 'ok');
        loadItems();
      } catch (e) {
        toast(e.message, 'err');
      }
    });
  });

  document.querySelectorAll('[data-add-purchase]').forEach((btn) => {
    btn.addEventListener('click', () => openPurchaseModal(btn.dataset.addPurchase));
  });

  document.querySelectorAll('[data-toggle-history]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const list = document.getElementById('history-' + btn.dataset.toggleHistory);
      list.classList.toggle('open');
    });
  });

  document.querySelectorAll('[data-del-purchase]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('이 구매 기록을 삭제할까요?')) return;
      try {
        await api(`/api/purchases/${btn.dataset.delPurchase}`, { method: 'DELETE' });
        toast('삭제했습니다.', 'ok');
        loadItems();
      } catch (e) {
        toast(e.message, 'err');
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

function openItemModal() {
  $('#input-item-name').value = '';
  $('#input-item-category').value = '';
  $('#modal-add-item').classList.add('open');
  $('#input-item-name').focus();
}

function closeItemModal() {
  $('#modal-add-item').classList.remove('open');
}

function openPurchaseModal(itemId) {
  state.purchaseTargetId = itemId;
  $('#input-purchase-date').value = new Date().toISOString().slice(0, 10);
  $('#input-purchase-price').value = '';
  $('#input-purchase-qty').value = '';
  $('#input-purchase-store').value = '';
  $('#modal-add-purchase').classList.add('open');
}

function closePurchaseModal() {
  $('#modal-add-purchase').classList.remove('open');
  state.purchaseTargetId = null;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function init() {
  $('#btn-add-item').addEventListener('click', openItemModal);
  $('#btn-cancel-item').addEventListener('click', closeItemModal);
  $('#modal-add-item').addEventListener('click', (e) => { if (e.target.id === 'modal-add-item') closeItemModal(); });

  $('#btn-save-item').addEventListener('click', async () => {
    const name = $('#input-item-name').value.trim();
    if (!name) { toast('품목명을 입력해주세요.', 'err'); return; }
    try {
      await api('/api/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, category: $('#input-item-category').value.trim() }),
      });
      closeItemModal();
      toast('품목을 추가했습니다.', 'ok');
      loadItems();
    } catch (e) {
      toast(e.message, 'err');
    }
  });

  $('#btn-cancel-purchase').addEventListener('click', closePurchaseModal);
  $('#modal-add-purchase').addEventListener('click', (e) => { if (e.target.id === 'modal-add-purchase') closePurchaseModal(); });

  $('#btn-save-purchase').addEventListener('click', async () => {
    const itemId = state.purchaseTargetId;
    if (!itemId) return;
    const body = {
      purchase_date: $('#input-purchase-date').value,
      price: $('#input-purchase-price').value,
      quantity: $('#input-purchase-qty').value.trim(),
      store: $('#input-purchase-store').value.trim(),
    };
    try {
      await api(`/api/items/${itemId}/purchases`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      closePurchaseModal();
      toast('구매 기록을 저장했습니다.', 'ok');
      loadItems();
    } catch (e) {
      toast(e.message, 'err');
    }
  });

  loadItems();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }
}

document.addEventListener('DOMContentLoaded', init);
