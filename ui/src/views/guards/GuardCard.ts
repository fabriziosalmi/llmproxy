import { attachTooltip, createBadge, createCard, createToggle, cx } from '../../ui';
import type { GuardSpec } from './types';

const INFO_ICON =
    '<svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
    '<circle cx="8" cy="8" r="6.5"/><path d="M8 7.5v3.5"/><circle cx="8" cy="5.4" r="0.4" fill="currentColor"/></svg>';

export interface GuardCardProps {
    spec: GuardSpec;
    /** Live enabled state. */
    enabled: boolean;
    /** Optional override status text — used by `firewall` to surface "OFF · env" with the reason. */
    statusOverride?: string;
    /** Called when the user flips the toggle. Only used when spec.toggleable=true. */
    onToggle?: (next: boolean) => void;
}

const ICON_COLOR_BY_INTENT: Record<GuardSpec['intent'], string> = {
    neutral: 'text-slate-300',
    primary: 'text-rose-400',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    danger: 'text-red-400',
    info: 'text-sky-400',
};

const STATUS_COLOR_BY_INTENT: Record<GuardSpec['intent'], string> = {
    neutral: 'text-slate-500',
    primary: 'text-rose-400',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    danger: 'text-red-400',
    info: 'text-sky-400',
};

/**
 * Render one guard card. Layout: icon + name + (toggle | status badge),
 * description, footer status line. The provenance ℹ button opens a tooltip
 * explaining what triggers the guard and where it can be reconfigured.
 */
export function createGuardCard(props: GuardCardProps): HTMLElement {
    const { spec, enabled } = props;
    const iconColor = ICON_COLOR_BY_INTENT[spec.intent];

    const head = document.createElement('div');
    head.className = 'flex items-start justify-between mb-3';

    const titleRow = document.createElement('div');
    titleRow.className = 'flex items-center gap-2';
    const iconHost = document.createElement('div');
    iconHost.className = iconColor;
    iconHost.innerHTML = spec.iconSvg;
    titleRow.appendChild(iconHost);

    const name = document.createElement('h3');
    name.className = 'text-xs font-bold text-white';
    name.textContent = spec.name;
    titleRow.appendChild(name);

    // Provenance ℹ button — opens a tooltip with the full "why this exists".
    const info = document.createElement('button');
    info.type = 'button';
    info.className =
        'shrink-0 text-slate-600 hover:text-slate-300 transition-colors p-0.5 rounded ' +
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-500/40';
    info.setAttribute('aria-label', `About ${spec.name}`);
    info.innerHTML = INFO_ICON;
    titleRow.appendChild(info);
    attachTooltip(info, { content: spec.provenance });

    head.appendChild(titleRow);

    if (spec.toggleable) {
        const toggle = createToggle({
            label: spec.name,
            checked: enabled,
            onChange: (next) => props.onToggle?.(next),
            testId: `guard-toggle-${spec.key}`,
        });
        // We only want the switch, not the label/description block.
        const switchEl = toggle.root.querySelector<HTMLButtonElement>('[role="switch"]');
        if (switchEl) head.appendChild(switchEl);
    } else {
        const status = props.statusOverride ?? spec.staticStatus ?? '';
        head.appendChild(
            createBadge({
                label: status,
                intent: enabled ? spec.intent : 'neutral',
                size: 'sm',
                testId: `guard-status-${spec.key}`,
            })
        );
    }

    const desc = document.createElement('p');
    desc.className = 'text-[10px] text-slate-400 leading-relaxed';
    desc.textContent = spec.description;

    const footer = document.createElement('div');
    footer.className = 'mt-3 flex items-center gap-2';
    const stateLabel = document.createElement('span');
    stateLabel.className = cx('text-[9px] font-mono', enabled ? STATUS_COLOR_BY_INTENT[spec.intent] : 'text-slate-600');
    stateLabel.textContent = enabled ? 'ACTIVE' : 'DISABLED';
    footer.appendChild(stateLabel);

    return createCard({
        body: [head, desc, footer],
        className: enabled ? `border-${colorTokenForIntent(spec.intent)}/20` : undefined,
        testId: `guard-card-${spec.key}`,
    });
}

// Tailwind needs literal class strings to detect them at build time, so we
// can't dynamically compose color names. Map intent → ring color stem and
// rely on the tailwind.config content scanner to keep these classes alive.
const COLOR_TOKEN_BY_INTENT: Record<GuardSpec['intent'], string> = {
    neutral: 'white/[0.06]',
    primary: 'rose-500',
    success: 'emerald-500',
    warning: 'amber-500',
    danger: 'red-500',
    info: 'sky-500',
};

function colorTokenForIntent(intent: GuardSpec['intent']): string {
    return COLOR_TOKEN_BY_INTENT[intent];
}
