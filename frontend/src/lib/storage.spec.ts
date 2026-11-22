import { describe, expect, it } from 'vitest';
import type { HostReport } from './api';
import { disksOf, fullInText, volumesOf } from './storage';

const limits = { warning: 85, critical: 95 };

describe('volumesOf', () => {
	const report: HostReport = {
		filesystems: [
			{ mount: '/', device: '/dev/sda1', type: 'ext4', total: 100, used: 40, available: 60 },
			{ mount: '/srv', device: '/dev/md0', type: 'ext4', total: 100, used: 92, available: 8 }
		]
	};

	it('says how full each is against what can still be written, toned by the disk limits', () => {
		const [root, srv] = volumesOf(report, {}, limits);
		expect(root.percent).toBe(40);
		expect(root.tone).toBe('normal');
		expect(srv.percent).toBe(92);
		expect(srv.tone).toBe('warning');
		expect(srv.free).toBe(8);
	});

	it('marks a volume full within the alarm days as soon', () => {
		const [root, srv] = volumesOf(report, { '/': 400, '/srv': 6 }, limits, 14);
		expect(root.fullIn).toBe(400);
		expect(root.soon).toBe(false);
		expect(srv.soon).toBe(true);
		expect(volumesOf(report, {}, limits)[0].fullIn).toBeNull();
	});

	it('words the days until full', () => {
		expect(fullInText(0.4)).toBe('within a day');
		expect(fullInText(6.4)).toBe('in ~6 d');
	});
});

describe('disksOf', () => {
	it('lists arrays first, a degraded one as such, with its member map and progress', () => {
		const rows = disksOf({
			raid: [
				{
					name: 'md0',
					active: true,
					level: 'raid1',
					devices: ['sda1', 'sdb1'],
					failed: [],
					status: 'U_',
					progress: { what: 'recovery', percent: 42 }
				}
			],
			smart: { available: true, disks: [{ disk: 'sda', model: 'WD Red', passed: true }] }
		});
		expect(rows.map((r) => r.name)).toEqual(['md0', 'sda']);
		expect(rows[0].state.word).toBe('Degraded');
		expect(rows[0].what).toBe('raid1 of sda1, sdb1');
		expect(rows[0].status).toBe('[U_]');
		expect(rows[0].note).toBe('recovery 42%');
	});

	it('gives a disk one state: wear, failing, healthy, or not read with why', () => {
		const rows = disksOf({
			smart: {
				available: true,
				disks: [
					{ disk: 'sda', passed: true, reallocated: 8, temperature: 38, power_on_hours: 17520 },
					{ disk: 'sdb', passed: false },
					{ disk: 'sdc', passed: true },
					{ disk: 'sdd', asleep: true },
					{ disk: 'sde', error: 'no permission' },
					{ disk: 'sdf' }
				]
			}
		});
		expect(rows.map((r) => r.state.word)).toEqual([
			'Wear',
			'Failing',
			'Healthy',
			'Not read',
			'Not read',
			'Not read'
		]);
		expect(rows[0].temperature).toBe(38);
		expect(rows[0].years).toBe(2);
		expect(rows.slice(3).map((r) => r.note)).toEqual([
			'asleep (not woken to read it)',
			'no permission',
			'health unknown'
		]);
	});
});
