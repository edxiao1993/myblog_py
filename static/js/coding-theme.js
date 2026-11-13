(() => {
  const key = 'coding-agent-theme';
  const toggle = document.getElementById('coding-theme');
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) { /* Storage can be unavailable. */ }
  if (saved !== 'dark' && saved !== 'light') saved = null;
  function apply(dark) {
    document.body.dataset.codingTheme = dark ? 'dark' : 'light';
    toggle.checked = dark;
  }
  apply(saved ? saved === 'dark' : preference.matches);
  toggle.addEventListener('change', () => {
    saved = toggle.checked ? 'dark' : 'light';
    apply(toggle.checked);
    try { localStorage.setItem(key, saved); } catch (_) { /* Keep the current page usable. */ }
  });
  preference.addEventListener('change', event => { if (!saved) apply(event.matches); });
})();
