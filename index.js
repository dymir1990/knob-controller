#!/usr/bin/env node
/**
 * Knob Controller - Controls Mac system volume and Google Nest Hub volume
 * using a VSDinside StreamDock N3 macro pad.
 *
 * Big knob (KNOB_3)  -> Mac system volume
 * Bottom-right knob (KNOB_2) -> Google Nest Hub volume
 */

const HID = require('node-hid');
const { execSync } = require('child_process');
const https = require('https');
const net = require('net');

// ─── Telegram Alerts ───
const TG_BOT_TOKEN = '8274257522:AAEphv-D7mhSx8VmPV6T9k4mm-Im2ofX6G4';
const TG_CHAT_ID = '8228433205';

function sendTelegramAlert(message) {
  const data = JSON.stringify({ chat_id: TG_CHAT_ID, text: `⚠️ Knob Controller: ${message}`, parse_mode: 'HTML' });
  const req = https.request({
    hostname: 'api.telegram.org',
    path: `/bot${TG_BOT_TOKEN}/sendMessage`,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  req.on('error', () => {});
  req.write(data);
  req.end();
}

// ─── Device Config ───
const VID = 0x5548;
const PID = 0x1001;

// Knob event codes (from N3 protocol)
const KNOB_EVENTS = {
  0x50: { knob: 'big', action: 'left' },     // Big knob CCW
  0x51: { knob: 'big', action: 'right' },    // Big knob CW
  0x60: { knob: 'small_br', action: 'left' },  // Bottom-right CCW
  0x61: { knob: 'small_br', action: 'right' }, // Bottom-right CW
  0x90: { knob: 'small_bl', action: 'left' },  // Bottom-left CCW
  0x91: { knob: 'small_bl', action: 'right' }, // Bottom-left CW
  0x33: { knob: 'small_bl', action: 'press' }, // Bottom-left press
  0x34: { knob: 'small_br', action: 'press' }, // Bottom-right press
  0x35: { knob: 'big', action: 'press' },      // Big knob press
};

// Volume step sizes
const SYSTEM_VOL_STEP = 5;      // percentage per tick
const CAST_VOL_STEP = 0.05;     // 0-1 scale, 5% per tick

// ─── Google Nest Hub (Cast v2) ───
const NEST_HUB_IP = '10.0.0.213';
const CAST_PORT = 8009;

let castVolume = 0.5; // Will be updated from device
let castMuted = false;

class CastVolumeController {
  constructor(host) {
    this.host = host;
    this.client = null;
    this.receiver = null;
    this.connected = false;
    this.volume = 0.5;
    this.connect();
  }

  connect() {
    const Client = require('castv2-client').Client;
    const DefaultMediaReceiver = require('castv2-client').DefaultMediaReceiver;

    this.client = new Client();

    this.client.on('error', (err) => {
      console.log(`Cast error: ${err.message}`);
      this.connected = false;
      // Retry after 5 seconds
      setTimeout(() => this.connect(), 5000);
    });

    this.client.connect(this.host, () => {
      console.log(`Connected to Nest Hub at ${this.host}`);
      this.connected = true;

      // Get current volume
      this.client.getVolume((err, vol) => {
        if (!err && vol) {
          this.volume = vol.level;
          console.log(`Current Nest Hub volume: ${Math.round(this.volume * 100)}%`);
        }
      });
    });
  }

  adjustVolume(delta) {
    if (!this.connected || !this.client) {
      console.log('Nest Hub not connected');
      return;
    }

    this.volume = Math.max(0, Math.min(1, this.volume + delta));
    this.client.setVolume({ level: this.volume }, (err) => {
      if (err) {
        console.log(`Cast volume error: ${err.message}`);
        this.connected = false;
      } else {
        console.log(`Nest Hub volume: ${Math.round(this.volume * 100)}%`);
      }
    });
  }

  toggleMute() {
    if (!this.connected || !this.client) return;
    this.client.getVolume((err, vol) => {
      if (!err && vol) {
        this.client.setVolume({ muted: !vol.muted }, (err) => {
          if (!err) {
            console.log(`Nest Hub ${!vol.muted ? 'muted' : 'unmuted'}`);
          }
        });
      }
    });
  }
}

// ─── System Volume ───
// When running as root (LaunchDaemon), osascript can't access the user audio session.
// Run as the logged-in user instead.
const SUDO_USER = process.env.SUDO_USER || 'dymirtatem';
function asUser(cmd) {
  return `su - ${SUDO_USER} -c '${cmd}'`;
}

function getSystemVolume() {
  try {
    const result = execSync(asUser('osascript -e "output volume of (get volume settings)"'), { encoding: 'utf8' });
    return parseInt(result.trim(), 10);
  } catch {
    return 50;
  }
}

function setSystemVolume(vol) {
  vol = Math.max(0, Math.min(100, vol));
  execSync(asUser(`osascript -e "set volume output volume ${vol}"`));
  return vol;
}

function adjustSystemVolume(delta) {
  const current = getSystemVolume();
  const newVol = setSystemVolume(current + delta);
  console.log(`System volume: ${newVol}%`);
}

function toggleSystemMute() {
  try {
    const result = execSync(asUser('osascript -e "output muted of (get volume settings)"'), { encoding: 'utf8' });
    const muted = result.trim() === 'true';
    execSync(asUser(`osascript -e "set volume output muted ${!muted}"`));
    console.log(`System ${!muted ? 'muted' : 'unmuted'}`);
  } catch (e) {
    console.log(`Mute toggle error: ${e.message}`);
  }
}

// ─── Main ───
function findDevice(retries = 30, interval = 2000) {
  return new Promise((resolve, reject) => {
    let attempt = 0;
    function tryFind() {
      attempt++;
      const devices = HID.devices().filter(d =>
        d.vendorId === VID && d.productId === PID && d.usagePage > 1000 && d.usage === 1
      );
      if (devices.length > 0) {
        resolve(devices[0]);
      } else if (attempt >= retries) {
        reject(new Error(`StreamDock N3 not found after ${retries} attempts (${retries * interval / 1000}s)`));
      } else {
        console.log(`Waiting for StreamDock N3... (attempt ${attempt}/${retries})`);
        setTimeout(tryFind, interval);
      }
    }
    tryFind();
  });
}

function initStreamDock() {
  // Run the Python SDK init script to wake the device and enable knob input.
  // The proprietary C library handles the wake/brightness/refresh handshake.
  console.log('Initializing StreamDock N3 via Python SDK...');
  try {
    execSync('python3 /Users/dymirtatem/Desktop/Projects/knob-controller/init_device.py', {
      encoding: 'utf8',
      timeout: 15000,
    });
    console.log('StreamDock N3 initialized');
  } catch (e) {
    console.log(`Init warning: ${e.message}`);
    // Continue anyway — device may already be initialized
  }
}

async function main() {
  console.log('Knob Controller starting...');

  // Initialize device (wake screen, set brightness, enable input reporting)
  initStreamDock();

  // Find device (retry for up to 60s after boot)
  const devInfo = await findDevice();
  console.log(`Found StreamDock N3 at ${devInfo.path}`);

  // Connect to Nest Hub
  const nestHub = new CastVolumeController(NEST_HUB_IP);

  // Open HID device
  const device = new HID.HID(devInfo.path);

  device.on('data', (data) => {
    // Log all raw HID data for debugging
    console.log('RAW:', Array.from(data).map(b => b.toString(16).padStart(2,'0')).join(' '));

    if (data.length < 11) return;

    const funcCode = data[9];
    const state = data[10];
    const event = KNOB_EVENTS[funcCode];

    if (!event) {
      console.log(`Unknown funcCode: 0x${funcCode.toString(16)}`);
      return;
    }

    // For press events, only act on press (state=1), ignore release (state=0)
    if (event.action === 'press' && state !== 1) return;

    switch (event.knob) {
      case 'big':
        if (event.action === 'left') adjustSystemVolume(-SYSTEM_VOL_STEP);
        else if (event.action === 'right') adjustSystemVolume(SYSTEM_VOL_STEP);
        else if (event.action === 'press') toggleSystemMute();
        break;

      case 'small_br':
        if (event.action === 'left') nestHub.adjustVolume(-CAST_VOL_STEP);
        else if (event.action === 'right') nestHub.adjustVolume(CAST_VOL_STEP);
        else if (event.action === 'press') nestHub.toggleMute();
        break;

      case 'small_bl':
        // Bottom-left knob - available for future use
        break;
    }
  });

  device.on('error', (err) => {
    console.error(`HID error: ${err.message}`);
    sendTelegramAlert(`HID crash — ${err.message}. Restarting via launchd.`);
    setTimeout(() => process.exit(1), 1000);
  });

  console.log('\nKnob Controller running!');
  console.log('  Big knob       -> Mac system volume (press to mute)');
  console.log('  Bottom-right   -> Nest Hub volume (press to mute)');
  console.log('  Press Ctrl+C to stop.\n');

  // Handle graceful shutdown
  process.on('SIGINT', () => {
    console.log('\nShutting down...');
    device.close();
    if (nestHub.client) nestHub.client.close();
    process.exit(0);
  });
}

main();
