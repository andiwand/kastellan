import { expect, it } from 'vitest';
import { bandLabel, bandOfFrequency, bitrate, bytes, kbitrate, versionSubject } from './format';

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
