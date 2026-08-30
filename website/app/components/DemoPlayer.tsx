'use client';

import { useEffect, useRef, useState } from 'react';

type Recording = { command: string; duration_seconds: number; events: (string | number)[][]; transcript: string };

export function DemoPlayer({ recording }: { recording: Recording }) {
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const position = useRef(0);
  function seek(value: number) { position.current = value; setElapsed(value); }
  const duration = recording.duration_seconds;
  useEffect(() => {
    if (!playing) return;
    const started = performance.now() - position.current * 1000;
    const timer = window.setInterval(() => {
      const next = Math.min((performance.now() - started) / 1000, duration);
      position.current = next;
      setElapsed(next);
      if (next >= duration) setPlaying(false);
    }, 40);
    return () => window.clearInterval(timer);
  }, [playing, duration]);
  const output = recording.events.filter(event => Number(event[0]) <= elapsed).map(event => String(event[2])).join('');
  function toggle() {
    if (!playing && elapsed >= duration) seek(0);
    setPlaying(!playing);
  }
  return <div className="recorded-demo">
    <div className="code-window-bar"><span>Recorded terminal · synthetic local run</span><span>No external provider calls</span></div>
    <pre className="demo-terminal" tabIndex={0} aria-label="Recorded terminal output"><code>{`$ ${recording.command}\n\n${output || 'Press Play recording, or read the complete transcript below.'}`}</code></pre>
    <div className="player-controls"><button type="button" className="button button-primary" onClick={toggle}>{playing ? 'Pause recording' : elapsed >= duration ? 'Replay recording' : 'Play recording'}</button><button type="button" className="button button-outline" onClick={() => { setPlaying(false); seek(duration); }}>Show full output</button><label>Recording position<input type="range" min={0} max={duration} step={0.01} value={elapsed} onChange={e => { setPlaying(false); seek(Number(e.target.value)); }} aria-valuetext={`${elapsed.toFixed(1)} of ${duration.toFixed(1)} seconds`} /></label></div>
    <p className="recording-note">Playback preserves the captured timings. Elapsed time is not a performance benchmark. No terminal commands execute in your browser.</p>
  </div>;
}
