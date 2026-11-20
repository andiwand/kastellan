import { api, type Settings } from '#lib/api.js';
import { store } from '#lib/state.svelte.js';

/** config.yaml's settings for the forms (Config, Groups, VPN pages): loaded once, each form
 * saves its part; saving refuses a file that changed since it was read (the version). */
class SettingsState {
	data = $state<Settings | null>(null);
	error = $state<string | null>(null);
	loading = false;

	async load() {
		this.loading = true;
		try {
			this.data = await api.settings();
			this.error = null;
		} catch (e) {
			this.error = (e as Error).message;
		} finally {
			this.loading = false;
		}
	}

	/** Load fresh when a page that edits them opens (the file may have changed elsewhere). */
	use() {
		if (!this.loading && store.editBlocked === null) this.load();
	}

	/** Save one part; then the settings and the app's view are read again. */
	async save(section: keyof Settings['sections'], value: object) {
		if (!this.data) throw new Error('not loaded');
		try {
			await api.putSettings(section, value, this.data.version);
		} catch (e) {
			// Changed elsewhere meanwhile: read it again, so the next try works on what is there.
			if ((e as Error).message.includes('changed since')) await this.load();
			throw e;
		}
		await this.load();
		await store.refresh();
	}
}

export const settings = new SettingsState();

/** A deep copy for a form to edit (the loaded settings stay as they were until saved). */
export const copy = <T>(v: T): T => structuredClone($state.snapshot(v)) as T;
