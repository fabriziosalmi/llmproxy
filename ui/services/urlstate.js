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

/** Path segments of the current view, e.g. `#/settings/traffic` → ['settings','traffic']. */
function viewSegments() {
    return hashParts()
        .view.replace(/^#\//, '')
        .split('/')
        .filter(Boolean);
}

/** The top-level tab (first path segment), e.g. `#/settings/traffic` → 'settings'. */
export function hashTab() {
    return viewSegments()[0] || '';
}

/** The sub-view (second path segment), e.g. `#/settings/traffic` → 'traffic'; '' if none. */
export function hashSub() {
    return viewSegments()[1] || '';
}

/**
 * Write `#/<tab>[/<sub>]` while preserving the current `?params`. Uses
 * replaceState so tab/sub-tab changes don't spam browser history. The single
 * source of truth for composing the view hash — both the top-level router
 * (content.js) and the Settings sub-tabs go through here.
 */
export function setHashView(tab, sub) {
    const { params } = hashParts();
    const path = sub ? `#/${tab}/${sub}` : `#/${tab}`;
    const qs = params.toString();
    history.replaceState(null, '', `${path}${qs ? `?${qs}` : ''}`);
}
