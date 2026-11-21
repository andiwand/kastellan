<script lang="ts">
	import SetupCard from './SetupCard.svelte';
	import CodeBlock from './CodeBlock.svelte';
	import { more } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { dayClock } from '#lib/format.js';

	/** A device that sends its logs itself, over TLS (syslog_tls.py): a client certificate made
	 * for it (its key shown once), and the rsyslog lines to use it. */
	let { device, canSetUp }: { device: string; canSetUp: boolean } = $props();

	type State = Awaited<ReturnType<typeof more.syslogState>>;
	let st = $state<State | null>(null);
	let bundle = $state<{ ca: string; cert: string; key: string; port: number } | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function load() {
		try {
			st = await more.syslogState(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}
	$effect(() => {
		void device;
		bundle = null;
		st = null;
		error = null;
		load();
	});

	async function issue() {
		busy = true;
		error = null;
		try {
			bundle = await more.syslogCertificate(device);
			await load();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	async function revoke() {
		error = null;
		try {
			await more.syslogRevoke(device);
			bundle = null;
			await load();
		} catch (e) {
			error = (e as Error).message;
		}
	}

	const hub = typeof location === 'undefined' ? 'kastellan' : location.hostname;
	const files = (b: { ca: string; cert: string; key: string }) =>
		[
			'# As root on the device: the certificate authority, its certificate, its key.',
			'install -d -m 700 /etc/rsyslog.d/kastellan',
			"cat > /etc/rsyslog.d/kastellan/ca.pem <<'EOF'",
			b.ca.trim(),
			'EOF',
			"cat > /etc/rsyslog.d/kastellan/cert.pem <<'EOF'",
			b.cert.trim(),
			'EOF',
			"(umask 077; cat > /etc/rsyslog.d/kastellan/key.pem <<'EOF'",
			b.key.trim(),
			'EOF',
			')'
		].join('\n');
	const rsyslog = (port: number) =>
		[
			'# /etc/rsyslog.d/90-kastellan.conf (needs rsyslog-gnutls), then: systemctl restart rsyslog',
			'global(DefaultNetstreamDriverCAFile="/etc/rsyslog.d/kastellan/ca.pem"',
			'       DefaultNetstreamDriverCertFile="/etc/rsyslog.d/kastellan/cert.pem"',
			'       DefaultNetstreamDriverKeyFile="/etc/rsyslog.d/kastellan/key.pem")',
			`*.* action(type="omfwd" target="${hub}" port="${port}" protocol="tcp"`,
			'           StreamDriver="gtls" StreamDriverMode="1" StreamDriverAuthMode="x509/certvalid"',
			'           StreamDriver.CheckExtendedKeyPurpose="on"',
			'           TCP_Framing="octet-counted" queue.type="LinkedList" queue.size="10000"',
			'           action.resumeRetryCount="-1")'
		].join('\n');
</script>

<SetupCard
	title="Send its logs (syslog over TLS)"
	about="Encrypted log forwarding with a certificate for this device."
	stage={st?.issued ? 'set' : 'available'}
	note={st?.issued && st.issued_at ? `certificate since ${dayClock(st.issued_at)}` : ''}
	onopen={load}
>
	{#if st && !st.enabled}
		<p class="mt-2 text-sm" style:color="var(--warning-text)">
			Syslog over TLS is off: turn it on under Settings → Logs first.
		</p>
	{:else if st && !st.listening}
		<p class="mt-2 text-sm" style:color="var(--warning-text)">
			Kastellan isn't listening on TCP {st.port} (see the Logs page).
		</p>
	{/if}
	<p class="mt-2 text-sm text-muted">
		The private key is shown once. New certificates leave earlier ones valid until you revoke them.
	</p>
	{#if bundle}
		<p class="mt-3 text-sm font-medium">1. The files (copy now; the key isn't shown again)</p>
		<CodeBlock text={files(bundle)} />
		<p class="mt-3 text-sm font-medium">2. rsyslog</p>
		<CodeBlock text={rsyslog(bundle.port)} />
	{/if}
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	<div class="mt-4 flex flex-wrap gap-2">
		<button class="btn btn-primary btn-sm" disabled={!canSetUp || busy} onclick={issue}>
			{busy ? 'Making it…' : st?.issued ? 'Make a new certificate' : 'Make its certificate'}
		</button>
		{#if st?.issued && canSetUp}
			<button class="btn btn-sm" onclick={revoke}>Revoke its certificates</button>
		{/if}
	</div>
	{#if !store.me?.user}<p class="mt-2 text-xs text-muted">Needs sign-in to be on.</p>{/if}
</SetupCard>
