import { redirect } from '@sveltejs/kit';

// Virtual machines are a tab of Containers & VMs now; old links and bookmarks still lead there.
export function load() {
	redirect(307, '/containers?tab=vms');
}
