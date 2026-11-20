import {
	api,
	SignedOut,
	type Alarms,
	type Change,
	type Device,
	type Health,
	type Me,
	type Series
} from './api';

/** The app's live data: devices every 5 s, the last day's buckets and changes every 30 s. */
class Store {
	devices = $state<Device[] | null>(null);
	health = $state<Health | null>(null);
	series = $state<Series | null>(null);
	changes = $state<Change[]>([]);
	alarms = $state<Alarms | null>(null);
	error = $state<string | null>(null);
	/** Who is signed in; null until the hub has answered. */
	me = $state<Me | null>(null);
	updated = $state<number | null>(null);
	now = $state(Date.now());

	byId = $derived(new Map((this.devices ?? []).map((d) => [d.id, d])));

	#timers: ReturnType<typeof setInterval>[] = [];
	#users = 0;

	async refresh() {
		try {
			this.health = await api.health();
			if (this.health.locked) {
				this.clear();
				this.error = null;
				return;
			}
			this.me = await api.me();
			if (this.me.signin && !this.me.user) {
				this.clear();
			} else {
				this.devices = await api.devices();
				this.updated = Date.now();
			}
			this.error = null;
		} catch (e) {
			if (e instanceof SignedOut) {
				this.me = { signin: true, user: null, role: null };
				this.clear();
				this.error = null;
			} else {
				this.error = e instanceof Error ? e.message : String(e);
			}
		}
	}

	/** Forget what a signed-out browser must not keep showing. */
	clear() {
		this.devices = null;
		this.series = null;
		this.changes = [];
		this.alarms = null;
	}

	async signOut() {
		try {
			await api.logout();
		} finally {
			this.clear();
			await this.refresh();
		}
	}

	async refreshHistory() {
		if (this.me?.signin && !this.me.user) return;
		try {
			[this.series, this.changes, this.alarms] = await Promise.all([
				api.series(24, 48),
				api.changes(24),
				api.alarms()
			]);
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
