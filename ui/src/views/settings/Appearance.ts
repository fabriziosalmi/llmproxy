/**
 * Settings → Appearance.
 *
 * The override surface for the decoupled theme service. "Auto" follows the
 * client's OS preference (prefers-color-scheme) and is the default; "Dark" and
 * "Light" pin an explicit theme. Selecting a mode applies it instantly and
 * persists it — no backend involved.
 */
import { createButton } from '../../ui';
import {
    getThemePreference,
    setThemePreference,
    resolveTheme,
    onThemeChange,
    type ThemePreference,
} from '../../services/theme';

export interface AppearanceHandle {
    refresh: () => Promise<void>;
}

const MODES: Array<{ id: ThemePreference; label: string; desc: string }> = [
    { id: 'auto', label: 'Auto', desc: 'Follow the system appearance' },
    { id: 'dark', label: 'Dark', desc: 'Always dark' },
    { id: 'light', label: 'Light', desc: 'Always light' },
];

export function mountAppearance(host: HTMLElement): AppearanceHandle {
    const card = document.createElement('div');
    card.className = 'bg-white/[0.03] backdrop-blur-xl rounded-2xl border border-white/[0.06] p-6';
    card.setAttribute('data-testid', 'settings-appearance');

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between mb-1';
    const title = document.createElement('h2');
    title.className = 'text-xs font-bold text-white';
    title.textContent = 'Appearance';
    head.appendChild(title);
    const note = document.createElement('span');
    note.className = 'text-[10px] text-slate-500 font-mono';
    head.appendChild(note);
    card.appendChild(head);

    const desc = document.createElement('p');
    desc.className = 'text-[10px] text-slate-500 mb-3';
    card.appendChild(desc);

    const row = document.createElement('div');
    row.className = 'flex flex-wrap items-center gap-2';
    card.appendChild(row);

    host.replaceChildren(card);

    function render(): void {
        const pref = getThemePreference();
        const resolved = resolveTheme(pref);
        note.textContent = pref === 'auto' ? `auto · currently ${resolved}` : pref;
        desc.textContent = MODES.find((m) => m.id === pref)?.desc ?? 'Choose how the dashboard looks.';
        row.replaceChildren(
            ...MODES.map((m) =>
                createButton({
                    label: m.label,
                    size: 'sm',
                    variant: m.id === pref ? 'primary' : 'ghost',
                    testId: `appearance-${m.id}`,
                    onClick: () => {
                        setThemePreference(m.id);
                        render();
                    },
                })
            )
        );
    }

    // Keep the selector honest if the OS theme flips while 'auto' is active,
    // or if another surface (header toggle) changes the preference.
    onThemeChange(() => render());
    render();

    return { refresh: async () => render() };
}
