(() => {
  let lastFocus, prefix = '', timer;
  function open(id) { const dialog = document.getElementById(id); if (!dialog) return; lastFocus = document.activeElement; dialog.showModal(); dialog.querySelector('textarea,input:not([type=hidden]),button')?.focus(); }
  function close(dialog) { dialog.close(); lastFocus?.focus(); }
  document.addEventListener('click', e => { const trigger = e.target.closest('[data-open]'); if (trigger) open(trigger.dataset.open); const closer = e.target.closest('[data-close]'); if (closer) close(closer.closest('dialog')); });
  document.querySelectorAll('dialog:not(#record-editor)').forEach(d => { d.addEventListener('close', () => lastFocus?.focus()); d.addEventListener('click', e => { if(e.target===d) { const r=d.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) close(d); } }); });
  document.addEventListener('keydown', e => {
    const typing = e.target.closest('input,textarea,select,[contenteditable=true]');
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase()==='k') { e.preventDefault(); open('commands'); return; }
    if ((e.metaKey || e.ctrlKey) && e.key==='Enter') { const form=e.target.closest('form'); if(form) { e.preventDefault(); form.requestSubmit(); } return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector('dialog[open]')) return;
    if(e.key==='?') { e.preventDefault(); open('shortcuts'); }
    if(prefix==='g' && ['d','t'].includes(e.key)) { window.location.href=e.key==='d'?'/deals':'/tasks'; prefix=''; return; }
    if(e.key==='g') { prefix='g'; clearTimeout(timer); timer=setTimeout(()=>prefix='',1000); }
    if(e.key==='n' && location.pathname==='/deals') document.querySelector('a[href="/new/deal"]')?.click();
  });
  document.addEventListener('submit', e => { if(!e.target.checkValidity())return; e.target.setAttribute('aria-busy','true'); const b=e.submitter; if(b) { b.classList.add('submitting'); /* retain submitter name/value */ setTimeout(()=>b.disabled=true,0); } });
  window.addEventListener('pageshow',()=>document.querySelectorAll('.submitting').forEach(b=>{b.disabled=false;b.classList.remove('submitting');}));
  document.body.addEventListener('htmx:configRequest', e => { e.detail.headers['X-CSRF-Token']=document.querySelector('meta[name=csrf-token]').content; });
})();

// The board only moves after the server accepts the record version; reload keeps filters and totals correct.
(() => {
  const board = document.querySelector('.board');
  if (!board) return;
  const status = document.getElementById('board-status');
  let dragged = null, busy = false, suppressClick = false;
  const clearTargets = () => board.querySelectorAll('.drop-target').forEach(c => c.classList.remove('drop-target'));
  async function move(card, stage, archived) {
    const isArchive = typeof archived === 'boolean';
    if (busy || (!isArchive && stage === card.dataset.stage)) return;
    busy = true;
    board.setAttribute('aria-busy', 'true');
    status.className = 'notice';
    status.textContent = isArchive ? (archived ? 'Archiving deal…' : 'Restoring deal…') : 'Saving stage…';
    try {
      const response = await fetch('/api/deal/' + encodeURIComponent(card.dataset.dealId), {
        method: 'PATCH', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name=csrf-token]').content,
                  'Idempotency-Key': crypto.randomUUID()},
        body: JSON.stringify({data: isArchive ? {archived} : {stage}, version: Number(card.dataset.version)})
      });
      if (!response.ok) {
        const messages = {409: 'This deal changed. Reload the board before changing it again.',
          401: 'Your session expired. Sign in again to change this deal.',
          403: 'You do not have permission to change this deal.', 404: 'This deal is no longer accessible.'};
        throw new Error(messages[response.status] || 'Could not save the change. Reload and try again.');
      }
      // Keep the current query string, including unit, search, owner and pagination.
      location.reload();
    } catch (error) {
      status.className = 'notice error';
      status.textContent = error.message || 'Connection failed. Reload the board to check the stage.';
      const reload = document.createElement('a');
      reload.href = location.href; reload.className = 'button small'; reload.textContent = 'Reload board';
      status.append(' ', reload);
      busy = false;
      board.removeAttribute('aria-busy');

    }
  }
  board.addEventListener('dragstart', event => {
    const card = event.target.closest('.deal-card');
    if (!card || busy || event.target.closest('select,button,input,details')) { event.preventDefault(); return; }
    dragged = card;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', card.dataset.dealId);
    card.classList.add('dragging');
  });
  board.addEventListener('dragover', event => {
    if (!dragged || busy) return;
    const column = event.target.closest('.board-column');
    if (!column) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move';
    clearTargets(); column.classList.add('drop-target');
    const bounds = board.getBoundingClientRect();
    if (event.clientX > bounds.right - 65) board.scrollLeft += 35;
    else if (event.clientX < bounds.left + 65) board.scrollLeft -= 35;
  });
  board.addEventListener('drop', event => {
    const column = event.target.closest('.board-column');
    if (!dragged || !column) return;
    event.preventDefault();
    suppressClick = true; setTimeout(() => suppressClick = false, 250);
    clearTargets(); move(dragged, column.dataset.stage);
  });
  board.addEventListener('dragend', () => {
    dragged?.classList.remove('dragging'); dragged = null; clearTargets();
  });
  board.addEventListener('click', event => { if (suppressClick) event.preventDefault(); });
  document.addEventListener('click', event => {
    board.querySelectorAll('.card-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  board.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const submenu = event.target.closest('.card-move-menu[open]');
    if (submenu) { submenu.open = false; submenu.querySelector('summary').focus(); event.preventDefault(); return; }
    const menu = event.target.closest('.card-menu[open]');
    if (menu) { menu.open = false; menu.querySelector('summary').focus(); event.preventDefault(); }
  });
  board.addEventListener('submit', event => {
    if (event.target.matches('.card-archive-form')) {
      event.preventDefault(); event.stopPropagation();
      event.target.closest('.card-menu').open = false;
      move(event.target.closest('.deal-card'), null, event.target.elements.archived.value === 'true');
      return;
    }
    if (!event.target.matches('.card-stage-form')) return;
    event.preventDefault(); event.stopPropagation();
    const menu = event.target.closest('.card-menu');
    if (menu) menu.open = false;
    move(event.target.closest('.deal-card'), event.submitter.value);
  });
})();

// Account menu: native disclosure with predictable dismissal and focus restoration.
document.addEventListener('click', event => {
  const menu = document.querySelector('.account-menu[open]');
  if (menu && !menu.contains(event.target)) menu.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const menu = document.querySelector('.account-menu[open]');
  if (menu) { menu.open = false; menu.querySelector('summary').focus(); }
});

// Same-origin previews retain the deal's access checks. Escape closes the dialog.
document.addEventListener('click', event => {
  const link = event.target.closest('[data-preview]');
  if (!link) return;
  event.preventDefault();
  const dialog = document.getElementById('material-preview');
  const frame = dialog.querySelector('iframe');
  document.getElementById('preview-title').textContent = link.dataset.title;
  frame.src = link.href;
  dialog.showModal();
  dialog.querySelector('button').focus();
  dialog.addEventListener('close', () => { frame.src = 'about:blank'; link.focus(); }, {once:true});
});

// Task context floats outside the scrolling board so columns cannot clip it.
(() => {
  let active = null, timer;
  const hide = () => {
    clearTimeout(timer);
    if (active) active.tip.hidden = true;
    active = null;
  };
  const scheduleHide = () => { clearTimeout(timer); timer = setTimeout(hide, 180); };
  document.querySelectorAll('[data-task-hover]').forEach(link => {
    const tip = document.getElementById(link.dataset.taskHover);
    if (!tip) return;
    document.body.append(tip);
    const show = () => {
      hide(); active = {link, tip}; tip.hidden = false;
      const rect = link.getBoundingClientRect();
      const width = tip.offsetWidth, height = tip.offsetHeight;
      tip.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
      const below = rect.bottom + 8;
      tip.style.top = `${Math.max(12, below + height <= window.innerHeight - 12 ? below : rect.top - height - 8)}px`;
    };
    link.addEventListener('mouseenter', show);
    link.addEventListener('mouseleave', scheduleHide);
    link.addEventListener('focus', show);
    link.addEventListener('blur', hide);
    link.addEventListener('click', hide);
    tip.addEventListener('mouseenter', () => clearTimeout(timer));
    tip.addEventListener('mouseleave', scheduleHide);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  document.addEventListener('dragstart', hide);
  document.addEventListener('scroll', event => { if (active && !active.tip.contains(event.target)) hide(); }, true);
  window.addEventListener('resize', hide);
})();

// Nested move menu: pointer hover, native click/tap, and keyboard disclosure.
(() => {
  document.querySelectorAll('.card-move-menu').forEach(menu => {
    const panel = menu.querySelector('.card-stage-form');
    const summary = menu.querySelector('summary');
    let timer;
    const position = () => {
      if (!menu.open) return;
      const r = summary.getBoundingClientRect();
      const width = panel.offsetWidth, height = panel.offsetHeight;
      panel.style.left = `${Math.max(8, Math.min(r.right + width + 8 <= innerWidth ? r.right + 2 : r.left - width - 2, innerWidth - width - 8))}px`;
      panel.style.top = `${Math.max(8, Math.min(r.top, innerHeight - height - 8))}px`;
    };
    menu.addEventListener('pointerenter', e => { if (e.pointerType === 'touch') return; clearTimeout(timer); menu.open = true; position(); });
    menu.addEventListener('pointerleave', () => { timer = setTimeout(() => { if (!menu.contains(document.activeElement)) menu.open = false; }, 180); });
    summary.addEventListener('click', event => { event.preventDefault(); clearTimeout(timer); menu.open = true; position(); });
    menu.addEventListener('toggle', position);
    menu.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight') { e.preventDefault(); menu.open = true; position(); panel.querySelector('button:not(:disabled)').focus(); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); menu.open = false; summary.focus(); }
    });
    menu.closest('.card-menu').addEventListener('toggle', e => { if (!e.target.open) menu.open = false; });
    window.addEventListener('resize', () => { menu.open = false; });
    document.addEventListener('scroll', () => { menu.open = false; }, true);
  });
})();

// Contextual editors share the existing forms, permissions and version checks.
(() => {
  const dialog = document.getElementById('record-editor');
  if (!dialog) return;
  const content = dialog.querySelector('.editor-content');
  let current = null;
  const headers = {'X-Overdrive-Editor': 'true'};
  const toast = document.querySelector('.save-toast');
  const say = message => { toast.textContent = message; toast.hidden = false; setTimeout(() => toast.hidden = true, 4500); };
  const saved = sessionStorage.getItem('overdrive:saved');
  if (saved) { sessionStorage.removeItem('overdrive:saved'); say(saved); }
  function dismiss(force = false) {
    if (!current) return true;
    if (current.busy || (!force && current.dirty && !confirm('Discard your unsaved changes?'))) return false;
    const previous = current; current = null;
    if (previous.inline) { previous.inline.innerHTML = previous.original; previous.inline.classList.remove('is-editing'); }
    else dialog.close();
    previous.trigger?.focus();
    return true;
  }
  function prepare(container, outcome) {
    const form = container.querySelector('[data-record-form]');
    if (!form) throw new Error('The editor could not be loaded. Please reload the page.');
    if (outcome) { form.elements.outcome.value = outcome; current.dirty = true; }
    const outcomeInput = form.elements.outcome;
    if (outcomeInput) {
      const reason = form.elements.loss_reason?.closest('.form-field');
      const sync = () => { if (reason) reason.hidden = outcomeInput.value !== 'lost'; };
      outcomeInput.addEventListener('change', sync); sync();
    }
    container.querySelector('input:not([type=hidden]),select,textarea')?.focus();
  }
  async function launch(link) {
    if (!dismiss()) return;
    const inline = link.hasAttribute('data-inline') ? link.closest('.inline-property') : null;
    const box = inline || content;
    current = {trigger: link, inline, original: inline?.innerHTML, dirty: false, busy: false};
    const editor = current;
    box.innerHTML = '<p class="editor-loading" role="status">Getting things ready…</p>';
    if (inline) inline.classList.add('is-editing');
    else { dialog.showModal(); dialog.querySelector('[data-editor-dismiss]').focus(); }
    try {
      const response = await fetch(link.href, {headers});
      if (!response.ok || response.redirected) throw new Error('This record is unavailable or your session expired. Reload the page to continue.');
      const html = await response.text();
      if (current !== editor) return;
      box.innerHTML = html;
      prepare(box, link.dataset.outcome);
    } catch (error) {
      if (current !== editor) return;
      dismiss(true); say(error.message);
    }
  }
  document.addEventListener('click', event => {
    const cancel = event.target.closest('[data-editor-cancel],[data-editor-dismiss]');
    if (cancel) { event.preventDefault(); dismiss(); return; }
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || !/^\/(edit|new)\//.test(url.pathname) || link.closest('#record-editor')) return;
    // Keep a separate modal (e.g. commands) from obscuring the drawer.
    link.closest('dialog[open]')?.close();
    event.preventDefault(); launch(link);
  });
  document.addEventListener('input', event => { if (current && (current.inline || content).contains(event.target)) current.dirty = true; });
  document.addEventListener('change', event => { if (current && (current.inline || content).contains(event.target)) current.dirty = true; });
  dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && current?.inline) { event.preventDefault(); dismiss(); }
  });
  window.addEventListener('beforeunload', event => { if (current?.dirty) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!current || !form.matches('[data-record-form]') || !(current.inline || content).contains(form)) return;
    event.preventDefault();
    if (current.busy) return;
    const editor = current, box = editor.inline || content;
    editor.busy = true;
    const payload = new FormData(form);
    const submit = event.submitter;
    if (submit) { submit.disabled = true; submit.textContent = 'Saving…'; }
    try {
      const response = await fetch(form.action, {method: 'POST', headers, body: payload});
      if (!response.ok) {
        const html = await response.text();
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        if (parsed.querySelector('[data-record-form]')) {
          box.innerHTML = html; prepare(box); box.querySelector('[role=alert]')?.scrollIntoView({block:'nearest'});
          editor.dirty = true;
          return;
        }
        throw new Error(response.status === 401 ? 'Your session expired. Your draft is still here; sign in in another tab and retry.' : 'Could not save. Your draft is still here. Please try again.');
      }
      const result = await response.json();
      editor.dirty = false;
      if (editor.inline) {
        // Re-render only the edited value, with the server's formatting and escaping.
        const fresh = await fetch(location.href);
        if (!fresh.ok || fresh.redirected) throw new Error('Saved. Reload the page to see the current values.');
        const page = new DOMParser().parseFromString(await fresh.text(), 'text/html');
        const key = editor.inline.dataset.recordProperty;
        const replacement = [...page.querySelectorAll('[data-record-property]')].find(el => el.dataset.recordProperty === key);
        if (!replacement) throw new Error('Saved. Reload the page to see the current values.');
        document.querySelectorAll('[data-record-property]').forEach(old => {
          if (!old.dataset.recordProperty.startsWith(result.record.id + '/')) return;
          const freshValue = [...page.querySelectorAll('[data-record-property]')].find(el => el.dataset.recordProperty === old.dataset.recordProperty);
          if (freshValue) old.replaceWith(freshValue.cloneNode(true));
        });
        current = null;
        [...document.querySelectorAll('[data-record-property]')].find(el => el.dataset.recordProperty === key)?.querySelector('a')?.focus();
        // Refresh complete forms (draft and version together), never just their version.
        const progress = document.querySelector('.cockpit-progress');
        if (progress) progress.replaceWith(page.querySelector('.cockpit-progress'));
        const summary = document.getElementById('edit-deal-status');
        if (summary) summary.innerHTML = page.getElementById('edit-deal-status').innerHTML;
        for (const selector of ['.notes-timeline', '.audit-feed', '.relationship-history', '.relationship-monogram', '.org-monogram', '.cockpit-rail > h2', '.cockpit-heading .eyebrow']) {
          const old = document.querySelector(selector), freshSection = page.querySelector(selector);
          if (old && freshSection) old.innerHTML = freshSection.innerHTML;
        }
        document.title = page.title;
        say('Saved. Keep it moving.');
      } else {
        sessionStorage.setItem('overdrive:saved', 'Saved. Keep it moving.');
        const creating = !payload.get('id');
        location.assign(creating ? result.target : location.href);
      }
    } catch (error) { say(error.message || 'Connection lost. Your draft is still here. Retry to check and save.'); }
    finally {
      editor.busy = false;
      form.removeAttribute('aria-busy');
      if (submit) { submit.disabled = false; submit.classList.remove('submitting'); submit.textContent = 'Save changes'; }
    }
  });

})();
