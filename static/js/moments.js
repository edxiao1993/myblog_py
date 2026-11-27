document.querySelectorAll('[data-moment-toggle]').forEach(button => {
  const content = button.closest('[data-moment-content]');
  const full = content.querySelector('[data-moment-full]');
  let expanded = false;

  // Read escaped text from the DOM; never insert raw user Markdown as HTML.
  if (window.marked && window.DOMPurify) {
    full.innerHTML = window.DOMPurify.sanitize(window.marked.parse(full.textContent, {gfm: true, breaks: true}), {USE_PROFILES: {html: true}});
    full.style.whiteSpace = 'normal';
  }

  const update = () => {
    const limit = parseFloat(window.getComputedStyle(full).lineHeight) * 5;
    const overflowing = full.scrollHeight > limit + 0.5;
    button.hidden = !overflowing;
    full.style.maxHeight = overflowing && !expanded ? `${limit}px` : '';
    full.style.overflow = overflowing && !expanded ? 'hidden' : '';
    button.setAttribute('aria-expanded', String(overflowing && expanded));
    button.textContent = window.AppI18n.t(expanded && overflowing ? 'Show less' : 'Show more');
  };

  const toggle = () => {
    if (button.hidden) return;
    expanded = !expanded;
    update();
  };
  button.addEventListener('click', toggle);

  const card = button.closest('[data-moment-card]');
  const interactive = target => target.closest('a, button, input, textarea, select, label, summary, details, [contenteditable], [role="button"]');
  let touchStart = null, previousTap = null, lastTouchToggle = -Infinity;
  card.addEventListener('dblclick', event => {
    // A touch double-tap may also generate a native dblclick. Toggle only once.
    if (Date.now() - lastTouchToggle < 700 || interactive(event.target)) return;
    toggle();
  });
  card.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'touch') return;
    if (event.isPrimary === false || interactive(event.target)) {
      touchStart = previousTap = null;
      return;
    }
    touchStart = {id: event.pointerId, x: event.clientX, y: event.clientY, time: Date.now()};
  });
  card.addEventListener('pointercancel', () => { touchStart = previousTap = null; });
  card.addEventListener('pointerup', event => {
    if (event.pointerType !== 'touch') return;
    const start = touchStart, now = Date.now();
    touchStart = null;
    if (!start || event.pointerId !== start.id || interactive(event.target)
        || now - start.time > 500 || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) {
      previousTap = null;
      return;
    }
    if (previousTap && now - previousTap.time < 350
        && Math.hypot(event.clientX - previousTap.x, event.clientY - previousTap.y) < 24) {
      previousTap = null;
      lastTouchToggle = now;
      toggle();
      if (!button.hidden) event.preventDefault();
    } else previousTap = {time: now, x: event.clientX, y: event.clientY};
  });
  update();
  full.addEventListener('load', update, true); // Images can change the rendered height.
  full.addEventListener('error', update, true);
  window.addEventListener('resize', update);
  if (window.ResizeObserver) {
    let width = full.getBoundingClientRect().width;
    const observer = new window.ResizeObserver(() => {
      const nextWidth = full.getBoundingClientRect().width;
      if (nextWidth !== width) {
        width = nextWidth;
        update();
      }
    });
    observer.observe(full);
  }
  if (document.fonts) document.fonts.ready.then(update);
});
const addModal = document.getElementById?.('addMomentModal');
const modalInput = document.getElementById?.('moment-modal-input');
if (addModal && modalInput) {
  addModal.addEventListener('shown.bs.modal', () => modalInput.focus());
  modalInput.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      modalInput.form?.requestSubmit();
    }
  });
}
