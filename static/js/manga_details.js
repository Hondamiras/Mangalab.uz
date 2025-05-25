
function getCSRF() {
  return document.querySelector('meta[name="csrf-token"]').content;
}
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('bookmark-btn');
  if (!btn) return;

  btn.addEventListener('click', async e => {
    e.preventDefault();

    try {
      const res = await fetch(btn.dataset.url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': getCSRF(),
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const { bookmarked } = await res.json();
      btn.dataset.bookmarked = bookmarked;

      /* --- Блок работы с иконкой, безопасный для <i> и <svg> --- */
      const icon = btn.querySelector('i, svg');   // ищем оба варианта
      if (icon) {
        icon.classList.toggle('fa-solid',   bookmarked);
        icon.classList.toggle('fa-regular', !bookmarked);
      }

      /* --- Текст и цвета --- */
      btn.querySelector('span').textContent = bookmarked ? 'Saved' : 'Save to Library';
      btn.classList.toggle('text-purple-400', bookmarked);
      btn.classList.toggle('border-purple-500/50', bookmarked);
      btn.classList.toggle('text-gray-300', !bookmarked);
      btn.classList.toggle('border-gray-700', !bookmarked);

      /* Короткая анимация */
      btn.classList.add('animate-pulse');
      setTimeout(() => btn.classList.remove('animate-pulse'), 400);

    } catch (err) {
      console.error('Bookmark toggle failed:', err);
    }
  });
});

/* ---------- TABS ---------- */
const tabBody = document.getElementById('tab-body');
const nav     = document.getElementById('tabs-nav');

if (nav && tabBody) {
  nav.addEventListener('click', async (e) => {
    const link = e.target.closest('a');      // кликнули по <a>?
    if (!link || !nav.contains(link)) return;

    e.preventDefault();                      // останавливаем обычный переход

    // подсветка активной
    Array.from(nav.querySelectorAll('a')).forEach(a => {
      a.classList.remove('text-purple-400', 'bg-gray-800/50');
      a.classList.add   ('text-gray-400');
    });
    link.classList.add   ('text-purple-400', 'bg-gray-800/50');
    link.classList.remove('text-gray-400');

    try {
      const res = await fetch(link.href, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      });
      if (!res.ok) throw new Error(res.status);

      const html   = await res.text();
      const doc    = new DOMParser().parseFromString(html, 'text/html');
      const block  = doc.getElementById('tab-body');
      if (block) tabBody.innerHTML = block.innerHTML;

    } catch (err) {
      console.error('Tab load failed:', err);
    }
  });
}
