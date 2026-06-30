/**
 * Main Content Component - Orchestrates views
 */
import { store } from '../services/store.js';
import { hashParts, hashTab } from '../services/urlstate.js';

const VALID_TABS = ['threats', 'guards', 'plugins', 'endpoints', 'models', 'analytics', 'security', 'logs', 'settings', 'docs'];

export function renderContent() {
    const { currentTab } = store.state;

    // Sync hash without triggering hashchange
    const { view, params } = hashParts();
    if (view !== `#/${currentTab}`) {
        const qs = params.toString();
        history.replaceState(null, '', `#/${currentTab}${qs ? `?${qs}` : ''}`);
    }

    document.querySelectorAll('.content-view').forEach((view) => view.classList.add('hidden'));
    const currentView = document.getElementById(`view-${currentTab}`);
    if (currentView) currentView.classList.remove('hidden');

    document.querySelectorAll('.nav-item').forEach((item) => {
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
    document.querySelectorAll('.nav-item').forEach((item) => {
        const tabId = item.id.replace('nav-', '');
        item.addEventListener('click', () => {
            store.update({ currentTab: tabId });
        });
    });

    // Restore tab from URL hash on load
    const initialTab = hashTab();
    if (initialTab && VALID_TABS.includes(initialTab)) {
        store.update({ currentTab: initialTab });
    }

    // Handle browser back/forward
    window.addEventListener('hashchange', () => {
        const tab = hashTab();
        if (tab && VALID_TABS.includes(tab) && tab !== store.state.currentTab) {
            store.update({ currentTab: tab });
        }
    });
}
