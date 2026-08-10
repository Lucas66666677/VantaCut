"use client";

import type { TimelineClip } from "@/types/timeline";

export type MixBusId = "dialogue" | "music" | "sfx" | "master";
export interface MeterReading { rms: number; peak: number; clipped: boolean; timestamp: number; }
export interface BusRoute { clipId: string; bus: Exclude<MixBusId, "master">; }

interface BusNodes { input: GainNode; low: BiquadFilterNode; presence: BiquadFilterNode; compressor: DynamicsCompressorNode; meter?: AudioWorkletNode; output: GainNode; }
const EMPTY_METER: MeterReading = { rms: 0, peak: 0, clipped: false, timestamp: 0 };
const dbToGain = (db: number) => 10 ** (db / 20);

/** Browser preview mixer. Sources are connected once to buses; effects never get duplicated per Clip. */
export class AudioMixEngine {
  private context?: AudioContext;
  private buses = new Map<MixBusId, BusNodes>();
  private masterInput?: GainNode;
  private meters = new Map<MixBusId, MeterReading>();
  private listeners = new Set<(bus: MixBusId, reading: MeterReading) => void>();
  private workletReady?: Promise<void>;
  private duckingEnabled = true;
  private clippedAt = new Map<MixBusId, number>();

  async initialise(): Promise<void> {
    const context = this.ensureContext(); await context.resume();
    if (!this.workletReady) this.workletReady = context.audioWorklet.addModule("/audio-meter.worklet.js").catch(() => undefined);
    await this.workletReady;
    if (this.buses.size) return;
    this.masterInput = context.createGain(); const masterCompressor = context.createDynamicsCompressor(); const masterGain = context.createGain();
    masterCompressor.threshold.value = -1; masterCompressor.knee.value = 2; masterCompressor.ratio.value = 12; masterCompressor.attack.value = .003; masterCompressor.release.value = .12;
    this.masterInput.connect(masterCompressor).connect(masterGain).connect(context.destination); masterGain.gain.value = 1;
    this.buses.set("master", { input: this.masterInput, low: context.createBiquadFilter(), presence: context.createBiquadFilter(), compressor: masterCompressor, output: masterGain });
    await Promise.all([this.createBus("dialogue"), this.createBus("music"), this.createBus("sfx")]);
  }

  private async createBus(id: Exclude<MixBusId, "master">): Promise<void> {
    const context = this.ensureContext(); const input = context.createGain(); const low = context.createBiquadFilter(); const presence = context.createBiquadFilter(); const compressor = context.createDynamicsCompressor(); const output = context.createGain();
    low.type = id === "dialogue" ? "highpass" : "lowshelf"; low.frequency.value = id === "dialogue" ? 75 : 140;
    presence.type = "peaking"; presence.frequency.value = id === "dialogue" ? 3_200 : 1_800; presence.Q.value = .8; presence.gain.value = id === "dialogue" ? 2.5 : 0;
    compressor.threshold.value = id === "dialogue" ? -22 : -16; compressor.knee.value = 12; compressor.ratio.value = id === "dialogue" ? 3.5 : 2; compressor.attack.value = .012; compressor.release.value = .18;
    let meter: AudioWorkletNode | undefined;
    if (context.audioWorklet) { try { meter = new AudioWorkletNode(context, "editor-audio-meter"); meter.port.onmessage = (event: MessageEvent<{ rms: number; peak: number }>) => this.publishMeter(id, event.data.rms, event.data.peak); } catch { /* analyser fallback deliberately omitted; mixer remains functional without metering */ } }
    input.connect(low).connect(presence).connect(compressor); if (meter) compressor.connect(meter).connect(output); else compressor.connect(output); output.connect(this.masterInput!);
    this.buses.set(id, { input, low, presence, compressor, meter, output });
  }

  connectClipSource(clipId: string, source: AudioNode, bus: Exclude<MixBusId, "master">): () => void {
    const target = this.buses.get(bus); if (!target) throw new Error("Audio mixer must be initialised before routing sources"); source.connect(target.input);
    return () => { try { source.disconnect(target.input); } catch { /* source was already disposed */ } void clipId; };
  }
  setBusGain(bus: MixBusId, decibels: number): void { const target = this.buses.get(bus); if (!target) return; const now = this.ensureContext().currentTime; target.output.gain.cancelScheduledValues(now); target.output.gain.setTargetAtTime(dbToGain(decibels), now, .018); }
  setDucking(enabled: boolean): void { this.duckingEnabled = enabled; if (!enabled) this.setBusGain("music", -12); }
  setDialogueTone(enabled: boolean): void { const dialogue = this.buses.get("dialogue"); if (!dialogue) return; if (!enabled) return; const oscillator = this.ensureContext().createOscillator(); const gain = this.ensureContext().createGain(); oscillator.frequency.value = 220; gain.gain.value = .035; oscillator.connect(gain).connect(dialogue.input); oscillator.start(); oscillator.stop(this.ensureContext().currentTime + .5); }
  subscribe(listener: (bus: MixBusId, reading: MeterReading) => void): () => void { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  routeTimeline(clips: TimelineClip[]): BusRoute[] { return clips.filter((clip) => clip.audio_enabled && clip.reviewStatus !== "cut").map((clip) => ({ clipId: clip.id, bus: clip.track === "audio_overlay" ? (clip.kind?.includes("music") || clip.id.includes("music") ? "music" : "sfx") : "dialogue" })); }
  dispose(): void { this.buses.forEach((bus) => { bus.meter?.disconnect(); bus.input.disconnect(); bus.output.disconnect(); }); this.buses.clear(); void this.context?.close(); this.context = undefined; this.masterInput = undefined; }
  private ensureContext(): AudioContext { if (this.context) return this.context; const Context = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext; this.context = new Context({ latencyHint: "interactive" }); return this.context; }
  private publishMeter(bus: MixBusId, rms: number, peak: number): void {
    const now = performance.now(); if (peak >= .999) this.clippedAt.set(bus, now); const reading = { rms, peak, clipped: now - (this.clippedAt.get(bus) ?? -Infinity) < 1_250, timestamp: now }; this.meters.set(bus, reading); this.listeners.forEach((listener) => listener(bus, reading));
    if (bus === "dialogue" && this.duckingEnabled) { const music = this.buses.get("music"); if (!music) return; const context = this.ensureContext(); const target = rms > .018 ? dbToGain(-20) : dbToGain(-12); const timeConstant = rms > .018 ? .035 : .32; music.output.gain.setTargetAtTime(target, context.currentTime, timeConstant); }
  }
}

let sharedMixer: AudioMixEngine | null = null;
export function getAudioMixEngine(): AudioMixEngine { sharedMixer ??= new AudioMixEngine(); return sharedMixer; }
