(() => {
  const copyPassword = document.getElementById('copy-manual-password');
  if (copyPassword) copyPassword.onclick = async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById('manual-password').value);
      copyPassword.textContent = 'Copied';
    } catch { copyPassword.textContent = 'Select the password field and copy manually'; }
  };
  const data = JSON.parse(document.getElementById('setup-builder-data').textContent);
  let selected = data.selected;
  const chain = document.getElementById('setup-chain');
  const add = key => {
    for (const prerequisite of data.sites[key].requires) add(prerequisite);
    if (!selected.includes(key)) selected.push(key);
  };
  function render() {
    document.getElementById('setup-sites').value = selected.join(',');
    chain.replaceChildren();
    for (const key of selected) {
      const row = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = data.sites[key].title;
      const detail = document.createElement('p');
      detail.textContent = data.sites[key].skill;
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'quiet'; remove.textContent = 'Remove';
      remove.setAttribute('aria-label', 'Remove ' + data.sites[key].title);
      remove.onclick = () => {
        const removed = new Set([key]);
        for (const candidate of selected) {
          if (data.sites[candidate].requires.some(dep => removed.has(dep))) removed.add(candidate);
        }
        selected = selected.filter(site => !removed.has(site)); render();
      };
      row.append(title, remove, detail); chain.append(row);
    }
    document.getElementById('setup-prompt').textContent = selected.map((key,i) => `${i+1}. ${data.sites[key].skill}`).join('\n\n');
    document.querySelectorAll('[data-add-site]').forEach(button => {
      button.disabled = selected.includes(button.dataset.addSite);
      button.setAttribute('aria-pressed', String(button.disabled));
    });
  }
  document.querySelectorAll('[data-add-site]').forEach(button => {
    button.onclick = () => { add(button.dataset.addSite); render(); };
  });
  render();
})();
