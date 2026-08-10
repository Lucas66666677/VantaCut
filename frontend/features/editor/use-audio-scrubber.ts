"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { avLatencyCompensationSeconds } from "@/features/editor/av-latency-calibration";

const ATTACK_SECONDS = .01;
const RELEASE_SECONDS = .01;
const GRAIN_SECONDS = .065;
const GRAIN_OVERLAP_SECONDS = .028;

type ActiveVoice = { source: AudioBufferSourceNode; gain: GainNode };

/** A short-burst tape scrubber: no HTMLMediaElement seeking and no long-lived playback nodes. */
class AudioScrubbingEngine {
  private context?: AudioContext;
  private master?: GainNode;
  private buffer?: AudioBuffer;
  private reverseBuffer?: AudioBuffer;
  private active = new Set<ActiveVoice>();
  private previousDirection = 1;

  async prepare(url: string): Promise<void> {
    const context = this.ensureContext();
    const response = await fetch(url); if (!response.ok) throw new Error("無法載入刷盤音訊");
    this.buffer = await context.decodeAudioData(await response.arrayBuffer());
    this.reverseBuffer = undefined;
  }
  start(timeSeconds: number): void { void this.context?.resume(); this.scrub(timeSeconds, 0); }
  stop(): void { this.fadeActive(0); if (this.master) { const now = this.context!.currentTime; this.master.gain.cancelScheduledValues(now); this.master.gain.setTargetAtTime(0, now, RELEASE_SECONDS); } }
  dispose(): void { this.stop(); void this.context?.close(); this.context = undefined; this.master = undefined; this.buffer = undefined; this.reverseBuffer = undefined; }

  scrub(timeSeconds: number, velocityPxPerMs: number): void {
    if (!this.buffer) return;
    const context = this.ensureContext(); void context.resume();
    const direction = velocityPxPerMs < 0 ? -1 : 1;
    const speed = Math.max(.18, Math.min(4, .2 + Math.abs(velocityPxPerMs) * 5.2));
    const reversal = direction !== this.previousDirection; this.previousDirection = direction;
    const now = context.currentTime;
    // Envelope follower ducks aggressive reversals before they can click or overload a speaker.
    const targetGain = reversal ? .42 : Math.min(.82, .5 + speed * .1);
    this.master!.gain.cancelScheduledValues(now); this.master!.gain.setTargetAtTime(targetGain, now, ATTACK_SECONDS);
    this.fadeActive(0);
    // Output hardware (especially Bluetooth) presents audio after the browser's render clock.
    // Reading a slightly later sample makes the delayed output meet the current video frame.
    const compensatedTime = timeSeconds + avLatencyCompensationSeconds();
    if (speed < .5) this.playGranular(compensatedTime, direction, speed, now);
    else this.playTapeBurst(compensatedTime, direction, speed, now);
  }

  private ensureContext(): AudioContext {
    if (this.context && this.master) return this.context;
    const Context =
      window.AudioContext ??
      (window as Window & { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    this.context = new Context(); this.master = this.context.createGain(); this.master.gain.value = 0; this.master.connect(this.context.destination);
    return this.context;
  }
  private sourceBuffer(direction: number): AudioBuffer {
    if (direction > 0 || !this.buffer) return this.buffer!;
    if (this.reverseBuffer) return this.reverseBuffer;
    const reversed = this.ensureContext().createBuffer(this.buffer.numberOfChannels, this.buffer.length, this.buffer.sampleRate);
    for (let channel = 0; channel < this.buffer.numberOfChannels; channel += 1) reversed.copyToChannel(this.buffer.getChannelData(channel).slice().reverse(), channel);
    this.reverseBuffer = reversed; return reversed;
  }
  private offsetFor(timeSeconds: number, direction: number): number {
    const duration = this.buffer!.duration; return Math.max(0, Math.min(Math.max(0, duration - .001), direction > 0 ? timeSeconds : duration - timeSeconds));
  }
  private voice(offset: number, direction: number, playbackRate: number, when: number, duration: number, peak: number): void {
    const context = this.ensureContext(); const source = context.createBufferSource(); const gain = context.createGain();
    source.buffer = this.sourceBuffer(direction); source.playbackRate.setValueAtTime(playbackRate, when); source.connect(gain); gain.connect(this.master!);
    gain.gain.setValueAtTime(0, when); gain.gain.linearRampToValueAtTime(peak, when + ATTACK_SECONDS); gain.gain.setValueAtTime(peak, Math.max(when + ATTACK_SECONDS, when + duration - RELEASE_SECONDS)); gain.gain.linearRampToValueAtTime(0, when + duration);
    const voice = { source, gain }; this.active.add(voice); source.onended = () => { this.active.delete(voice); source.disconnect(); gain.disconnect(); };
    source.start(when, offset, duration / playbackRate); source.stop(when + duration + .01);
  }
  private playTapeBurst(timeSeconds: number, direction: number, speed: number, now: number): void { this.voice(this.offsetFor(timeSeconds, direction), direction, speed, now, .075, .9); }
  private playGranular(timeSeconds: number, direction: number, speed: number, now: number): void {
    // Overlapping Hann-like gain envelopes retain intelligibility below 0.5x without robotic gaps.
    for (let grain = 0; grain < 2; grain += 1) {
      const offset = this.offsetFor(timeSeconds + direction * grain * GRAIN_OVERLAP_SECONDS, direction);
      this.voice(offset, direction, Math.max(.35, speed), now + grain * GRAIN_OVERLAP_SECONDS, GRAIN_SECONDS, .58);
    }
  }
  private fadeActive(target: number): void {
    if (!this.context) return; const now = this.context.currentTime;
    for (const voice of this.active) { voice.gain.gain.cancelScheduledValues(now); voice.gain.gain.setTargetAtTime(target, now, RELEASE_SECONDS); try { voice.source.stop(now + RELEASE_SECONDS * 2); } catch { /* already ended */ } }
  }
}

/** Loads an audio proxy once and exposes imperative callbacks safe to call from native pointer listeners. */
export function useAudioScrubber(audioUrl?: string) {
  const engine = useRef(new AudioScrubbingEngine()); const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!audioUrl) { setReady(false); return; }
    let cancelled = false; setReady(false);
    void engine.current.prepare(audioUrl).then(() => { if (!cancelled) setReady(true); }).catch(() => { if (!cancelled) setReady(false); });
    return () => { cancelled = true; };
  }, [audioUrl]);
  useEffect(() => () => engine.current.dispose(), []);
  return { ready, start: useCallback((time: number) => engine.current.start(time), []), scrub: useCallback((time: number, velocity: number) => engine.current.scrub(time, velocity), []), stop: useCallback(() => engine.current.stop(), []) };
}
