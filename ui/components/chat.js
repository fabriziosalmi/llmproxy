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

    document.querySelector('#nav-chat').addEventListener('click', () => {
        setTimeout(() => input.focus(), 100);
    });

    // 20. Regenerate Key (Double Click Protection)
    const regenBtn = document.getElementById('regenerate-key-btn');
    if (regenBtn) {
        let clickCount = 0;
        regenBtn.addEventListener('click', () => {
            clickCount++;
            if (clickCount === 1) {
                regenBtn.innerText = "CONFIRM?";
                regenBtn.classList.add('bg-rose-500/40', 'text-white');
                setTimeout(() => {
                    clickCount = 0;
                    regenBtn.innerText = "REGENERATE";
                    regenBtn.classList.remove('bg-rose-500/40', 'text-white');
                }, 3000);
            } else if (clickCount === 2) {
                regenBtn.innerText = "REGENERATING...";
                regenBtn.disabled = true;
                setTimeout(() => {
                    document.getElementById('ops-master-key').value = `sk-proxy-${Math.random().toString(36).substring(2, 12)}`;
                    regenBtn.innerText = "SUCCESS";
                    setTimeout(() => {
                        regenBtn.innerText = "REGENERATE";
                        regenBtn.disabled = false;
                        regenBtn.classList.remove('bg-rose-500/40', 'text-white');
                        clickCount = 0;
                    }, 2000);
                }, 1000);
            }
        });
    }
}

async function sendUserMessage() {
    const input = document.getElementById('chat-input');
    const scroller = document.getElementById('chat-scroller');
    const text = input.value.trim();
    if (!text) return;
    
    const triggerWord = text.toLowerCase().match(/(hack|ignore|bypass|system prompt|sk-)/);
    
    input.value = '';
    appendMessage('user', text);
    
    // Simulate Guardrail Intervention
    if (triggerWord) {
        setTimeout(() => {
            appendMessage('guardrail', `Analyzing payload stream for cognitive anomalies... Threat signature detected matching rule [CWE-89]. Segmenting context window: "${text.substring(0, 40)}..."`);
        }, 400);
        return;
    }
    
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
        div.className = 'flex justify-end gap-4 animate-in fade-in slide-in-from-right-2 self-end';
        div.innerHTML = `
            <div class="flex flex-col items-end gap-1.5 group">
                <div class="p-5 bg-sky-500/10 border border-sky-500/30 rounded-3xl rounded-tr-sm text-sm leading-relaxed max-w-xl text-sky-100 shadow-[0_8px_30px_rgba(14,165,233,0.1)] font-medium">${text}</div>
                <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity mr-2 bg-white/[0.02] px-2 py-0.5 rounded-lg border border-white/5">
                    <button class="p-1 hover:bg-white/10 rounded-lg transition-colors outline-none"><svg class="w-3.5 h-3.5 text-slate-500 hover:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg></button>
                    <span class="text-[9px] font-mono text-slate-400 font-bold uppercase tracking-widest cursor-default">Variant 1/3 (GPT-4)</span>
                    <button class="p-1 hover:bg-white/10 rounded-lg transition-colors outline-none"><svg class="w-3.5 h-3.5 text-slate-500 hover:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg></button>
                </div>
            </div>
        `;
    } else if (type === 'bot') {
        div.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        div.innerHTML = `
            <div class="w-9 h-9 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold text-xs shrink-0 shadow-[0_0_15px_rgba(16,185,129,0.15)]">P</div>
            <div class="flex flex-col gap-2 relative group max-w-xl">
                <div class="p-5 bg-white/[0.03] border border-white/20 rounded-3xl rounded-tl-sm text-sm leading-relaxed text-slate-200 shadow-xl">${text}</div>
                <div class="flex gap-4 px-3 opacity-0 group-hover:opacity-100 transition-opacity text-[9px] font-mono text-slate-500 tracking-wider items-center bg-white/[0.02] py-1 rounded-lg border border-white/5 w-fit">
                    <span title="Time To First Token">TTFT: ${(Math.random()*80 + 20).toFixed(0)}ms</span>
                    <span class="w-1 h-1 rounded-full bg-white/10"></span>
                    <span>Tok: ${(Math.random()*400 + 50).toFixed(0)}</span>
                    <span class="w-1 h-1 rounded-full bg-white/10"></span>
                    <span>$${(Math.random()*0.003).toFixed(4)}</span>
                    <span class="w-1 h-1 rounded-full bg-emerald-500/50"></span>
                    <span class="text-sky-400 font-bold">via Groq</span>
                </div>
            </div>
        `;
    } else if (type === 'guardrail') {
        div.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        div.innerHTML = `
            <div class="w-9 h-9 rounded-2xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-500 font-black text-[10px] shrink-0 shadow-[0_0_15px_rgba(225,29,72,0.3)]">
                <svg class="w-4 h-4 text-rose-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
            </div>
            <div class="flex flex-col gap-2 relative max-w-xl group">
                <div class="relative overflow-hidden p-5 bg-black/40 border border-rose-500/30 rounded-3xl rounded-tl-sm text-sm leading-relaxed text-slate-500">
                    <div class="animate-[redact_1.5s_ease-in-out_forwards] absolute inset-y-0 left-0 bg-rose-500/20 mix-blend-color-burn border-r-2 border-rose-500 z-10 backdrop-blur-[1px]"></div>
                    <span class="relative z-0 line-through decoration-rose-500/50 decoration-2">${text}</span>
                    <div class="absolute inset-0 flex items-center justify-center bg-black/90 backdrop-blur-sm opacity-0 animate-[fade-in_0.5s_1.2s_forwards] z-20">
                        <span class="text-[10px] font-black tracking-widest text-rose-500 uppercase px-3 py-1.5 border border-rose-500/50 bg-rose-500/10 rounded-lg shadow-[0_0_20px_rgba(225,29,72,0.4)]">REDACTED BY INJECTION GUARD</span>
                    </div>
                </div>
            </div>
        `;
    } else if (type === 'error') {
        div.className = 'flex gap-5 items-start animate-in fade-in slide-in-from-left-2';
        div.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400 font-bold text-xs shrink-0 shadow-[0_0_10px_rgba(225,29,72,0.2)]">!</div>
            <div class="p-5 bg-rose-500/5 border border-rose-500/20 rounded-3xl rounded-tl-sm text-sm leading-relaxed max-w-xl text-rose-200/80 flex flex-col gap-4 shadow-lg">
                <span>${text}</span>
                <button class="self-start px-4 py-1.5 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-colors flex items-center gap-2 ring-1 ring-rose-500/30" onclick="document.getElementById('chat-input').value='${text.replace(/'/g, "\\'")}'; document.getElementById('chat-input').focus()">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                    Retry Connection
                </button>
            </div>`;
    } else {
        div.className = 'flex gap-5 items-start';
        div.innerHTML = `<div class="w-9 h-9 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 font-bold text-xs shrink-0">${text}</div>`;
    }
    
    scroller.appendChild(div);
    scroller.scrollTop = scroller.scrollHeight;
    return div;
}
