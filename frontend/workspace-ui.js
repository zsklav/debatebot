/* Accessible tabs: changing tools preserves in-progress inputs and results. */
(() => {
  const names = ['debate', 'evidence', 'critique'];
  function select(name, focus = false) {
    for (const key of names) {
      const tab = document.getElementById(`tab-${key}`);
      const selected = key === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      document.getElementById(`view-${key}`).hidden = !selected;
      if (selected && focus) tab.focus();
    }
  }
  names.forEach((name, index) => {
    const tab = document.getElementById(`tab-${name}`);
    tab.addEventListener('click', () => select(name));
    tab.addEventListener('keydown', event => {
      const targets = {ArrowRight: (index + 1) % names.length,
        ArrowLeft: (index + names.length - 1) % names.length, Home: 0, End: names.length - 1};
      if (!(event.key in targets)) return;
      event.preventDefault();
      select(names[targets[event.key]], true);
    });
  });
  window.addEventListener('debatebot:authchanged', event => {
    if (event.detail.reset) select('debate');
  });
})();
