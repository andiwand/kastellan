import { expect, it } from 'vitest';
import type { Vitals } from './api';
import { detailOf, fieldsOf, formatVital, limitsOf, toneOf } from './vitals';

const v = (more: Partial<Vitals>): Vitals => ({
	cpu: null,
	load: null,
	cores: null,
	memory: null,
	memory_used: null,
	memory_total: null,
	storage: null,
	storage_used: null,
	storage_total: null,
	storage_mount: null,
	temperature: null,
	checked: '',
	...more
});

it('shows what a device tells, the CPU also by its load alone', () => {
	expect(fieldsOf(v({ load: 0.4, memory: 50 }))).toEqual(['cpu', 'memory']);
	expect(fieldsOf(v({ cpu: 3, storage: 20, temperature: 50 }))).toEqual([
		'cpu',
		'storage',
		'temperature'
	]);
	expect(fieldsOf(null)).toEqual([]);
});

it('colours only what is high, storage by the disk alarms', () => {
	const disk = { warning: 80, critical: 90 };
	expect(toneOf(84, limitsOf('storage', disk))).toBe('warning');
	expect(toneOf(84, limitsOf('cpu', disk))).toBe('normal');
	expect(toneOf(96, limitsOf('cpu', disk))).toBe('critical');
	expect(toneOf(null, limitsOf('cpu', disk))).toBe('normal');
});

it('says what a value is of', () => {
	expect(detailOf('cpu', v({ load: 0.42, cores: 4 }))).toBe('load 0.42 · 4 cores');
	expect(detailOf('memory', v({ memory_used: 1.2e9, memory_total: 4e9 }))).toBe('1.2 GB of 4.0 GB');
	expect(detailOf('memory', v({ memory_used: 55, memory_total: 100 }))).toBe(''); // shares only
	expect(
		detailOf('storage', v({ storage_used: 2e9, storage_total: 8e9, storage_mount: '/' }))
	).toBe('2.0 GB of 8.0 GB · /');
	expect(formatVital('temperature', 51.6)).toBe('52 °C');
	expect(formatVital('cpu', null)).toBe('–');
});
