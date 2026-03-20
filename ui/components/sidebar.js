/**
 * Sidebar Component
 */
import { store } from '../services/store.js';

export function renderSidebar() {
    const { isCollapsed } = store.state;
    const sidebar = document.getElementById('sidebar');
    const icon = document.getElementById('toggle-icon-svg');
    
    if (isCollapsed) {
        sidebar.classList.add('collapsed');
        icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>';
    } else {
        sidebar.classList.remove('collapsed');
        icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 19l-7-7 7-7m8 14l-7-7 7-7"/>';
    }
}

export function initSidebar() {
    const btn = document.getElementById('sidebar-toggle-btn');
    if (btn) {
        btn.addEventListener('click', () => {
            store.update({ isCollapsed: !store.state.isCollapsed });
        });
    }

    // Mobile hamburger
    const mobileBtn = document.getElementById('mobile-menu-btn');
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (mobileBtn && sidebar && backdrop) {
        mobileBtn.addEventListener('click', () => {
            const isHidden = sidebar.classList.contains('mobile-hidden');
            if (isHidden) {
                sidebar.classList.remove('mobile-hidden');
                backdrop.classList.remove('hidden');
            } else {
                sidebar.classList.add('mobile-hidden');
                backdrop.classList.add('hidden');
            }
        });
    }

    // Close sidebar when ANY nav item is clicked on mobile
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            if (window.innerWidth < 768) {
                const sb = document.getElementById('sidebar');
                const bd = document.getElementById('sidebar-backdrop');
                if (sb) sb.classList.add('mobile-hidden');
                if (bd) bd.classList.add('hidden');
            }
        });
    });
}
