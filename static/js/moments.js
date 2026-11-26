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

  button.addEventListener('click', () => {
    expanded = !expanded;
    update();
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
