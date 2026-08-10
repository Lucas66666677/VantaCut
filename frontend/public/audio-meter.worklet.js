class AudioMeterProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.samples = 0; this.sumSquares = 0; this.peak = 0; this.interval = Math.max(128, sampleRate / 60); }
  process(inputs) {
    const input = inputs[0];
    for (const channel of input) for (let index = 0; index < channel.length; index += 1) {
      const value = channel[index]; this.sumSquares += value * value; this.peak = Math.max(this.peak, Math.abs(value)); this.samples += 1;
    }
    if (this.samples >= this.interval) {
      this.port.postMessage({ rms: Math.sqrt(this.sumSquares / Math.max(1, this.samples)), peak: this.peak });
      this.samples = 0; this.sumSquares = 0; this.peak = 0;
    }
    return true;
  }
}
registerProcessor("editor-audio-meter", AudioMeterProcessor);
