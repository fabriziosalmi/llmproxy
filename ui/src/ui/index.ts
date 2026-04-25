/**
 * Primitive components — composed by views to avoid copy-pasted styling.
 *
 * Each export is a factory function that returns an HTMLElement. Callers
 * mount via appendChild / replaceChildren — there is no virtual DOM. Keeping
 * the API DOM-first means tests run in happy-dom without a renderer and
 * existing innerHTML callsites can adopt primitives one at a time.
 */
export { cx } from './classnames';
export type { ClassValue } from './classnames';

export { createButton } from './Button';
export type { ButtonOptions, ButtonVariant, ButtonSize } from './Button';

export { createCard, createCardHeader } from './Card';
export type { CardOptions, CardElevation } from './Card';

export { createBadge } from './Badge';
export type { BadgeOptions, BadgeIntent, BadgeSize } from './Badge';

export { createEmptyState } from './EmptyState';
export type { EmptyStateOptions } from './EmptyState';

export { createErrorState } from './ErrorState';
export type { ErrorStateOptions } from './ErrorState';

export { createSkeleton } from './Skeleton';
export type { SkeletonOptions, SkeletonShape } from './Skeleton';

export { createMetricTile } from './MetricTile';
export type { MetricTileOptions, MetricIntent, MetricSize } from './MetricTile';

export { createModal, confirm, prompt } from './Modal';
export type { CreateModalOptions, ModalButton, ConfirmOptions, PromptOptions, ButtonRole } from './Modal';

export { createDrawer } from './Drawer';
export type { CreateDrawerOptions, DrawerHandle } from './Drawer';

export { attachTooltip } from './Tooltip';
export type { AttachTooltipOptions, TooltipPlacement } from './Tooltip';

export { createInput } from './Input';
export type { InputFieldOptions, InputFieldHandle, InputType } from './Input';

export { createToggle } from './Toggle';
export type { ToggleOptions, ToggleHandle } from './Toggle';
