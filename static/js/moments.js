document.querySelectorAll('[data-moment-toggle]').forEach(button => {
  const content = button.closest('[data-moment-content]');
  const full = content.querySelector('[data-moment-full]');
  let expanded = false;

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
