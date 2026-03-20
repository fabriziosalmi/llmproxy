/**
 * Main Content Component - Orchestrates views
 */
import { store } from '../services/store.js';

let previousTab = null;

function createSkeleton() {
    const el = document.createElement('div');
    el.className = 'skeleton-shimmer';
    // Row heights mimic typical card layouts
    const rows = [
        { w: '35%', h: '20px' },
        { w: '100%', h: '120px' },
        { w: '100%', h: '80px' },
        { w: '60%', h: '16px' },
        { w: '80%', h: '16px' },
    ];
    rows.forEach(r => {
        const bar = document.createElement('div');
        bar.className = 'skeleton-bar';
        bar.style.width = r.w;
        bar.style.height = r.h;
        el.appendChild(bar);
    });
    return el;
}

export function renderContent() {
    const { currentTab } = store.state;

    // Show shimmer skeleton on view switch
    const currentView = document.getElementById(`view-${currentTab}`);
    if (currentView && previousTab !== null && previousTab !== currentTab) {
        currentView.style.position = 'relative';
        const skeleton = createSkeleton();
        currentView.appendChild(skeleton);
        setTimeout(() => {
            skeleton.style.opacity = '0';
            skeleton.style.transition = 'opacity 0.25s ease';
            setTimeout(() => skeleton.remove(), 260);
        }, 280);
    }
    previousTab = currentTab;

    document.querySelectorAll('.content-view').forEach(view => view.classList.add('hidden'));
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
