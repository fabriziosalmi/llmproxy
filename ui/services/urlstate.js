/**
 * Small hash-query helper for shareable UI state.
 *
 * LLMProxy routes are hash-based (`#/models`). Query params live after the
 * view (`#/models?q=gpt&tr=24h`) so static hosting and browser refresh keep
 * working.
 */

export function hashParts() {
    const raw = window.location.hash || '#/threats';
    const [viewPart, query = ''] = raw.split('?');
    return {
        view: viewPart || '#/threats',
        params: new URLSearchParams(query),
    };
}

export function getHashParam(key) {
    return hashParts().params.get(key);
}

export function setHashParams(patch) {
    const { view, params } = hashParts();
    for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === undefined || value === '') params.delete(key);
        else params.set(key, String(value));
    }
    const qs = params.toString();
    history.replaceState(null, '', `${view}${qs ? `?${qs}` : ''}`);
}

export function hashTab() {
    return hashParts().view.replace('#/', '');
}
