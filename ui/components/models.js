/**
 * Models View — Aggregated model registry from all configured providers.
 * Fetches from GET /v1/models and displays in a sortable table.
 */
import { api } from '../services/api.js';
import { store } from '../services/store.js';

const EMBEDDING_PREFIXES = ['text-embedding', 'embedding-', 'nomic-embed', 'mxbai-embed', 'all-minilm', 'bge-', 'snowflake-arctic', 'mistral-embed'];

function isEmbeddingModel(id) {
    return EMBEDDING_PREFIXES.some(p => id.toLowerCase().startsWith(p));
}

let _allChat = [];
let _allEmbed = [];

export function initModels() {
    refreshModels();
    store.poll(refreshModels, 30000, 'models');

    const searchInput = document.getElementById('models-search');
    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                const q = searchInput.value.toLowerCase().trim();
                const fc = q ? _allChat.filter(m => m.id.toLowerCase().includes(q) || m.owned_by.toLowerCase().includes(q)) : _allChat;
                const fe = q ? _allEmbed.filter(m => m.id.toLowerCase().includes(q) || m.owned_by.toLowerCase().includes(q)) : _allEmbed;
                renderModelsTable(fc, fe);
            }, 150);
        });
    }
}

export function renderModels() {
    // Called by store.subscribe — no-op, data refreshed via polling
}

async function refreshModels() {
    try {
        const data = await api.fetchModels();
        const models = data.data || [];

        const providers = new Set(models.map(m => m.owned_by));
        _allEmbed = models.filter(m => isEmbeddingModel(m.id));
        _allChat = models.filter(m => !isEmbeddingModel(m.id));

        setText('kpi-active-models', models.length);
        setText('kpi-providers', providers.size);
        setText('kpi-embedding-models', _allEmbed.length);

        renderModelsTable(_allChat, _allEmbed);
    } catch (e) {
        console.error('Failed to load models:', e);
    }
}

function renderModelsTable(chatModels, embeddingModels) {
    const container = document.getElementById('models-table');
    if (!container) return;

    const providerColors = {
        openai: 'text-emerald-400',
        anthropic: 'text-amber-400',
        google: 'text-sky-400',
        azure: 'text-blue-400',
        ollama: 'text-slate-400',
        groq: 'text-orange-400',
        together: 'text-purple-400',
        mistral: 'text-indigo-400',
        deepseek: 'text-cyan-400',
        xai: 'text-rose-400',
        perplexity: 'text-teal-400',
    };

    function modelRow(m) {
        const color = providerColors[m.owned_by] || 'text-slate-400';
        const badge = isEmbeddingModel(m.id)
            ? '<span class="ml-2 px-1.5 py-0.5 text-[10px] font-bold bg-violet-500/20 text-violet-400 rounded">EMB</span>'
            : '';
        return `
            <tr class="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                <td class="px-4 py-2.5">
                    <span class="text-[11px] font-bold text-white font-mono">${m.id}</span>${badge}
                </td>
                <td class="px-4 py-2.5">
                    <span class="text-[10px] font-semibold ${color} uppercase">${m.owned_by}</span>
                </td>
            </tr>`;
    }

    container.innerHTML = `
        <div class="overflow-hidden rounded-xl">
            <table class="w-full">
                <thead>
                    <tr class="border-b border-white/[0.08]">
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Model ID</th>
                        <th class="text-left text-[9px] font-bold text-slate-500 uppercase tracking-widest px-4 py-3">Provider</th>
                    </tr>
                </thead>
                <tbody>
                    ${chatModels.map(modelRow).join('')}
                    ${embeddingModels.length > 0 ? `
                        <tr><td colspan="2" class="px-4 pt-4 pb-2">
                            <p class="text-[9px] font-bold text-violet-400 uppercase tracking-widest">Embedding Models</p>
                        </td></tr>
                        ${embeddingModels.map(modelRow).join('')}
                    ` : ''}
                </tbody>
            </table>
        </div>
        <p class="text-[9px] text-slate-600 mt-3 px-1">${chatModels.length + embeddingModels.length} models across ${new Set([...chatModels, ...embeddingModels].map(m => m.owned_by)).size} providers</p>
    `;
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = value;
        el.classList.remove('skeleton');
    }
}
