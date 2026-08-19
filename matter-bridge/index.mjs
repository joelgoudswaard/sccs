#!/usr/bin/env node
/**
 * SCCS Matter bridge — child process.
 * Python sends JSON lines on stdin; this process writes JSON lines on stdout.
 */
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { Endpoint, Environment, ServerNode, VendorId } from "@matter/main";
import { BridgedDeviceBasicInformationServer } from "@matter/main/behaviors/bridged-device-basic-information";
import { DimmableLightDevice } from "@matter/main/devices/dimmable-light";
import { OnOffLightDevice } from "@matter/main/devices/on-off-light";
import { OnOffPlugInUnitDevice } from "@matter/main/devices/on-off-plug-in-unit";
import { ContactSensorDevice } from "@matter/main/devices/contact-sensor";
import { TemperatureSensorDevice } from "@matter/main/devices/temperature-sensor";
import { HumiditySensorDevice } from "@matter/main/devices/humidity-sensor";
import { AggregatorEndpoint } from "@matter/main/endpoints/aggregator";

const cfgPath = process.argv[2];
if (!cfgPath) {
    console.error("usage: node index.mjs <config.json>");
    process.exit(2);
}

const cfg = JSON.parse(readFileSync(cfgPath, "utf8"));
const specs = cfg.specs || [];
const endpoints = new Map();
const applying = new Set();

function emit(obj) {
    process.stdout.write(JSON.stringify(obj) + "\n");
}

function pctToLevel(pct) {
    const n = Number(pct) || 0;
    if (n <= 0) return 1;
    return Math.max(1, Math.min(254, Math.round((n * 254) / 100)));
}

function levelToPct(level) {
    const n = Number(level) || 0;
    return Math.max(0, Math.min(100, Math.round((n * 100) / 254)));
}

function deviceClass(kind) {
    switch (kind) {
        case "light":
            return DimmableLightDevice;
        case "relay_light":
            return OnOffLightDevice;
        case "relay_switch":
        case "scene":
            return OnOffPlugInUnitDevice;
        case "reed":
            return ContactSensorDevice;
        case "temperature":
            return TemperatureSensorDevice;
        case "water":
            return HumiditySensorDevice;
        default:
            return null;
    }
}

function endpointId(key) {
    return key.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 32);
}

if (cfg.storage_path) {
    Environment.default.vars.set("storage.path", cfg.storage_path);
}
if (cfg.mdns_interface) {
    Environment.default.vars.set("mdns.networkInterface", cfg.mdns_interface);
}

const uniqueId = cfg.unique_id || "sccs";
const server = await ServerNode.create({
    id: uniqueId,
    network: { port: Number(cfg.port || 5540) },
    commissioning: {
        passcode: cfg.passcode ? Number(cfg.passcode) : undefined,
        discriminator: cfg.discriminator ? Number(cfg.discriminator) : undefined,
    },
    productDescription: {
        name: cfg.name || "SCCS",
        deviceType: AggregatorEndpoint.deviceType,
    },
    basicInformation: {
        vendorName: "SCCS",
        vendorId: VendorId(0xfff1),
        nodeLabel: cfg.name || "SCCS",
        productName: cfg.name || "SCCS",
        productLabel: cfg.name || "SCCS",
        productId: 0x8000,
        serialNumber: `sccs-${uniqueId}`,
        uniqueId,
    },
});

const aggregator = new Endpoint(AggregatorEndpoint, { id: "sccs" });
await server.add(aggregator);

async function addSpec(spec) {
    const Cls = deviceClass(spec.kind);
    if (!Cls) return;

    const Device = Cls.with(BridgedDeviceBasicInformationServer);
    const id = endpointId(spec.key);
    const endpoint = new Endpoint(Device, {
        id,
        bridgedDeviceBasicInformation: {
            nodeLabel: spec.name,
            productName: spec.name,
            productLabel: spec.name,
            serialNumber: spec.key.slice(0, 32),
            reachable: true,
        },
    });
    await aggregator.add(endpoint);
    endpoints.set(spec.key, { endpoint, spec, lastNonzero: 100 });

    if (spec.kind === "light" || spec.kind === "relay_light" || spec.kind === "relay_switch") {
        endpoint.events.onOff.onOff$Changed.on((value) => {
            if (applying.has(spec.key)) return;
            const rec = endpoints.get(spec.key);
            const brightness = spec.has_brightness
                ? value
                    ? rec.lastNonzero
                    : 0
                : value
                  ? 100
                  : 0;
            emit({
                type: "command",
                key: spec.key,
                kind: spec.kind,
                entity: spec.entity,
                on: Boolean(value),
                brightness,
            });
        });
    }
    if (spec.has_brightness && endpoint.events.levelControl) {
        endpoint.events.levelControl.currentLevel$Changed.on((level) => {
            if (applying.has(spec.key)) return;
            const rec = endpoints.get(spec.key);
            const pct = levelToPct(level);
            if (pct > 0) rec.lastNonzero = pct;
            emit({
                type: "command",
                key: spec.key,
                kind: spec.kind,
                entity: spec.entity,
                on: pct > 0,
                brightness: pct,
            });
        });
    }
    if (spec.kind === "scene") {
        endpoint.events.onOff.onOff$Changed.on((value) => {
            if (applying.has(spec.key)) return;
            if (!value) return;
            emit({
                type: "command",
                key: spec.key,
                kind: spec.kind,
                entity: spec.entity,
                on: true,
            });
            applying.add(spec.key);
            endpoint
                .set({ onOff: { onOff: false } })
                .catch(() => {})
                .finally(() => applying.delete(spec.key));
        });
    }

    if (spec.has_bug_mode) {
        const bugKey = `${spec.key}:bug`;
        const bugId = endpointId(bugKey);
        const bug = new Endpoint(OnOffPlugInUnitDevice.with(BridgedDeviceBasicInformationServer), {
            id: bugId,
            bridgedDeviceBasicInformation: {
                nodeLabel: `${spec.name} Bug Mode`,
                productName: `${spec.name} Bug Mode`,
                productLabel: `${spec.name} Bug Mode`,
                serialNumber: bugKey.slice(0, 32),
                reachable: true,
            },
        });
        await aggregator.add(bug);
        endpoints.set(bugKey, { endpoint: bug, spec, lastNonzero: 100 });
        bug.events.onOff.onOff$Changed.on((value) => {
            if (applying.has(bugKey)) return;
            emit({
                type: "command",
                key: spec.key,
                kind: spec.kind,
                entity: spec.entity,
                mode: value ? "red" : "white",
            });
        });
    }
}

for (const spec of specs) {
    try {
        await addSpec(spec);
    } catch (err) {
        emit({ type: "error", message: `add ${spec.key}: ${err.message || err}` });
    }
}

function commissioningSnapshot() {
    const c = server.state.commissioning || {};
    const codes = c.pairingCodes || c;
    return {
        type: "status",
        commissioned: Boolean(c.commissioned || (c.fabrics && Object.keys(c.fabrics).length)),
        fabric_count: c.fabrics ? Object.keys(c.fabrics).length : c.commissioned ? 1 : 0,
        qr: codes.qrPairingCode || c.qrPairingCode || "",
        manual: codes.manualPairingCode || c.manualPairingCode || "",
        passcode: c.passcode || "",
    };
}

try {
    await server.start();
    emit({ type: "ready", ...commissioningSnapshot() });
} catch (err) {
    emit({ type: "error", message: String(err.message || err) });
    process.exit(1);
}

if (server.events?.commissioning?.commissioned$Changed) {
    server.events.commissioning.commissioned$Changed.on(() => {
        emit(commissioningSnapshot());
    });
}

async function applySet(msg) {
    const rec = endpoints.get(msg.key);
    if (!rec) return;
    applying.add(msg.key);
    try {
        const patch = {};
        if (typeof msg.on === "boolean") {
            patch.onOff = { onOff: msg.on };
        }
        if (msg.brightness != null && rec.spec.has_brightness) {
            const pct = Math.max(0, Math.min(100, Number(msg.brightness)));
            if (pct > 0) rec.lastNonzero = pct;
            patch.levelControl = { currentLevel: pctToLevel(pct) };
            if (msg.on === undefined) patch.onOff = { onOff: pct > 0 };
        }
        if (msg.mode != null) {
            const bug = endpoints.get(`${msg.key}:bug`);
            if (bug) {
                applying.add(`${msg.key}:bug`);
                try {
                    await bug.endpoint.set({ onOff: { onOff: String(msg.mode).toLowerCase() === "red" } });
                } finally {
                    applying.delete(`${msg.key}:bug`);
                }
            }
        }
        if (msg.closed != null && rec.spec.kind === "reed") {
            await rec.endpoint.set({ booleanState: { stateValue: Boolean(msg.closed) } });
            return;
        }
        if (msg.celsius != null && rec.spec.kind === "temperature") {
            await rec.endpoint.set({
                temperatureMeasurement: { measuredValue: Math.round(Number(msg.celsius) * 100) },
            });
            return;
        }
        if (msg.percent != null && rec.spec.kind === "water") {
            await rec.endpoint.set({
                relativeHumidityMeasurement: { measuredValue: Math.round(Number(msg.percent) * 100) },
            });
            return;
        }
        if (Object.keys(patch).length) {
            await rec.endpoint.set(patch);
        }
    } catch (err) {
        emit({ type: "error", message: `set ${msg.key}: ${err.message || err}` });
    } finally {
        applying.delete(msg.key);
    }
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
    let msg;
    try {
        msg = JSON.parse(line);
    } catch {
        return;
    }
    if (msg.type === "set") {
        applySet(msg);
    } else if (msg.type === "status") {
        emit(commissioningSnapshot());
    } else if (msg.type === "shutdown") {
        server
            .close()
            .catch(() => {})
            .finally(() => process.exit(0));
    }
});

process.on("SIGTERM", () => {
    server
        .close()
        .catch(() => {})
        .finally(() => process.exit(0));
});
