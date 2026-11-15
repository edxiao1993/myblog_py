(() => {
  const key = 'app-theme';
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  let saved = null, toggles = [];
  try {
    saved = localStorage.getItem(key);
    if (saved !== 'dark' && saved !== 'light') saved = localStorage.getItem('coding-agent-theme');
  } catch (_) { /* Storage can be unavailable. */ }
  if (saved !== 'dark' && saved !== 'light') saved = null;
  function apply(dark) {
    const theme = dark ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.bsTheme = theme;
    toggles.forEach(toggle => { toggle.checked = dark; });
  }
  apply(saved ? saved === 'dark' : preference.matches);
  function bind() {
    toggles = Array.from(document.querySelectorAll('[data-theme-toggle]'));
    apply(saved ? saved === 'dark' : preference.matches);
    toggles.forEach(toggle => toggle.addEventListener('change', () => {
      saved = toggle.checked ? 'dark' : 'light';
      apply(toggle.checked);
      try { localStorage.setItem(key, saved); } catch (_) { /* Keep the switch usable. */ }
    }));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
  preference.addEventListener('change', event => { if (!saved) apply(event.matches); });
})();
