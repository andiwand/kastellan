import { redirect } from '@sveltejs/kit';

// Access is a tab of Settings now; old links and bookmarks still lead there.
export function load() {
	redirect(307, '/settings?tab=access');
}
