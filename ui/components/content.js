/**
 * Main Content Component - Orchestrates views
 */
import { store } from '../services/store.js';

const VALID_TABS = ['threats', 'guards', 'plugins', 'endpoints', 'models', 'analytics', 'security', 'logs', 'settings'];

export function renderContent() {
    const { currentTab } = store.state;

    // Sync hash without triggering hashchange
    if (window.location.hash !== `#/${currentTab}`) {
        history.replaceState(null, '', `#/${currentTab}`);
    }

    document.querySelectorAll('.content-view').forEach(view => view.classList.add('hidden'));
    const currentView = document.getElementById(`view-${currentTab}`);
    if (currentView) currentView.classList.remove('hidden');

    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
        item.classList.add('text-slate-500');
    });

    const targetNav = document.getElementById(`nav-${currentTab}`);
    if (targetNav) {
        targetNav.classList.add('active');
        targetNav.classList.remove('text-slate-500');
    }

    const viewTitleText = document.getElementById('view-title-text');
    if (viewTitleText) {
        viewTitleText.textContent = currentTab.charAt(0).toUpperCase() + currentTab.slice(1);
    }
}

export function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        const tabId = item.id.replace('nav-', '');
        item.addEventListener('click', () => {
            store.update({ currentTab: tabId });
        });
    });

    // Restore tab from URL hash on load
    const hashTab = window.location.hash.replace('#/', '');
    if (hashTab && VALID_TABS.includes(hashTab)) {
        store.update({ currentTab: hashTab });
    }

    // Handle browser back/forward
    window.addEventListener('hashchange', () => {
        const tab = window.location.hash.replace('#/', '');
        if (tab && VALID_TABS.includes(tab) && tab !== store.state.currentTab) {
            store.update({ currentTab: tab });
        }
    });
}
