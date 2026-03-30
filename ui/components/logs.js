/**
 * Logs Component (Phase 11: Zenith xterm.js Edition)
 */
import { store } from '../services/store.js';

let term;
let fitAddon;
let resizeListenerAttached = false;

export function initLogs() {
    const container = document.getElementById('log-terminal');
    if (!container) return;

    // Remove any existing children
    container.innerHTML = '';

    // Initialize xterm.js
    term = new Terminal({
        theme: {
            background: 'transparent',
            foreground: '#34d399', // 13. Brighter Emerald (emerald-400)
            cursor: '#34d399',
            black: '#000000',
            red: '#f43f5e',
            green: '#34d399',
            yellow: '#fbbf24',
            blue: '#38bdf8',
            magenta: '#818cf8',
            cyan: '#22d3ee',
            white: '#e2e8f0',
        },
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: 11,
        lineHeight: 1.4,
        letterSpacing: 0.5,
        cursorBlink: true,
        scrollback: 10000,
        allowTransparency: true,
    });

    fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);

    // Load WebGL Addon for zero-latency rendering
    try {
        const webglAddon = new WebglAddon.WebglAddon();
        term.loadAddon(webglAddon);
        console.info("xterm.js: WebGL Accelerated Rendering Enabled");
    } catch (e) {
        console.warn("xterm.js: WebGL failed, falling back to Canvas", e);
    }

    term.open(container);
    fitAddon.fit();

    // Handle Window Resize (attach once only)
    if (!resizeListenerAttached) {
        window.addEventListener('resize', () => {
            if (fitAddon) fitAddon.fit();
        });
        resizeListenerAttached = true;
    }

    // Connect Log Stream
    const BASE_URL = window.location.origin;
    if (store.state.logSource) store.state.logSource.close();
    
    const logSource = new EventSource(`${BASE_URL}/api/v1/logs`);
    store.update({ logSource });

    term.writeln('\x1b[32m[SYSTEM]\x1b[0m Waiting for neural traffic link...'); // 13. No italic

    logSource.onmessage = (event) => {
        try {
            const entry = JSON.parse(event.data);
            appendLogToTerm(entry);
        } catch (e) {
            console.error('Failed to parse log entry:', e);
        }
    };

    const clearBtn = document.getElementById('clear-logs-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            if (term) {
                term.clear();
                term.writeln('\x1b[33m[SYSTEM] Terminal buffer cleared.\x1b[0m');
            }
        });
    }
}

function appendLogToTerm(log) {
    if (!term) return;

    // Check filters
    const filterInput = document.getElementById('log-filter');
    if (filterInput && filterInput.value.trim() !== '') {
        const pipes = filterInput.value.split('|').map(s => s.trim().toLowerCase()).filter(s => s);
        const text = (log.message || "").toLowerCase();
        const matches = pipes.every(p => text.includes(p.replace(/grep['" ]+/g, '').replace(/['"]/g, '')));
        if (!matches) return;
    }

    const timestamp = `\x1b[90m[${log.timestamp}]\x1b[0m`;
    const levelColor = {
        'INFO': '\x1b[34m',    // Blue
        'WARNING': '\x1b[33m', // Yellow
        'ERROR': '\x1b[31m',   // Red
        'CRITICAL': '\x1b[91;1m', // High Intensity Red
        'SYSTEM': '\x1b[35m',  // Magenta
        'PROXY': '\x1b[32m',   // Green
        'SECURITY': '\x1b[33;1m' // Bold Yellow
    }[log.level] || '\x1b[37m';

    const level = `${levelColor}${log.level}\x1b[0m`;
    let message = log.message;

    // 5.4 + 15.19: JSON native syntax highlighting (pretty-print + ANSI colors)
    if (message.includes('{') && message.includes('}')) {
        // Try to extract and pretty-print embedded JSON
        const jsonMatch = message.match(/(\{[\s\S]*\})/);
        if (jsonMatch) {
            try {
                const parsed = JSON.parse(jsonMatch[1]);
                const pretty = JSON.stringify(parsed, null, 2);
                const prefix = message.slice(0, message.indexOf(jsonMatch[1]));
                const suffix = message.slice(message.indexOf(jsonMatch[1]) + jsonMatch[1].length);
                // Render prefix on its own line, then pretty JSON with colors
                const isAtBottom = term.buffer.active.viewportY >= term.buffer.active.baseY;
                if (prefix.trim()) term.writeln(`${timestamp} ${level} ${prefix.trim()}`);
                pretty.split('\n').forEach(line => {
                    const colored = line.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (m) {
                        if (/^"/.test(m)) {
                            return (/:$/.test(m) ? '\x1b[36m' : '\x1b[32m') + m + '\x1b[0m';
                        } else if (/true|false/.test(m)) return '\x1b[33m' + m + '\x1b[0m';
                        else if (/null/.test(m)) return '\x1b[90m' + m + '\x1b[0m';
                        return '\x1b[31m' + m + '\x1b[0m';
                    });
                    // Braces/brackets in white
                    term.writeln(`  \x1b[90m│\x1b[0m ${colored}`);
                });
                if (suffix.trim()) term.writeln(`${timestamp} ${level} ${suffix.trim()}`);
                handleAutoScroll(isAtBottom);
                return;
            } catch (_) {
                // Not valid JSON, fall through to regex coloring
            }
        }
        // Fallback: inline regex highlighting
        message = message.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
            let cls = '\x1b[32m';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) cls = '\x1b[36m';
            } else if (/true|false/.test(match)) cls = '\x1b[33m';
            else if (/null/.test(match)) cls = '\x1b[90m';
            else cls = '\x1b[31m';
            return cls + match + '\x1b[0m';
        });
    }

    // 5.1: Live Diff — GitHub-style colored lines for prompt-blocked diff output
    if (message.includes('@@') || /^[\-+]{3}\s/.test(message.trim())) {
        const lines = message.split('\n');
        const isAtBottom = term.buffer.active.viewportY >= term.buffer.active.baseY;
        lines.forEach(line => {
            const trimmed = line.trimStart();
            if (trimmed.startsWith('@@')) {
                term.writeln(`${timestamp} \x1b[36;1m${line}\x1b[0m`);
            } else if (trimmed.startsWith('---')) {
                term.writeln(`${timestamp} \x1b[1;90m${line}\x1b[0m`);
            } else if (trimmed.startsWith('+++')) {
                term.writeln(`${timestamp} \x1b[1;90m${line}\x1b[0m`);
            } else if (trimmed.startsWith('-')) {
                term.writeln(`${timestamp} \x1b[41;37m ${line} \x1b[0m`);
            } else if (trimmed.startsWith('+')) {
                term.writeln(`${timestamp} \x1b[42;30m ${line} \x1b[0m`);
            } else {
                term.writeln(`${timestamp} ${level} ${line}`);
            }
        });
        handleAutoScroll(isAtBottom);
        return;
    }

    // 15.16 Intelligent Autoscroll (Freeze if user scrolled up)
    const isAtBottom = term.buffer.active.viewportY >= term.buffer.active.baseY;

    term.writeln(`${timestamp} ${level} ${message}`);
    
    handleAutoScroll(isAtBottom);
}

function handleAutoScroll(isAtBottom) {
    if (isAtBottom) {
        term.scrollToBottom();
        const badge = document.getElementById('log-paused-badge');
        if (badge) badge.classList.add('hidden', 'opacity-0');
    } else {
        const badge = document.getElementById('log-paused-badge');
        if (badge) badge.classList.remove('hidden', 'opacity-0');
        const count = document.getElementById('log-missed-count');
        if (count) count.innerText = parseInt(count.innerText || '0') + 1;
    }
}

export function clearLogs() {
    if (term) term.clear();
}
