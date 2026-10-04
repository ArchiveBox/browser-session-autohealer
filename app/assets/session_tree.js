function loadBranch(branch) {
    if (!branch.open || !branch.dataset.load || branch.dataset.loaded) return;
    branch.dataset.loaded = 'loading';
    const target = branch.querySelector(':scope > .tree-children');
    htmx.ajax('GET', branch.dataset.load, {target, swap: 'innerHTML'}).then(() => {
        branch.dataset.loaded = 'true';
        target.querySelectorAll('details[open][data-load]').forEach(loadBranch);
    }).catch(() => {
        delete branch.dataset.loaded;
        target.textContent = 'Unable to load. Close and reopen to try again.';
    });
}
document.addEventListener('toggle', event => {
    if (event.target.matches('details[data-load]')) loadBranch(event.target);
}, true);
document.addEventListener('DOMContentLoaded', () => document.querySelectorAll('details[open][data-load]').forEach(loadBranch));
function scrub(timeline, index) {
    const frames = JSON.parse(timeline.dataset.frames), frame = frames[index];
    timeline.querySelector('.timeline-image').src = frame.url;
    timeline.querySelector('.timeline-image').alt = frame.label;
    timeline.querySelector('.timeline-full').href = frame.url;
    timeline.querySelector('.timeline-caption').textContent = frame.label;
    timeline.querySelector('input').value = index;
    timeline.querySelectorAll('[data-frame]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.frame) === Number(index))));
}
document.addEventListener('input', event => {
    if (event.target.matches('.evidence-timeline input')) scrub(event.target.closest('.evidence-timeline'), event.target.value);
});
document.addEventListener('click', event => {
    const button = event.target.closest('[data-frame]');
    if (button) scrub(button.closest('.evidence-timeline'), button.dataset.frame);
});
