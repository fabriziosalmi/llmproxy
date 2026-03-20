/**
 * Chat Component
 */
import { store } from '../services/store.js';
import { api } from '../services/api.js';

export function initChat() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { 
            e.preventDefault(); 
            sendUserMessage(); 
        }
    });

    document.querySelector('.relative.group button').addEventListener('click', sendUserMessage);
}

async function sendUserMessage() {
    const input = document.getElementById('chat-input');
    const scroller = document.getElementById('chat-scroller');
    const text = input.value.trim();
    if (!text) return;
    
    input.value = '';
    appendMessage('user', text);
    
    const thinkingDiv = appendMessage('system', '...');
    thinkingDiv.classList.add('animate-pulse');
    
    try {
        const response = await api.sendChatMessage(text);
        const data = await response.json();
        scroller.removeChild(thinkingDiv);
        
        if (response.status === 200) {
            appendMessage('bot', data.choices[0].message.content);
        } else {
            appendMessage('error', data.detail || 'Access Denied');
        }
    } catch (e) {
        scroller.removeChild(thinkingDiv);
        appendMessage('error', 'Connection failed');
    }
}

function appendMessage(type, text) {
    const scroller = document.getElementById('chat-scroller');
    const div = document.createElement('div');
    
    if (type === 'user') {
        div.className = 'flex justify-end gap-4 animate-in fade-in slide-in-from-right-2';
        div.innerHTML = `<div class="p-5 bg-sky-500 border border-sky-400 rounded-3xl rounded-tr-sm text-sm leading-relaxed max-w-[85%] text-white shadow-2xl shadow-sky-500/20">${text}</div>`;
    } else if (type === 'bot') {
        div.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        div.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-xs shrink-0">P</div>
            <div class="p-5 bg-white/[0.03] border border-white/5 rounded-3xl rounded-tl-sm text-sm leading-relaxed max-w-[85%] text-slate-200">${text}</div>`;
    } else if (type === 'error') {
        div.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        div.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-red-500/10 border border-red-500/30 flex items-center justify-center text-red-400 font-bold text-xs shrink-0">!</div>
            <div class="p-5 bg-red-500/5 border border-red-500/20 rounded-3xl rounded-tl-sm text-sm leading-relaxed max-w-[85%] text-red-200">${text}</div>`;
    } else {
        div.className = 'flex gap-5 items-start';
        div.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 font-bold text-xs shrink-0">${text}</div>`;
    }
    
    scroller.appendChild(div);
    scroller.scrollTop = scroller.scrollHeight;
    return div;
}
