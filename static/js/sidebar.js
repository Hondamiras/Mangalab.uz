document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('filterSidebar');
    const main    = document.getElementById('mainContent');
    const overlay = document.getElementById('overlay');
    if (!sidebar || !main || !overlay) return;
  
    function openSidebar() {
      sidebar.classList.remove('-translate-x-full');
      main.classList.add('md:ml-72', 'lg:ml-80');
      overlay.classList.remove('hidden');
    }
    function closeSidebar() {
      sidebar.classList.add('-translate-x-full');
      main.classList.remove('md:ml-72', 'lg:ml-80');
      overlay.classList.add('hidden');
    }
  
    document.querySelectorAll('.toggle-filters').forEach(btn => {
      btn.addEventListener('click', () =>
        sidebar.classList.contains('-translate-x-full') ? openSidebar() : closeSidebar()
      );
    });
    overlay.addEventListener('click', closeSidebar);
  });
  document.querySelectorAll('.filter-header').forEach(header => {
    header.addEventListener('click', () => {
      const content = header.nextElementSibling;
      const arrow   = header.querySelector('.filter-arrow');
      content.classList.toggle('hidden');
      arrow.classList.toggle('rotate-180');
    });
  });
    