import { describe, expect, it } from 'vitest';
import { createSkeleton } from './Skeleton';

describe('Skeleton', () => {
    it('defaults to a single line shape with role=status', () => {
        const el = createSkeleton();
        expect(el.tagName).toBe('SPAN');
        expect(el.className).toContain('rounded-md');
        expect(el.getAttribute('role')).toBe('status');
        expect(el.getAttribute('aria-label')).toBe('Loading');
    });

    it('respects width and height overrides', () => {
        const el = createSkeleton({ width: '120px', height: '14px' });
        expect((el as HTMLElement).style.width).toBe('120px');
        expect((el as HTMLElement).style.height).toBe('14px');
    });

    it('switches to block and circle shapes', () => {
        const block = createSkeleton({ shape: 'block' });
        const circle = createSkeleton({ shape: 'circle' });
        expect(block.className).toContain('rounded-lg');
        expect(circle.className).toContain('rounded-full');
        expect(circle.className).toContain('aspect-square');
    });

    it('repeat>1 wraps children in a stacked container with aria-label on the wrap', () => {
        const wrap = createSkeleton({ repeat: 4, gap: 'gap-3' });
        expect(wrap.tagName).toBe('DIV');
        expect(wrap.className).toContain('gap-3');
        expect(wrap.getAttribute('role')).toBe('status');
        expect(wrap.children).toHaveLength(4);
        for (const child of Array.from(wrap.children)) {
            expect(child.getAttribute('aria-hidden')).toBe('true');
        }
    });

    it('explicit empty ariaLabel hides from screen readers', () => {
        const el = createSkeleton({ ariaLabel: '' });
        expect(el.getAttribute('role')).toBeNull();
        expect(el.getAttribute('aria-hidden')).toBe('true');
    });
});
