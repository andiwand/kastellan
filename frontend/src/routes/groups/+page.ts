import { redirect } from '@sveltejs/kit';

/** Groups are a tab of Devices now: old links lead there. */
export function load({ url }) {
	const tab = url.searchParams.get('tab');
	redirect(307, `/devices?section=groups${tab ? `&tab=${encodeURIComponent(tab)}` : ''}`);
}
