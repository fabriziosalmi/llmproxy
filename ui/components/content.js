/**
 * Main Content Component - Orchestrates views
 */
import { store } from '../services/store.js';

export function renderContent() {
    const { currentTab } = store.state;
    
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
}
