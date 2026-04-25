import { cx } from '../../../ui';
import type { GuardsStatus } from './types';

export function renderFirewallStats(container: HTMLElement, guardsStatus: GuardsStatus): void {
    const fw = guardsStatus.firewall ?? {};
    const scanned = fw.total_scanned ?? 0;
    const blocked = fw.total_blocked ?? 0;
    const signatures = fw.block_by_signature ?? {};

    container.replaceChildren();
    container.dataset.ready = '1';
    container.setAttribute('data-testid', 'firewall-stats');

    const top = document.createElement('div');
    top.className = 'flex items-center gap-6 mb-2';

    const scannedBox = document.createElement('div');
    const scannedNum = document.createElement('span');
    scannedNum.className = 'text-lg font-black font-mono text-white';
    scannedNum.textContent = scanned.toLocaleString();
    scannedNum.setAttribute('data-testid', 'firewall-scanned');
    const scannedLab = document.createElement('span');
    scannedLab.className = 'text-[9px] text-slate-500 ml-1';
    scannedLab.textContent = 'scanned';
    scannedBox.appendChild(scannedNum);
    scannedBox.appendChild(scannedLab);
    top.appendChild(scannedBox);

    const blockedBox = document.createElement('div');
    const blockedNum = document.createElement('span');
    blockedNum.className = cx('text-lg font-black font-mono', blocked > 0 ? 'text-rose-400' : 'text-emerald-400');
    blockedNum.textContent = blocked.toLocaleString();
    blockedNum.setAttribute('data-testid', 'firewall-blocked');
    const blockedLab = document.createElement('span');
    blockedLab.className = 'text-[9px] text-slate-500 ml-1';
    blockedLab.textContent = 'blocked';
    blockedBox.appendChild(blockedNum);
    blockedBox.appendChild(blockedLab);
    top.appendChild(blockedBox);

    container.appendChild(top);

    const sigEntries = Object.entries(signatures);
    if (sigEntries.length > 0) {
        const sigList = document.createElement('div');
        sigList.className = 'space-y-1 mt-2 pt-2 border-t border-white/[0.04]';
        sigList.setAttribute('data-testid', 'firewall-signatures');
        for (const [sig, count] of sigEntries) {
            const row = document.createElement('div');
            row.className = 'flex items-center justify-between';
            const sigSpan = document.createElement('span');
            sigSpan.className = 'text-[10px] font-mono text-slate-500 truncate max-w-[250px]';
            sigSpan.textContent = sig;
            const countSpan = document.createElement('span');
            countSpan.className = 'text-[10px] font-mono text-rose-400';
            countSpan.textContent = `${count}x`;
            row.appendChild(sigSpan);
            row.appendChild(countSpan);
            sigList.appendChild(row);
        }
        container.appendChild(sigList);
    }
}
