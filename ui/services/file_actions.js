export async function copyText(text) {
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            /* fall through */
        }
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
        return document.execCommand('copy');
    } catch {
        return false;
    } finally {
        ta.remove();
    }
}

export function downloadText(filename, text, type = 'text/plain') {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

export function csvCell(value) {
    const s = String(value ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function rowsToCsv(headers, rows) {
    return [headers.map(csvCell).join(','), ...rows.map((r) => headers.map((h) => csvCell(r[h])).join(','))].join('\n');
}

export function stamp() {
    return new Date().toISOString().slice(0, 10);
}
