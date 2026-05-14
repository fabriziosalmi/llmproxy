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

const conversationHistory = [];
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

// Tokenize fenced code blocks BEFORE escaping or any other regex pass so their
// internal newlines and angle brackets survive intact. The placeholder uses
// underscores + hex digits so it round-trips through escapeHtml unchanged and
// is statistically vanishingly unlikely to collide with user content.
const CODE_PLACEHOLDER_RE = /__LLMP_CB_([0-9a-f]+)__/g;

function renderMarkdown(text) {
    if (!text) return '';

    // 1) Pull out fenced code blocks. ``` may or may not have a language tag.
    //    Internal content is captured verbatim so newlines/indent stay byte-
    //    exact. Each block gets a placeholder we restore in step 8.
    const codeBlocks = [];
    let html = text.replace(/```([A-Za-z0-9_+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const i = codeBlocks.length.toString(16);
        codeBlocks.push({ lang: (lang || '').toLowerCase(), code });
        return `__LLMP_CB_${i}__`;
    });

    // 2) Escape everything else (XSS-safe; placeholders survive — only [_a-zA-Z0-9]).
    html = escapeHtml(html);

    // 3) Inline code — single-line only so it can't swallow line breaks.
    html = html.replace(
        /`([^`\n]+)`/g,
        '<code class="bg-white/10 px-1.5 py-0.5 rounded text-[12px] text-sky-400 font-mono">$1</code>'
    );

    // 4) Bold / italic.
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="text-white font-bold">$1</strong>');
    html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em class="text-slate-300 italic">$1</em>');

    // 5) Headers (single line, line start). prose-invert in chat.html owns the
    //    visual styling (font-family, size) so they don't pick up mono drift.
    html = html.replace(/^### (.+)$/gm, '<h3 class="font-bold text-white">$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2 class="font-bold text-white">$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1 class="font-bold text-white">$1</h1>');

    // 6) Lists.
    html = html.replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-slate-300">$1</li>');
    html = html.replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal text-slate-300">$1</li>');

    // 7) Paragraph breaks. Safe to do globally now — code blocks are still
    //    placeholders, so their internal newlines never reach this regex.
    html = html.replace(/\n/g, '<br>');

    // 8) Restore code blocks. Escape ONLY the content (lang is whitelisted),
    //    preserve internal whitespace via white-space:pre on .hljs (chat.html).
    //    .language-X is the hint highlight.js uses to pick the grammar; the
    //    copy button sits inside <pre> so the delegated handler can grab the
    //    sibling <code>'s textContent.
    html = html.replace(CODE_PLACEHOLDER_RE, (_, i) => {
        const block = codeBlocks[parseInt(i, 16)];
        if (!block) return '';
        // Trim surrounding blank lines only, never internal whitespace.
        const stripped = block.code.replace(/^\n+|\n+$/g, '');
        const escaped = escapeHtml(stripped);
        const langAttr = block.lang ? ` language-${block.lang}` : '';
        const langLabel = block.lang ? `<span class="code-lang">${block.lang}</span>` : '';
        return `<pre>${langLabel}<button type="button" class="copy-btn" aria-label="Copy code">Copy</button><code class="hljs${langAttr}">${escaped}</code></pre>`;
    });

    return html;
}

// Run highlight.js over any <pre><code> in the given root that hasn't been
// colorized yet. Called once per assistant message AFTER the stream ends so
// we don't re-tokenize partial code on every delta. Safe if hljs isn't loaded
// (the CDN is `defer`-loaded and may not have arrived for the first turn).
function highlightCodeBlocks(root) {
    const hl = window.hljs;
    if (!hl || !root) return;
    root.querySelectorAll('pre > code:not([data-hl])').forEach((el) => {
        try {
            hl.highlightElement(el);
        } catch {
            /* unknown language → leave plain */
        }
        el.setAttribute('data-hl', '1');
    });
}

// ── Init ──

async function loadModels() {
    try {
        const res = await fetch(`${BASE}/v1/models`, { headers: authHeaders() });
        if (!res.ok) return { ok: false, status: res.status };
        const data = await res.json();
        const models = (data.data || []).filter((m) => !m.id.includes('embed'));
        // Clear any pre-existing entries so re-bootstrapping after a key
        // change doesn't duplicate the list.
        modelSelect.innerHTML = '';
        models.forEach((m) => {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = `${m.id} (${m.owned_by})`;
            modelSelect.appendChild(opt);
        });
        return { ok: true, count: models.length };
    } catch (e) {
        return { ok: false, error: e };
    }
}

async function bootstrap() {
    const result = await loadModels();
    if (result.ok) {
        setStatus('live');
        return true;
    }
    // 401/403 means we need a key; network failures go to Offline.
    if (result.status === 401 || result.status === 403 || !getToken()) {
        const { prompt } = await import('./src/ui');
        const key = await prompt({
            title: 'LLMProxy API key required',
            message: 'Paste the Bearer key the proxy gave you at install (see $LLM_PROXY_API_KEYS in .env).',
            label: 'API key',
            inputType: 'password',
            placeholder: 'sk-proxy-…',
            confirmLabel: 'Connect',
            validate: (v) => (v.trim() ? null : 'Key is required'),
        });
        if (!key) {
            setStatus('offline');
            return false;
        }
        localStorage.setItem('proxy_key', key.trim());
        // Re-run: load models with the new key, update status. Fixes the
        // dead-end state where saving a valid key still left the UI marked
        // Offline until a full page reload.
        const retry = await loadModels();
        if (retry.ok) {
            setStatus('live');
            return true;
        }
        setStatus('offline');
        return false;
    }
    setStatus('offline');
    return false;
}

async function init() {
    await bootstrap();
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
    bubble.className =
        role === 'user'
            ? 'max-w-[80%] bg-rose-500/10 border border-rose-500/20 rounded-2xl rounded-br-md px-4 py-3'
            : 'max-w-[85%] bg-white/[0.03] border border-white/[0.06] rounded-2xl rounded-bl-md px-4 py-3';

    // Label: provider (model) for assistant, "You" for user
    const label = document.createElement('div');
    label.className =
        'text-[9px] font-bold uppercase tracking-widest mb-1.5 ' +
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
    body.className =
        role === 'user'
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
        stats.className =
            'stats-bar mt-2 pt-2 border-t border-white/[0.04] flex items-center gap-3 text-[9px] font-mono text-slate-600 hidden';
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

        // Stream is done — colorize any code blocks in this message exactly
        // once. Doing it earlier would re-tokenize partial code on every
        // delta and burn CPU for no visual gain.
        highlightCodeBlocks(bodyEl);

        // Calculate metrics
        const totalTime = ((performance.now() - startTime) / 1000).toFixed(1);
        const completionTokens = usage?.completion_tokens || tokenCount;
        const promptTokens = usage?.prompt_tokens || '?';
        const tps =
            completionTokens > 0 && parseFloat(totalTime) > 0
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
        tokenInfo.textContent = `${promptTokens}p + ${completionTokens}c tokens` + (tps ? ` | ${tps} tok/s` : '');
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

// Delegated copy handler — clicks on any `.copy-btn` grab the sibling
// <code>'s textContent. Single listener on the message stream avoids
// having to rewire after every progressive innerHTML re-render.
messagesEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const code = btn.parentElement?.querySelector('code');
    if (!code) return;
    try {
        await navigator.clipboard.writeText(code.textContent || '');
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        btn.classList.add('copied');
        setTimeout(() => {
            btn.textContent = prev;
            btn.classList.remove('copied');
        }, 1200);
    } catch {
        btn.textContent = 'Failed';
        setTimeout(() => {
            btn.textContent = 'Copy';
        }, 1200);
    }
});

init();
