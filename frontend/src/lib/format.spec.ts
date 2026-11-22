import { expect, it } from 'vitest';
import {
	ago,
	bandLabel,
	bandOfFrequency,
	bitrate,
	bytes,
	clock,
	date,
	day,
	dayClock,
	duration,
	hoursLabel,
	kbitrate,
	stamp,
	versionSubject
} from './format';

it('says what a software change is about', () => {
	expect(versionSubject('@firmware')).toBe('Firmware');
	expect(versionSubject('@container nginx')).toBe('Container nginx');
	expect(versionSubject('web')).toBe('web');
});

it('writes sizes and rates in SI units', () => {
	expect(bytes(null)).toBe('–');
	expect(bytes(512)).toBe('512 B');
	expect(bytes(12_400)).toBe('12 kB');
	expect(bytes(4_294_967_296)).toBe('4.3 GB');
	expect(bytes(2e12)).toBe('2.0 TB');
	expect(bitrate(undefined)).toBe('–');
	expect(bitrate(400_000)).toBe('0.4 Mbit/s');
	expect(bitrate(95_300_000)).toBe('95 Mbit/s');
	expect(bitrate(1.2e9)).toBe('1.2 Gbit/s');
	expect(kbitrate(null)).toBe('–');
	expect(kbitrate(250_000)).toBe('250 Mbit/s');
});

it('names Wi-Fi bands', () => {
	expect(bandLabel('5g')).toBe('5 GHz');
	expect(bandLabel('60g')).toBe('60g');
	expect(bandLabel(null)).toBe('');
	expect(bandOfFrequency(2412)).toBe('2.4 GHz');
	expect(bandOfFrequency(5180)).toBe('5 GHz');
	expect(bandOfFrequency(5975)).toBe('6 GHz');
});

// Local times, so the tests hold in any time zone.
const now = new Date(2026, 10, 22, 14, 5, 7).getTime();

it('writes times one way: 24-hour clock, English month, the year only when another', () => {
	expect(clock(new Date(now))).toBe('14:05');
	expect(clock(new Date(now), true)).toBe('14:05:07');
	expect(clock(now / 1000)).toBe('14:05'); // Unix seconds
	expect(date(new Date(2026, 0, 3), now)).toBe('Jan 3');
	expect(date(new Date(2025, 11, 31), now)).toBe('Dec 31 2025');
	expect(day(new Date(2026, 10, 2, 9, 7), now)).toBe('Nov 2, 09:07');
});

it('writes a time in a list by how long ago its day was', () => {
	expect(dayClock(new Date(2026, 10, 22, 0, 1), now)).toBe('00:01');
	expect(dayClock(new Date(2026, 10, 21, 23, 59), now)).toBe('Yesterday 23:59');
	expect(dayClock(new Date(2026, 10, 20, 8, 0), now)).toBe('Nov 20, 08:00');
	expect(dayClock(new Date(2026, 10, 22, 14, 5, 7), now, true)).toBe('14:05:07');
});

it('writes recent times relative, and durations', () => {
	const iso = (s: number) => new Date(now - s * 1000).toISOString();
	expect(ago(iso(2), now)).toBe('now');
	expect(ago(iso(300), now)).toBe('5 min ago');
	expect(ago(iso(3 * 3600), now)).toBe('3 h ago');
	expect(ago(null, now)).toBe('');
	expect(duration(45)).toBe('45 s');
	expect(duration(3 * 3600 + 300)).toBe('3 h 05 min');
	expect(duration(3 * 86400 + 4 * 3600)).toBe('3 d 4 h');
});

it('reads a time a device wrote as text, and leaves other text alone', () => {
	expect(stamp('2026-11-12 03:41:07 +0100', now)).toMatch(/^Nov 1[12], \d\d:\d\d$/);
	expect(stamp('Low line voltage', now)).toBe('Low line voltage');
});

it('names time ranges', () => {
	expect([1, 24, 168, 720, 0].map(hoursLabel)).toEqual(['1 h', '24 h', '7 d', '30 d', 'All']);
});
