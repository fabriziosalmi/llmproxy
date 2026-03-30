/**
 * LLMProxy — Chat Interface
 * Safe markdown rendering (no innerHTML on user content), TPS metrics, provider labels.
 */

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

// ── Auth ──

function getToken() {
    return localStorage.getItem('proxy_key') || '';
}

function authHeaders() {
    const t = getToken();
    const h = { 'Content-Type': 'application/json' };
    if (t) h['Authorization'] = `Bearer ${t}`;
    return h;
}

// ── Safe Markdown Renderer ──
// Converts markdown to HTML without using innerHTML on untrusted content.
// Only supports: **bold**, *italic*, `code`, ```code blocks```, headers, lists, links.
// All text content is escaped via textContent before insertion.

function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function renderMarkdown(text) {
    if (!text) return '';

    // Escape HTML entities first (prevents XSS)
    let html = escapeHtml(text);

    // Code blocks (``` ... ```) — must be before inline code
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
        `<pre class="bg-white/5 rounded-lg p-3 my-2 overflow-x-auto text-[12px]"><code class="text-emerald-400">${code.trim()}</code></pre>`
    );

    // Inline code (`...`)
    html = html.replace(/`([^`]+)`/g, '<code class="bg-white/10 px-1.5 py-0.5 rounded text-[12px] text-sky-400">$1</code>');

    // Bold (**...**)
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="text-white font-bold">$1</strong>');

    // Italic (*...*)
    html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em class="text-slate-300 italic">$1</em>');

    // Headers (### ... at line start)
    html = html.replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold text-white mt-3 mb-1">$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2 class="text-base font-bold text-white mt-3 mb-1">$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1 class="text-lg font-bold text-white mt-3 mb-1">$1</h1>');

    // Unordered lists (- item)
    html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-slate-300">$1</li>');

    // Ordered lists (1. item)
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-slate-300">$1</li>');

    // Line breaks
    html = html.replace(/\n/g, '<br>');

    return html;
}

// ── Init ──

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

// ── Message Rendering ──

function addMessage(role, content, meta = {}) {
    const welcome = messagesEl.querySelector('.text-center');
    if (welcome) welcome.remove();

    const wrapper = document.createElement('div');
    wrapper.className = `max-w-3xl mx-auto flex ${role === 'user' ? 'justify-end' : 'justify-start'} mb-4`;

    const bubble = document.createElement('div');
    bubble.className = role === 'user'
        ? 'max-w-[80%] bg-rose-500/10 border border-rose-500/20 rounded-2xl rounded-br-md px-4 py-3'
        : 'max-w-[85%] bg-white/[0.03] border border-white/[0.06] rounded-2xl rounded-bl-md px-4 py-3';

    // Label: provider (model) for assistant, "You" for user
    const label = document.createElement('div');
    label.className = 'text-[9px] font-bold uppercase tracking-widest mb-1.5 ' +
        (role === 'user' ? 'text-rose-400' : 'text-slate-500');
    if (role === 'user') {
        label.textContent = 'You';
    } else {
        const provider = meta.provider || '';
        const model = meta.model || 'Assistant';
        label.textContent = provider ? `${provider} (${model})` : model;
    }

    // Body: plain text for user, markdown for assistant
    const body = document.createElement('div');
    body.className = role === 'user'
        ? 'text-sm leading-relaxed text-slate-200 whitespace-pre-wrap'
        : 'text-sm leading-relaxed text-slate-200 prose-invert';
    if (role === 'user') {
        body.textContent = content; // Safe: no HTML interpretation
    } else {
        body.innerHTML = content ? renderMarkdown(content) : '';
    }

    bubble.appendChild(label);
    bubble.appendChild(body);

    // Stats bar (assistant only, populated after stream completes)
    if (role !== 'user') {
        const stats = document.createElement('div');
        stats.className = 'stats-bar mt-2 pt-2 border-t border-white/[0.04] flex items-center gap-3 text-[9px] font-mono text-slate-600 hidden';
        bubble.appendChild(stats);
    }

    wrapper.appendChild(bubble);
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    return body;
}

function updateMessageStats(bodyEl, stats) {
    const bubble = bodyEl.parentElement;
    const statsBar = bubble.querySelector('.stats-bar');
    if (!statsBar) return;

    const parts = [];
    if (stats.ttft) parts.push(`TTFT ${stats.ttft}ms`);
    if (stats.tps) parts.push(`${stats.tps} tok/s`);
    if (stats.promptTokens) parts.push(`${stats.promptTokens}p`);
    if (stats.completionTokens) parts.push(`${stats.completionTokens}c`);
    if (stats.totalTime) parts.push(`${stats.totalTime}s`);
    if (stats.cost) parts.push(`$${stats.cost}`);

    statsBar.textContent = parts.join(' | ');
    statsBar.classList.remove('hidden');
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

// ── Send Message ──

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
    let ttftMs = null;

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

        // Read provider from proxy response headers
        const provider = res.headers.get('X-LLMProxy-Provider') || '';

        if (!res.ok) {
            const err = await res.text();
            removeTypingIndicator();
            addMessage('assistant', `Error ${res.status}: ${err}`, { model: 'error' });
            return;
        }

        removeTypingIndicator();
        const bodyEl = addMessage('assistant', '', { model, provider });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let fullContent = '';
        let rawChunks = '';
        let usage = null;
        let responseModel = model;
        let tokenCount = 0;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // Track TTFT (time to first chunk)
            if (ttftMs === null) {
                ttftMs = Math.round(performance.now() - startTime);
            }

            const chunk = decoder.decode(value, { stream: true });
            rawChunks += chunk;
            for (const line of chunk.split('\n')) {
                if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    const delta = data.choices?.[0]?.delta?.content || '';
                    if (delta) {
                        fullContent += delta;
                        tokenCount++;
                        // Render markdown progressively
                        bodyEl.innerHTML = renderMarkdown(fullContent);
                        messagesEl.scrollTop = messagesEl.scrollHeight;
                    }
                    if (data.usage) usage = data.usage;
                    if (data.model) responseModel = data.model;
                } catch {}
            }
        }

        // Fallback for non-streaming responses
        if (!fullContent && rawChunks) {
            try {
                const fullJson = JSON.parse(rawChunks);
                if (fullJson.error) {
                    fullContent = `Provider error: ${fullJson.error.message || JSON.stringify(fullJson.error)}`;
                } else {
                    fullContent = fullJson.choices?.[0]?.message?.content || '';
                }
                if (fullContent) bodyEl.innerHTML = renderMarkdown(fullContent);
                if (fullJson.usage) usage = fullJson.usage;
                if (fullJson.model) responseModel = fullJson.model;
            } catch {}
        }

        conversationHistory.push({ role: 'assistant', content: fullContent || '(empty response)' });

        // Calculate metrics
        const totalTime = ((performance.now() - startTime) / 1000).toFixed(1);
        const completionTokens = usage?.completion_tokens || tokenCount;
        const promptTokens = usage?.prompt_tokens || '?';
        const tps = completionTokens > 0 && parseFloat(totalTime) > 0
            ? (completionTokens / parseFloat(totalTime)).toFixed(1)
            : null;

        // Update label with actual provider (model)
        const label = bodyEl.parentElement.querySelector('div:first-child');
        if (label && responseModel) {
            label.textContent = provider ? `${provider} (${responseModel})` : responseModel;
        }

        // Update stats bar on the message
        updateMessageStats(bodyEl, {
            ttft: ttftMs,
            tps,
            promptTokens,
            completionTokens,
            totalTime,
        });

        // Update footer info
        latencyInfo.textContent = `${totalTime}s`;
        tokenInfo.textContent = `${promptTokens}p + ${completionTokens}c tokens` +
            (tps ? ` | ${tps} tok/s` : '');

    } catch (e) {
        removeTypingIndicator();
        addMessage('assistant', `Network error: ${e.message}`, { model: 'error' });
    } finally {
        isStreaming = false;
        sendBtn.disabled = false;
        inputEl.focus();
    }
}

// ── Input Handling ──

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
