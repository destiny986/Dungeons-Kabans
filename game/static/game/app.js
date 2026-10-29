/* Journal interactions. No user-controlled HTML is constructed in JavaScript. */
(() => {
  'use strict';
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const appDialog = q('#app-dialog');
  let focusReturn = null, anchorPoint = null, grid = null, notificationTimer;
  const confirmed = new WeakSet();
  const scrollState = new Map();
  let readPending = false, lastReadMessage = null;
  function notify(message) {
    const target = q('#notifications');
    if (!target) return;
    target.textContent = message;
    clearTimeout(notificationTimer);
    notificationTimer = setTimeout(() => { target.textContent = ''; }, 5500);
  }
  function csrf() { return q('[name=csrfmiddlewaretoken]')?.value || ''; }
  function refresh() { if (window.htmx && q('#poller')) htmx.trigger('#poller', 'refresh'); }
  function positionDialog(dialog, point) {
    dialog.style.margin = 'auto'; dialog.style.top = ''; dialog.style.left = '';
    if (!point || dialog.querySelector('.confirmation,.error-box,.error-page')) return;
    const rect = dialog.getBoundingClientRect(), pad = 14;
    const x = Math.max(pad, Math.min(point.x, innerWidth - rect.width - pad));
    const y = Math.max(pad, Math.min(point.y + 10, innerHeight - rect.height - pad));
    dialog.style.margin = '0'; dialog.style.left = `${x}px`; dialog.style.top = `${y}px`;
  }
  function openDialog(dialog, point = null, returnTarget = document.activeElement) {
    if (!dialog) return;
    focusReturn = returnTarget;
    if (!dialog.open) dialog.showModal();
    positionDialog(dialog, point);
  }
  function closeDialog(dialog = appDialog) { if (dialog?.open) dialog.close(); }
  function applyFilter(input) {
    const root = document.getElementById(input.dataset.filter);
    if (!root) return;
    const controls = qa('[data-filter]').filter(control => control.dataset.filter === input.dataset.filter);
    const needle = (controls.find(control => control.type === 'search')?.value || '').trim().toLocaleLowerCase('ru');
    const activation = controls.find(control => control.dataset.filterKey === 'activation')?.value || '';
    const resource = controls.find(control => control.dataset.filterKey === 'resource')?.value || '';
    const filtering = !!(needle || activation || resource);
    let matches = 0;
    qa('[data-search]', root).forEach(row => {
      row.hidden = !row.dataset.search.toLocaleLowerCase('ru').includes(needle) ||
        (!!activation && row.dataset.activation !== activation) || (!!resource && row.dataset.resource !== resource);
      if (!row.hidden) matches++;
    });
    let empty = q('.no-filter-results', root);
    if (filtering && !matches && qa('[data-search]', root).length) {
      if (!empty) { empty = document.createElement('p'); empty.className = 'no-filter-results'; empty.textContent = 'Ничего не найдено. Попробуйте другой запрос.'; root.append(empty); }
    } else empty?.remove();
  }
  function updateCapabilities(root = document) {
    qa('[data-capability]', root).forEach(block => {
      const toggle = document.querySelector(`[name="${block.dataset.capability}"]`);
      // Keep inputs mounted so toggling a capability never discards a draft.
      block.hidden = !!toggle && !toggle.checked;
      qa('input,select,textarea', block).forEach(field => { field.disabled = block.hidden; });
    });
    qa('[data-show-resource]', root).forEach(block => { block.hidden = q('[name=resource]')?.value !== block.dataset.showResource; });
  }
  function updatePreview(form) {
    const root = form.closest('.character-editor') || form.closest('.editor-layout');
    if (!root) return;
    qa('[data-preview]', root).forEach(target => {
      const field = form.elements.namedItem(target.dataset.preview);
      if (field && field.value !== undefined) target.textContent = field.value;
    });
    qa('[data-preview-select]', root).forEach(target => {
      const field = form.elements.namedItem(target.dataset.previewSelect);
      if (field?.selectedOptions) target.textContent = field.selectedOptions[0]?.textContent || '';
    });
    const tags = q('[data-preview-tags]', root), field = form.elements.namedItem('tags');
    if (tags && field) {
      tags.replaceChildren();
      field.value.split(',').map(tag => tag.trim()).filter(Boolean).forEach(tag => { const span = document.createElement('span'); span.textContent = tag; tags.append(span); });
    }
    const color = form.elements.namedItem('color')?.value;
    if (color && ['red','blue','green','orange','yellow','cyan'].includes(color)) qa('[data-preview-card]', root).forEach(card => {
      ['red','blue','green','orange','yellow','cyan'].forEach(c => card.classList.remove(`event-${c}`)); card.classList.add(`event-${color}`);
    });
  }
  function markRead() {
    const pane = q('#pane-chat');
    if (!pane || pane.hidden || document.hidden || readPending) return;
    const last = qa('#player-messages .chat-message').reduce((maximum, message) => {
      const id = message.dataset.messageId || '0';
      return BigInt(id) > BigInt(maximum) ? id : maximum;
    }, '0');
    if (last === lastReadMessage) return;
    readPending = true;
    fetch(pane.dataset.readUrl, { method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams({through_id:last}) })
      .then(response => { if (!response.ok) throw new Error(); lastReadMessage = last; const badge = q('#chat-unread'); if (badge) { badge.hidden = true; badge.textContent = ''; } })
      .catch(() => { /* Retried on next poll; do not clear unread before success. */ })
      .finally(() => { readPending = false; });
  }
  function activateTab(name, focus = false) {
    const tabs = qa('[data-tab]');
    tabs.forEach(tab => { const selected = tab.dataset.tab === name; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; const pane = document.getElementById(tab.getAttribute('aria-controls')); if (pane) pane.hidden = !selected; if (selected && focus) tab.focus(); });
    const input = q('#active-tab'); if (input) input.value = name;
    if (name === 'chat') { const chat = q('#player-messages'); if (chat && !chat.dataset.opened) { chat.scrollTop = chat.scrollHeight; chat.dataset.opened = 'true'; } markRead(); }
  }
  function initGrid() {
    const el = q('#master-grid');
    if (!el || !window.GridStack) return;
    const defaults = qa('.grid-stack-item', el).map(node => ({id:node.getAttribute('gs-id'),x:+node.getAttribute('gs-x'),y:+node.getAttribute('gs-y'),w:+node.getAttribute('gs-w'),h:+node.getAttribute('gs-h')}));
    grid = GridStack.init({column:12,cellHeight:67,margin:9,handle:'.panel-heading',float:false,animate:false,minRow:10,draggable:{cancel:'button,a,input,select,textarea,form'},resizable:{handles:'se'},columnOpts:{breakpoints:[{w:767,c:1}]}}, el);
    try { const saved = JSON.parse(localStorage.getItem('dk-journal-layout-v1')); if (Array.isArray(saved) && saved.length === defaults.length && saved.every(node => defaults.some(d => d.id === node.id) && ['x','y','w','h'].every(key => Number.isInteger(node[key]) && node[key] >= 0) && node.w > 0 && node.h > 0 && node.w <= 12)) grid.load(saved, false); } catch (_) { /* Corrupt or unavailable storage uses the default table. */ }
    grid.on('change', () => { if (innerWidth > 767) { try { localStorage.setItem('dk-journal-layout-v1', JSON.stringify(grid.save(false).map(({id,x,y,w,h}) => ({id,x,y,w,h})))); } catch (_) {} } });
    const mobile = matchMedia('(max-width: 767px)');
    const update = () => { grid.enableMove(!mobile.matches); grid.enableResize(!mobile.matches); };
    mobile.addEventListener('change', update); update();
    q('[data-reset-layout]')?.addEventListener('click', () => { try { localStorage.removeItem('dk-journal-layout-v1'); } catch (_) {} grid.load(defaults, false); notify('Расположение секций восстановлено'); });
  }
  document.addEventListener('click', event => {
    const close = event.target.closest('[data-close]');
    if (close) { closeDialog(close.closest('dialog')); return; }
    const opener = event.target.closest('[data-open]');
    if (opener) { openDialog(document.getElementById(opener.dataset.open), null, opener); return; }
    const card = event.target.closest('[data-dialog]');
    if (card) { focusReturn = card; anchorPoint = {x:event.clientX || card.getBoundingClientRect().left, y:event.clientY || card.getBoundingClientRect().top}; }
    const tab = event.target.closest('[data-tab]'); if (tab) activateTab(tab.dataset.tab);
    const all = event.target.closest('[data-select-all],[data-select-none]'); if (all) qa('input[name=recipients]', all.closest('form')).forEach(input => { input.checked = all.hasAttribute('data-select-all'); });
    const quantity = event.target.closest('[data-quantity-all]'); if (quantity) { q('[name=quantity]', quantity.closest('form')).value = quantity.dataset.quantityAll; notify('Выбраны все предметы. Нажмите «Удалить» для подтверждения.'); }
    const copy = event.target.closest('[data-copy]'); if (copy) { const input = document.getElementById(copy.dataset.copy); if (input) { input.select(); if (navigator.clipboard?.writeText) navigator.clipboard.writeText(input.value).then(() => notify('Ссылка скопирована')).catch(() => notify('Выделите и скопируйте ссылку')); else { document.execCommand('copy'); notify('Ссылка скопирована'); } } }
  });
  document.addEventListener('close', event => { if (event.target.tagName !== 'DIALOG') return; if (focusReturn?.isConnected) focusReturn.focus({preventScroll:true}); }, true);
  document.addEventListener('click', event => { if (event.target.tagName === 'DIALOG') { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog(event.target); } });
  document.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-tab]');
    if (tab && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { event.preventDefault(); const tabs = qa('[data-tab]'); let index = tabs.indexOf(tab); if (event.key === 'Home') index = 0; else if (event.key === 'End') index = tabs.length - 1; else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; activateTab(tabs[index].dataset.tab, true); }
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && event.target.closest('[data-chat-form]')) { event.preventDefault(); event.target.closest('form').requestSubmit(); }
  });
  document.addEventListener('input', event => {
    if (event.target.matches('[data-filter]')) applyFilter(event.target);
    if (event.target.id === 'health-current') event.target.dataset.dirty = 'true';
    const form = event.target.closest('[data-preview-form]'); if (form) updatePreview(form);
  });
  document.addEventListener('change', event => {
    if (event.target.matches('[data-filter]')) applyFilter(event.target);
    updateCapabilities();
    const form = event.target.closest('[data-preview-form]'); if (!form) return;
    updatePreview(form);
    if (event.target.type === 'file' && ['icon','portrait'].includes(event.target.name)) {
      const root = form.closest('.character-editor') || form.closest('.editor-layout');
      const file = event.target.files[0]; if (!file) return;
      const url = URL.createObjectURL(file);
      qa('[data-preview-image]', root).forEach(img => { if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl); img.src = url; img.dataset.objectUrl = url; img.hidden = false; });
      q('.preview-portrait>.portrait-empty',root)?.remove();
    }
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form.matches('[data-send-form]') && !q('[name=recipients]:checked', form)) { event.preventDefault(); event.stopImmediatePropagation(); notify('Выберите хотя бы одного получателя'); return; }
    if (q('[data-require-selection]',form) && !q('input[type=checkbox]:checked',form)) { event.preventDefault(); event.stopImmediatePropagation(); notify('Выберите категории для удаления'); return; }
    if (form.dataset.confirm && !confirmed.has(form)) {
      event.preventDefault(); event.stopImmediatePropagation();
      const confirmation = document.createElement('dialog'); confirmation.setAttribute('aria-label','Подтверждение'); confirmation.append(q('#confirm-template').content.cloneNode(true)); q('[data-confirm-text]',confirmation).textContent = form.dataset.confirm; document.body.append(confirmation);
      q('[data-confirm-yes]',confirmation).addEventListener('click', () => { confirmed.add(form); confirmation.close(); form.requestSubmit(event.submitter || undefined); confirmed.delete(form); });
      confirmation.addEventListener('close', () => confirmation.remove(), {once:true}); openDialog(confirmation, null, event.submitter || form); return;
    }
    const key = q('[name=request_id]',form);
    if (key && !key.dataset.operationKey && !q('[name=proposal]',form)) { key.value = crypto.randomUUID(); key.dataset.operationKey = 'true'; }
  }, true);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); markRead(); } });
  document.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.elt?.closest('form[data-health-form]')) { focusReturn = q('#health-current'); anchorPoint = null; }
  });
  document.addEventListener('htmx:beforeSwap', event => {
    if ([409,422].includes(event.detail.xhr.status)) { event.detail.shouldSwap = true; event.detail.isError = false; event.detail.target = q('#dialog-content'); }
    if (event.detail.target?.id === 'dialog-content' && event.detail.xhr.responseText.trim()) { if (event.detail.xhr.status >= 400) anchorPoint = null; }
  });
  document.addEventListener('htmx:oobBeforeSwap', event => {
    const target = event.detail.target;
    if (!target) return;
    if (target.id === 'player-summary' && (document.activeElement?.id === 'health-current' || q('#health-current')?.dataset.dirty === 'true')) { event.detail.shouldSwap = false; return; }
    // Local player menus stay alive while open; their next poll catches up on close.
    if (target.querySelector('dialog[open]')) { event.detail.shouldSwap = false; return; }
    if (target.matches('[data-chat-scroll]')) scrollState.set(target.id, {top:target.scrollTop,bottom:target.scrollHeight-target.scrollTop-target.clientHeight < 45});
    if (target.id === 'chat-recipient') target.dataset.savedValue = target.value;
  });
  document.addEventListener('htmx:oobAfterSwap', event => {
    const target = event.detail.target; if (!target) return;
    if (scrollState.has(target.id)) { const state = scrollState.get(target.id); target.scrollTop = state.bottom ? target.scrollHeight : state.top; scrollState.delete(target.id); }
    if (target.id === 'chat-recipient' && target.dataset.savedValue) { target.value = target.dataset.savedValue; delete target.dataset.savedValue; }
    qa('[data-filter]').forEach(applyFilter); markRead();
  });
  document.addEventListener('htmx:afterSwap', event => {
    if (event.detail.target?.id === 'dialog-content' && event.detail.target.textContent.trim()) { openDialog(appDialog, anchorPoint, focusReturn); anchorPoint = null; }
  });
  document.addEventListener('htmx:afterRequest', event => {
    const source = event.detail.elt;
    const healthForm = source?.closest('form[data-health-form]');
    if (healthForm && event.detail.xhr.status === 422) {
      const input = q('#health-current');
      if (input) { input.value = input.dataset.savedValue; delete input.dataset.dirty; }
    }
    if (event.detail.successful && event.detail.xhr.status >= 200 && event.detail.xhr.status < 300 && source?.id !== 'poller') {
      const form = source?.closest('form');
      if (form?.matches('[data-health-form]')) { const input = q('#health-current'); if (input) { input.dataset.savedValue = input.value; delete input.dataset.dirty; } }
      if (form?.matches('[data-chat-form]')) { const text = q('[name=text]',form); if (text) text.value = ''; }
      const key = form && q('[name=request_id]',form); if (key) { key.value = crypto.randomUUID(); delete key.dataset.operationKey; }
      refresh();
    }
    if (source?.id === 'poller') { const status = q('#connection-status'); if (status) status.textContent = event.detail.successful ? 'Обновлено' : 'Не удалось обновить. Повторяем соединение.'; }
  });
  document.addEventListener('htmx:sendError', () => notify('Нет соединения. Изменения не подтверждены; повторите отправку.'));
  document.addEventListener('htmx:responseError', event => { if (![409,422].includes(event.detail.xhr.status)) notify('Не удалось выполнить запрос. Попробуйте ещё раз.'); });
  document.addEventListener('closeDialog', () => closeDialog());
  document.addEventListener('refresh', event => { if (event.target !== q('#poller')) refresh(); });
  qa('[data-chat-scroll]').forEach(chat => { chat.scrollTop = chat.scrollHeight; });
  qa('[data-preview-form]').forEach(updatePreview); updateCapabilities(); initGrid();
})();
