/**
 * Logs Component
 */
import { store } from '../services/store.js';

export function initLogs() {
    const BASE_URL = window.location.origin;
    if (store.state.logSource) store.state.logSource.close();
    
    const logSource = new EventSource(`${BASE_URL}/api/v1/logs`);
    store.update({ logSource });
    
    logSource.onmessage = (event) => {
        const entry = JSON.parse(event.data);
        appendLog(entry);
    };
}

function appendLog(log) {
    const container = document.getElementById('terminal-logs');
    if (!container) return;
    
    if (container.children.length === 1 && container.children[0].classList.contains('italic')) {
        container.innerHTML = '';
    }
    
    const div = document.createElement('div');
    div.className = "flex gap-4 animate-in fade-in slide-in-from-left-1";
    const levelColor = log.level === 'PROXY' ? 'text-sky-400 font-black' : 
                      log.level === 'ERROR' ? 'text-red-400' : 
                      log.level === 'SYSTEM' ? 'text-amber-400' : 'text-slate-500';
                      
    div.innerHTML = `
        <span class="text-slate-600 shrink-0">${log.timestamp}</span>
        <span class="w-12 tracking-widest text-[8px] uppercase font-black ${levelColor} shrink-0">${log.level}</span>
        <span class="text-slate-300 break-all">${log.message}</span>
    `;
    
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    if (container.children.length > 50) container.removeChild(container.firstChild);
}

export function clearLogs() {
    const container = document.getElementById('terminal-logs');
    if (container) container.innerHTML = '<div class="text-slate-500 italic opacity-50">Log stream cleared.</div>';
}
