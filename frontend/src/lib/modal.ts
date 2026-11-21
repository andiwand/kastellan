/** `use:modal` on a <dialog>: open it as a modal, and keep the page behind it where it is (no
 * scrolling) while it is open. */
export function modal(dialog: HTMLDialogElement) {
	dialog.showModal();
	const root = document.documentElement;
	const before = root.style.overflow;
	root.style.overflow = 'hidden';
	return {
		destroy() {
			root.style.overflow = before;
		}
	};
}
