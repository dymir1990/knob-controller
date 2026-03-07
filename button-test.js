#!/usr/bin/env node
/**
 * Button/knob test — dumps all raw HID data from every StreamDock N3 interface.
 * Run with: sudo node button-test.js
 * Then press/turn every button and knob.
 */
const HID = require('node-hid');

const VID = 0x5548;
const PID = 0x1001;

const allDevs = HID.devices().filter(d => d.vendorId === VID && d.productId === PID);
console.log(`Found ${allDevs.length} interfaces:\n`);
allDevs.forEach((d, i) => {
  console.log(`  [${i}] path=${d.path} usagePage=0x${d.usagePage.toString(16)} usage=${d.usage} interface=${d.interface}`);
});
console.log('');

// Try opening ALL interfaces
const opened = [];
for (const info of allDevs) {
  try {
    const dev = new HID.HID(info.path);
    console.log(`Opened: ${info.path} (usagePage=0x${info.usagePage.toString(16)} usage=${info.usage})`);
    dev.on('data', (buf) => {
      const hex = Array.from(buf).map(b => b.toString(16).padStart(2, '0')).join(' ');
      console.log(`[usagePage=0x${info.usagePage.toString(16)} usage=${info.usage}] ${hex}`);
    });
    dev.on('error', (e) => console.log(`Error on ${info.path}: ${e.message}`));
    opened.push(dev);
  } catch (e) {
    console.log(`Failed to open ${info.path}: ${e.message}`);
  }
}

if (opened.length === 0) {
  console.log('\nNo interfaces could be opened.');
  process.exit(1);
}

console.log(`\nListening on ${opened.length} interface(s) — press buttons, turn knobs. Ctrl+C to stop.\n`);

process.on('SIGINT', () => {
  opened.forEach(d => d.close());
  process.exit(0);
});
