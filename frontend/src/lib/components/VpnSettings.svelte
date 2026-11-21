<script lang="ts">
	import { api, type Settings } from '#lib/api.js';
	import { settings, copy } from '#lib/settings.svelte.js';
	import { store } from '#lib/state.svelte.js';
	import SettingsCard from './SettingsCard.svelte';
	import { untrack } from 'svelte';

	/** Where the VPN's clients are managed: WireGuard through wg-easy, OpenVPN through an
	 * easy-rsa PKI on a host (and the server that gets the revocation list). Saved into
	 * config.yaml's `vpn:`; wg-easy's password as a secret, never in the file. */
	let { onsaved }: { onsaved?: () => void } = $props();

	type Vpn = Settings['sections']['vpn']['vpn'];
	let wg = $state({ on: false, wg_easy: '', user: 'admin', password_secret: 'wg-easy' });
	let password = $state('');
	let ovpn = $state<{
		on: boolean;
		mode: 'restricted' | 'direct';
		pki: string;
		server: string;
		days: number;
	}>({
		on: false,
		mode: 'restricted',
		pki: '',
		server: '',
		days: 365
	});
	let rest: Record<string, unknown> = {}; // OpenVPN's other options, as they are in the file
	let base = $state('');
	const json = () => JSON.stringify({ wg: $state.snapshot(wg), ovpn: $state.snapshot(ovpn) });

	function fill(v: Vpn) {
		wg = {
			on: !!v.wireguard,
			wg_easy: v.wireguard?.wg_easy ?? '',
			user: v.wireguard?.user ?? 'admin',
			password_secret: v.wireguard?.password_secret ?? 'wg-easy'
		};
		const o = v.openvpn;
		const { mode, pki, server, days, ...others } = o ?? ({} as NonNullable<Vpn['openvpn']>);
		rest = o ? copy(others) : {};
		ovpn = {
			on: !!o,
			mode: mode ?? 'restricted',
			pki: pki ?? '',
			server: server ?? '',
			days: days ?? 365
		};
		password = '';
		base = json();
	}

	// Fresh once editing is allowed: on a reload that is known only after the hub answered.
	let asked = false;
	$effect(() => {
		if (!asked && store.editBlocked === null) {
			asked = true;
			untrack(() => settings.load());
		}
	});
	let shown = '';
	$effect(() => {
		const d = settings.data;
		if (d && d.version !== shown) {
			shown = d.version;
			fill(d.sections.vpn.vpn);
		}
	});

	const sshDevices = $derived(
		(store.devices ?? []).filter((d) => d.ssh).sort((a, b) => a.name.localeCompare(b.name))
	);
	const secretSet = $derived(!!settings.data?.secrets[wg.password_secret]);
	const ready = $derived(
		(!wg.on || (/^https?:\/\/\S+$/.test(wg.wg_easy) && (secretSet || password !== ''))) &&
			(!ovpn.on || ovpn.pki !== '')
	);

	async function save() {
		// The settings first: if they are refused, the working password isn't touched.
		const vpn = {
			wireguard: wg.on
				? { wg_easy: wg.wg_easy, user: wg.user, password_secret: wg.password_secret }
				: null,
			openvpn: ovpn.on
				? { ...rest, mode: ovpn.mode, pki: ovpn.pki, server: ovpn.server || null, days: ovpn.days }
				: null
		};
		await settings.save('vpn', { vpn });
		if (wg.on && password) {
			await api.putSecret(wg.password_secret, password);
			password = '';
			await settings.load();
		}
		onsaved?.();
	}
</script>

{#if settings.error}
	<p class="text-sm" style:color="var(--critical-text)">{settings.error}</p>
{:else if settings.data}
	<SettingsCard
		title="VPN settings"
		description="Where Kastellan manages the VPN's clients. Kastellan adds and removes clients there; it doesn't run the VPN itself."
		dirty={json() !== base || password !== ''}
		canSave={ready}
		reset={() => fill(settings.data!.sections.vpn.vpn)}
		{save}
	>
		<fieldset class="flex flex-col gap-3">
			<label class="choice text-sm font-medium">
				<input type="checkbox" bind:checked={wg.on} /> WireGuard, through wg-easy
			</label>
			{#if wg.on}
				<div class="ml-6.5 grid gap-3 sm:grid-cols-2">
					<label class="field sm:col-span-2">
						wg-easy's address
						<input
							class="input"
							type="url"
							placeholder="http://sv1:51821"
							bind:value={wg.wg_easy}
						/>
					</label>
					<label class="field">
						User <input class="input" bind:value={wg.user} />
					</label>
					<label class="field">
						Password
						<input
							class="input"
							type="password"
							autocomplete="off"
							placeholder={secretSet ? 'set; empty keeps it' : 'needed'}
							bind:value={password}
						/>
					</label>
				</div>
			{/if}
		</fieldset>
		<fieldset class="flex flex-col gap-3">
			<label class="choice text-sm font-medium">
				<input type="checkbox" bind:checked={ovpn.on} /> OpenVPN, through an easy-rsa PKI
			</label>
			{#if ovpn.on}
				<div class="ml-6.5 flex flex-col gap-3">
					<div class="grid gap-3 sm:grid-cols-2">
						<label class="field">
							The PKI's host
							<select class="input" bind:value={ovpn.pki}>
								<option value="" disabled>choose (devices with an SSH login)</option>
								{#each sshDevices as d (d.id)}<option value={d.id}>{d.name}</option>{/each}
							</select>
						</label>
						<label class="field">
							The OpenVPN server (gets the revocation list)
							<select class="input" bind:value={ovpn.server}>
								<option value="">none</option>
								{#each sshDevices as d (d.id)}<option value={d.id}>{d.name}</option>{/each}
							</select>
						</label>
						<label class="field">
							New certificates last (days)
							<input class="input" type="number" min="1" max="3650" bind:value={ovpn.days} />
						</label>
					</div>
					<div class="flex flex-col gap-1.5 text-sm">
						<label class="choice">
							<input type="radio" bind:group={ovpn.mode} value="restricted" />
							<span>
								Restricted <span class="text-xs text-muted">
									— a key that may only run the PKI and CRL scripts (set up on the hosts)
								</span>
							</span>
						</label>
						<label class="choice">
							<input type="radio" bind:group={ovpn.mode} value="direct" />
							<span>
								Direct <span class="text-xs text-muted">
									— through the hosts' normal SSH logins, nothing installed
								</span>
							</span>
						</label>
					</div>
					{#if sshDevices.length === 0}
						<p class="hint" style:color="var(--warning-text)">
							No device has an SSH login yet: give the PKI's host one on its page first.
						</p>
					{/if}
				</div>
			{/if}
		</fieldset>
	</SettingsCard>
{/if}
