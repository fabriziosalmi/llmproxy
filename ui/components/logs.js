/**
 * Logs Component
 */
import { store } from '../services/store.js';

let isHovering = false;
let missedCount = 0;

export function initLogs() {
    const BASE_URL = window.location.origin;
    if (store.state.logSource) store.state.logSource.close();
    
    const logSource = new EventSource(`${BASE_URL}/api/v1/logs`);
    store.update({ logSource });
    
    const container = document.getElementById('terminal-logs');
    if (container) {
        container.addEventListener('mouseenter', () => { isHovering = true; });
        container.addEventListener('mouseleave', () => { 
            isHovering = false; 
            missedCount = 0;
            updatePausedBadge();
            container.scrollTop = container.scrollHeight;
        });
    }

    const filterInput = document.getElementById('log-pipe-filter');
    if (filterInput) {
        filterInput.addEventListener('input', () => {
            const pipes = filterInput.value.split('|').map(s => s.trim().toLowerCase()).filter(s => s);
            const logs = document.querySelectorAll('.log-entry');
            logs.forEach(log => {
                const text = log.innerText.toLowerCase();
                const matches = pipes.every(p => text.includes(p.replace(/grep['" ]+/g, '').replace(/['"]/g, '')));
                log.style.display = (pipes.length === 0 || matches) ? 'flex' : 'none';
            });
            container.scrollTop = container.scrollHeight;
        });
    }

    const clearBtn = document.getElementById('clear-logs-btn');
    if (clearBtn) clearBtn.addEventListener('click', clearLogs);

    logSource.onmessage = (event) => {
        try {
            const entry = JSON.parse(event.data);
            appendLog(entry);
        } catch (e) {
            console.error('Failed to parse log entry:', e);
        }
    };
}

function updatePausedBadge() {
    const badge = document.getElementById('log-paused-badge');
    const count = document.getElementById('log-missed-count');
    if (!badge || !count) return;
    
    if (isHovering && missedCount > 0) {
        badge.classList.remove('hidden', 'opacity-0');
        count.innerText = missedCount;
    } else {
        badge.classList.add('opacity-0');
        setTimeout(() => badge.classList.add('hidden'), 200);
    }
}

function syntaxHighlight(msg) {
    if (typeof msg !== 'string') return msg;
    // Highlight JSON-like structures
    if (msg.includes('{') || msg.includes('[')) {
        return msg.replace(/"(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?/g, function (match) {
            let cls = 'text-amber-400';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) {
                    cls = 'text-sky-400 font-bold'; // key
                } else {
                    cls = 'text-emerald-300'; // string
                }
            } else if (/true|false/.test(match)) {
                cls = 'text-purple-400 font-bold'; // boolean
            } else if (/null/.test(match)) {
                cls = 'text-slate-500 italic'; // null
            } else {
                cls = 'text-orange-400'; // number
            }
            return '<span class="' + cls + '">' + match + '</span>';
        });
    }
    // Highlighting generic path/identifiers
    return msg.replace(/(['"])(.*?)\1/g, '<span class="text-emerald-300">$1$2$1</span>');
}

function appendLog(log) {
    const container = document.getElementById('terminal-logs');
    if (!container) return;
    
    if (container.children.length === 1 && container.children[0].classList.contains('italic')) {
        container.innerHTML = '';
    }
    
    const div = document.createElement('div');
    div.className = "log-entry flex gap-4 animate-in fade-in slide-in-from-left-1 group p-1.5 -mx-1.5 rounded hover:bg-white/5 transition-colors cursor-pointer";
    div.onclick = () => console.log('Log details:', log); // Interactive detail preparation
    
    const levelColor = log.level === 'PROXY' ? 'text-sky-400 font-black' : 
                      log.level === 'ERROR' ? 'text-red-400' : 
                      log.level === 'SYSTEM' ? 'text-amber-400' : 'text-slate-500';
                      
    const highlightedMsg = syntaxHighlight(log.message || '');
    
    div.innerHTML = `
        <span class="text-slate-600 shrink-0 select-none">${log.timestamp || new Date().toISOString().split('T')[1].slice(0,-1)}</span>
        <span class="w-12 tracking-widest text-[8px] uppercase font-black ${levelColor} shrink-0 select-none">${log.level || 'INFO'}</span>
        <span class="text-slate-300 break-all font-medium leading-relaxed">${highlightedMsg}</span>
    `;
    
    // Filter check
    const filterInput = document.getElementById('log-pipe-filter');
    if (filterInput && filterInput.value.trim() !== '') {
        const pipes = filterInput.value.split('|').map(s => s.trim().toLowerCase()).filter(s => s);
        const text = div.innerText.toLowerCase();
        const matches = pipes.every(p => text.includes(p.replace(/grep['" ]+/g, '').replace(/['"]/g, '')));
        if (!matches) div.style.display = 'none';
    }

    container.appendChild(div);
    
    if (isHovering) {
        missedCount++;
        updatePausedBadge();
    } else {
        container.scrollTop = container.scrollHeight;
    }
    
    if (container.children.length > 200) {
        container.removeChild(container.firstChild); // Increase buffer size
    }
}

export function clearLogs() {
    const container = document.getElementById('terminal-logs');
    if (container) container.innerHTML = '<div class="text-slate-500 italic opacity-50 p-2">Log stream cleared.</div>';
}
