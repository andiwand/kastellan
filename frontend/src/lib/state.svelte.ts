import { api, type Change, type Device, type Health, type Series } from './api';

/** The app's live data: devices every 5 s, the last day's buckets and changes every 30 s. */
class Store {
	devices = $state<Device[] | null>(null);
	health = $state<Health | null>(null);
	series = $state<Series | null>(null);
	changes = $state<Change[]>([]);
	error = $state<string | null>(null);
	updated = $state<number | null>(null);
	now = $state(Date.now());

	byId = $derived(new Map((this.devices ?? []).map((d) => [d.id, d])));

	#timers: ReturnType<typeof setInterval>[] = [];
	#users = 0;

	async refresh() {
		try {
			[this.devices, this.health] = await Promise.all([api.devices(), api.health()]);
			this.error = null;
			this.updated = Date.now();
		} catch (e) {
			this.error = e instanceof Error ? e.message : String(e);
		}
	}

	async refreshHistory() {
		try {
			[this.series, this.changes] = await Promise.all([api.series(24, 48), api.changes(24)]);
		} catch {
			// refresh() reports a broken connection
		}
	}

	/** Start polling while at least one page uses the store; returns the stop function. */
	use() {
		if (this.#users++ === 0) {
			this.refresh();
			this.refreshHistory();
			this.#timers = [
				setInterval(() => this.refresh(), 5000),
				setInterval(() => this.refreshHistory(), 30000),
				setInterval(() => (this.now = Date.now()), 1000)
			];
		}
		return () => {
			if (--this.#users === 0) this.#timers.forEach(clearInterval);
		};
	}
}

export const store = new Store();
