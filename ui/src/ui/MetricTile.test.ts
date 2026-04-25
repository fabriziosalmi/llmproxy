import { describe, expect, it, vi } from 'vitest';
import { createMetricTile } from './MetricTile';

describe('MetricTile', () => {
    it('renders label and formatted value', () => {
        const t = createMetricTile({ label: 'Requests', value: '1,234' });
        expect(t.querySelector('p:first-child')?.textContent).toBe('Requests');
        const valueEl = t.querySelectorAll('p')[1];
        expect(valueEl?.textContent).toBe('1,234');
    });

    it('shows a provenance ℹ button only when provenance is provided', () => {
        const t1 = createMetricTile({ label: 'X', value: '1' });
        const t2 = createMetricTile({ label: 'X', value: '1', provenance: 'Sum of /metrics counter X' });
        expect(t1.querySelector('button')).toBeNull();
        const info = t2.querySelector('button');
        expect(info).not.toBeNull();
        expect(info?.title).toContain('counter');
        expect(info?.getAttribute('aria-label')).toBe('About X');
    });

    it('clicking the provenance ℹ does not trigger the tile click', () => {
        const onClick = vi.fn();
        const t = createMetricTile({ label: 'X', value: '1', provenance: 'src', onClick });
        const info = t.querySelector('button') as HTMLButtonElement;
        info.click();
        expect(onClick).not.toHaveBeenCalled();
        // Clicking the tile body still fires
        t.click();
        expect(onClick).toHaveBeenCalledTimes(1);
    });

    it('renders a skeleton when loading', () => {
        const t = createMetricTile({ label: 'X', value: '', loading: true });
        const value = t.querySelectorAll('p')[1];
        expect(value?.querySelector('span')?.className).toContain('animate-');
        expect(value?.getAttribute('aria-label')).toContain('loading');
    });

    it('renders an em-dash + tooltip on error', () => {
        const t = createMetricTile({ label: 'X', value: '', error: 'metrics endpoint 502' });
        const value = t.querySelectorAll('p')[1];
        expect(value?.textContent).toBe('—');
        expect(value?.title).toContain('502');
    });

    it('intent switches the value color and border', () => {
        const danger = createMetricTile({ label: 'Blocked', value: '5', intent: 'danger' });
        const success = createMetricTile({ label: 'Pass Rate', value: '99%', intent: 'success' });
        expect(danger.className).toContain('border-red-500/20');
        expect(success.className).toContain('border-emerald-500/20');
    });

    it('keyboard activation works for clickable tiles', () => {
        const onClick = vi.fn();
        const t = createMetricTile({ label: 'Open', value: '1', onClick });
        expect(t.tabIndex).toBe(0);
        t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        expect(onClick).toHaveBeenCalledTimes(1);
    });
});
