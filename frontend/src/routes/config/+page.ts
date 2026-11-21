import { redirect } from '@sveltejs/kit';

// Config became Settings; old links and bookmarks still lead there.
export function load({ url }) {
	redirect(307, `/settings${url.search}`);
}
