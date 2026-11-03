(() => {
  const data = JSON.parse(document.getElementById('i18n-data').textContent);
  const t = (message, values = {}) => (data.messages[message] ?? message)
    .replace(/\{(\w+)\}/g, (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match);
  window.AppI18n = {t, locale: data.locale};
})();
