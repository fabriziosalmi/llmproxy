const BASE = window.location.origin;
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const modelSelect = document.getElementById('model-select');
const statusEl = document.getElementById('status-indicator');
const tokenInfo = document.getElementById('token-info');
const latencyInfo = document.getElementById('latency-info');

let conversationHistory = [];
let isStreaming = false;

function getToken() {
    return localStorage.getItem('proxy_key') || '';
}

function authHeaders() {
    const t = getToken();
    const h = { 'Content-Type': 'application/json' };
    if (t) h['Authorization'] = `Bearer ${t}`;
    return h;
}

async function init() {
    try {
        const res = await fetch(`${BASE}/v1/models`, { headers: authHeaders() });
        if (res.ok) {
            const data = await res.json();
            const models = (data.data || []).filter(m => !m.id.includes('embed'));
            models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = `${m.id} (${m.owned_by})`;
                modelSelect.appendChild(opt);
            });
            setStatus('live');
        } else {
            setStatus('offline');
        }
    } catch {
        setStatus('offline');
    }

    if (!getToken()) {
        const key = prompt('Enter your LLMProxy API key:');
        if (key) localStorage.setItem('proxy_key', key);
    }
}

function setStatus(state) {
    const dot = statusEl.querySelector('div');
    const text = statusEl.querySelector('span');
    if (state === 'live') {
        dot.className = 'w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.4)]';
        text.textContent = 'Connected';
        text.className = 'text-[9px] font-mono text-emerald-400';
    } else {
        dot.className = 'w-1.5 h-1.5 rounded-full bg-rose-500';
        text.textContent = 'Offline';
        text.className = 'text-[9px] font-mono text-rose-500';
    }
}

function addMessage(role, content, meta = {}) {
    const welcome = messagesEl.querySelector('.text-center');
    if (welcome) welcome.remove();

    const wrapper = document.createElement('div');
    wrapper.className = `max-w-3xl mx-auto flex ${role === 'user' ? 'justify-end' : 'justify-start'}`;

    const bubble = document.createElement('div');
    bubble.className = role === 'user'
        ? 'max-w-[80%] bg-rose-500/10 border border-rose-500/20 rounded-2xl rounded-br-md px-4 py-3'
        : 'max-w-[80%] bg-white/[0.03] border border-white/[0.06] rounded-2xl rounded-bl-md px-4 py-3';

    const label = document.createElement('div');
    label.className = 'text-[9px] font-bold uppercase tracking-widest mb-1 ' +
        (role === 'user' ? 'text-rose-400' : 'text-slate-500');
    label.textContent = role === 'user' ? 'You' : (meta.model || 'Assistant');

    const body = document.createElement('div');
    body.className = 'text-sm leading-relaxed text-slate-200 font-mono whitespace-pre-wrap';
    body.textContent = content;

    bubble.appendChild(label);
    bubble.appendChild(body);
    wrapper.appendChild(bubble);
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    return body;
}

function addTypingIndicator() {
    const wrapper = document.createElement('div');
    wrapper.id = 'typing';
    wrapper.className = 'max-w-3xl mx-auto flex justify-start';
    wrapper.innerHTML = `
        <div class="bg-white/[0.03] border border-white/[0.06] rounded-2xl rounded-bl-md px-4 py-3">
            <div class="flex items-center gap-1">
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 typing-dot"></div>
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 typing-dot" style="animation-delay:0.2s"></div>
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 typing-dot" style="animation-delay:0.4s"></div>
            </div>
        </div>`;
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
}

function removeTypingIndicator() {
    document.getElementById('typing')?.remove();
}

async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || isStreaming) return;

    isStreaming = true;
    sendBtn.disabled = true;
    inputEl.value = '';
    inputEl.style.height = 'auto';

    addMessage('user', text);
    conversationHistory.push({ role: 'user', content: text });
    addTypingIndicator();

    const model = modelSelect.value;
    const startTime = performance.now();

    try {
        const res = await fetch(`${BASE}/v1/chat/completions`, {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                model,
                messages: conversationHistory,
                stream: true,
            }),
        });

        if (!res.ok) {
            const err = await res.text();
            removeTypingIndicator();
            addMessage('assistant', `Error ${res.status}: ${err}`, { model: 'error' });
            return;
        }

        removeTypingIndicator();
        const bodyEl = addMessage('assistant', '', { model });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let fullContent = '';
        let rawChunks = '';
        let usage = null;
        let responseModel = model;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            rawChunks += chunk;
            for (const line of chunk.split('\n')) {
                if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    const delta = data.choices?.[0]?.delta?.content || '';
                    if (delta) {
                        fullContent += delta;
                        bodyEl.textContent = fullContent;
                        messagesEl.scrollTop = messagesEl.scrollHeight;
                    }
                    if (data.usage) usage = data.usage;
                    if (data.model) responseModel = data.model;
                } catch {}
            }
        }

        // Fallback: if streaming produced no content, try parsing as
        // non-streaming JSON response (some providers return full JSON
        // even with stream:true, or the proxy may buffer the response).
        if (!fullContent && rawChunks) {
            try {
                const fullJson = JSON.parse(rawChunks);
                // Check for upstream error
                if (fullJson.error) {
                    fullContent = `Provider error: ${fullJson.error.message || JSON.stringify(fullJson.error)}`;
                } else {
                    fullContent = fullJson.choices?.[0]?.message?.content || '';
                }
                if (fullContent) bodyEl.textContent = fullContent;
                if (fullJson.usage) usage = fullJson.usage;
                if (fullJson.model) responseModel = fullJson.model;
            } catch {}
        }

        conversationHistory.push({ role: 'assistant', content: fullContent || '(empty response)' });

        const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
        latencyInfo.textContent = `${elapsed}s`;
        if (usage) {
            tokenInfo.textContent = `${usage.prompt_tokens || '?'}p + ${usage.completion_tokens || '?'}c tokens`;
        }
        const label = bodyEl.previousSibling;
        if (label) label.textContent = responseModel;

    } catch (e) {
        removeTypingIndicator();
        addMessage('assistant', `Network error: ${e.message}`, { model: 'error' });
    } finally {
        isStreaming = false;
        sendBtn.disabled = false;
        inputEl.focus();
    }
}

inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

sendBtn.addEventListener('click', sendMessage);

init();
