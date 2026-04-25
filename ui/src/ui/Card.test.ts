import { describe, expect, it, vi } from 'vitest';
import { createCard, createCardHeader } from './Card';

describe('Card', () => {
    it('renders an article element with backdrop-blur surface', () => {
        const card = createCard();
        expect(card.tagName).toBe('ARTICLE');
        expect(card.className).toContain('backdrop-blur-xl');
    });

    it('mounts header, body, and footer in order', () => {
        const header = document.createElement('div');
        header.dataset.slot = 'header';
        const body = document.createElement('p');
        body.textContent = 'body';
        const footer = document.createElement('div');
        footer.dataset.slot = 'footer';

        const card = createCard({ header, body, footer });
        const slots = Array.from(card.children);
        expect(slots[0]?.getAttribute('data-slot')).toBe('header');
        expect(slots[2]?.getAttribute('data-slot')).toBe('footer');
        expect(card.textContent).toContain('body');
    });

    it('accepts an array of body nodes', () => {
        const a = document.createElement('span');
        a.textContent = 'a';
        const b = document.createElement('span');
        b.textContent = 'b';
        const card = createCard({ body: [a, b] });
        expect(card.textContent).toBe('ab');
    });

    it('interactive cards are keyboard-activatable', () => {
        const onClick = vi.fn();
        const card = createCard({ interactive: true, onClick });
        expect(card.getAttribute('role')).toBe('button');
        expect(card.tabIndex).toBe(0);

        card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        expect(onClick).toHaveBeenCalledTimes(2);
    });

    it('non-interactive cards do not respond to keyboard', () => {
        const onClick = vi.fn();
        const card = createCard({ onClick });
        card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        expect(onClick).not.toHaveBeenCalled();
    });
});

describe('createCardHeader', () => {
    it('renders title only when no subtitle is given', () => {
        const h = createCardHeader('Title');
        expect(h.querySelector('h3')?.textContent).toBe('Title');
        expect(h.querySelector('p')).toBeNull();
    });

    it('renders title + subtitle', () => {
        const h = createCardHeader('Title', 'Subtitle');
        expect(h.querySelector('h3')?.textContent).toBe('Title');
        expect(h.querySelector('p')?.textContent).toBe('Subtitle');
    });
});
