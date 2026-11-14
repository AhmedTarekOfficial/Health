// Aerospace Audio & Speech Recognition Session
// Handles Web Speech API with real-time word streaming, audio activity detection, and resilient listening

export function createSpeechSession({
  Recognition,
  language,
  onState,
  onWords,
  onFinal,
  postSpeechSilenceMs = 3500 // 3.5s of silence AFTER speaking begins
}) {
  let enabled = false;
  let engine = null;
  let silenceTimer = null;
  let accumulatedFinal = '';
  let hasSpokenWords = false;
  let audioContext = null;
  let audioStream = null;
  let analyser = null;
  let animFrameId = null;

  function clearSilenceTimer() {
    if (silenceTimer) {
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }
  }

  function armPostSpeechSilenceTimer() {
    clearSilenceTimer();
    // Only arm silence auto-stop AFTER the user has actually started delivering speech!
    if (!hasSpokenWords) return;

    silenceTimer = setTimeout(() => {
      if (enabled && hasSpokenWords) {
        console.log('[VOICE] Silence detected after speech – finalizing.');
        finishSession();
      }
    }, postSpeechSilenceMs);
  }

  function finishSession() {
    clearSilenceTimer();
    const finalTranscript = accumulatedFinal.trim();
    if (finalTranscript && onFinal) {
      onFinal(finalTranscript);
    }
    disable();
  }

  function stopAudioVisualizer() {
    if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }
    if (audioStream) {
      audioStream.getTracks().forEach(track => track.stop());
      audioStream = null;
    }
    if (audioContext && audioContext.state !== 'closed') {
      try {
        audioContext.close();
      } catch {}
      audioContext = null;
    }
    resetVisualizerBars();
  }

  function resetVisualizerBars() {
    const bars = document.querySelectorAll('.voice-equalizer span');
    bars.forEach(b => {
      b.style.transform = 'scaleY(0.3)';
      b.style.background = '#4ade80';
    });
  }

  async function startAudioVisualizer() {
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
      audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      audioContext = new AudioCtx();
      const source = audioContext.createMediaStreamSource(audioStream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      const bars = document.querySelectorAll('.voice-equalizer span');

      function draw() {
        if (!enabled) {
          resetVisualizerBars();
          return;
        }
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        const avg = sum / bufferLength;
        const normalized = Math.min(Math.max((avg - 10) / 45, 0.2), 2.0);

        bars.forEach((b, idx) => {
          const factor = (dataArray[idx % bufferLength] || avg) / 60;
          const scale = Math.min(Math.max(factor * 1.5, 0.3), 2.2);
          b.style.transform = `scaleY(${scale})`;
          if (scale > 0.8) {
            b.style.background = '#22c55e';
          }
        });

        animFrameId = requestAnimationFrame(draw);
      }
      draw();
    } catch (err) {
      console.warn('[VOICE] MediaDevices visualizer notice:', err.message);
    }
  }

  function connect() {
    if (!enabled || engine) return;
    if (!Recognition) {
      enabled = false;
      onState('error', 'Web Speech API is unavailable in this browser. Please type directly into the chat.');
      return;
    }

    accumulatedFinal = '';
    hasSpokenWords = false;
    clearSilenceTimer();

    const current = new Recognition();
    engine = current;

    try {
      current.lang = typeof language === 'function' ? language() : language || 'en-US';
      current.continuous = true;
      current.interimResults = true;
      current.maxAlternatives = 1;
    } catch (e) {
      console.warn('[VOICE] Recognition configuration error:', e);
    }

    onState('connecting', 'Acquiring microphone...');

    current.onstart = () => {
      if (engine === current && enabled) {
        onState('listening', 'Microphone active! Speak your clinical query now...');
        startAudioVisualizer();
      }
    };

    current.onresult = event => {
      if (engine !== current || !enabled) return;

      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const res = event.results[i];
        const text = res[0].transcript;
        if (res.isFinal) {
          accumulatedFinal += ' ' + text;
        } else {
          interim += text;
        }
      }

      const fullLiveText = (accumulatedFinal + ' ' + interim).trim();
      if (fullLiveText) {
        hasSpokenWords = true;
        onWords(fullLiveText);
        // User is actively speaking – arm silence timer for after they pause
        armPostSpeechSilenceTimer();
      }
    };

    current.onerror = event => {
      if (engine !== current) return;
      console.log('[VOICE] Recognition event error:', event.error);

      // 'no-speech' happens if astronaut takes a few seconds before speaking.
      // DO NOT cancel the session on 'no-speech' if the user is still listening!
      if (event.error === 'no-speech') {
        if (enabled && !hasSpokenWords) {
          onState('listening', 'Listening for your voice... (Speak clearly into mic)');
          return;
        }
      }

      if (event.error === 'not-allowed') {
        enabled = false;
        disconnect();
        onState('error', 'Microphone access denied. Please allow microphone permissions in your browser URL bar.');
        return;
      }

      if (event.error === 'network') {
        // Speech service network timeout
        if (accumulatedFinal.trim()) {
          finishSession();
        } else {
          onState('error', 'Voice network service error. Please type or use quick triage prompts.');
          disable();
        }
        return;
      }
    };

    current.onend = () => {
      if (engine !== current) return;
      engine = null;

      // If user enabled the mic and hasn't explicitly stopped, and has not finished words:
      // In Chrome, continuous speech recognition sometimes ends after silence.
      // If user already spoke words and paused, finalize!
      if (enabled) {
        if (hasSpokenWords && accumulatedFinal.trim()) {
          finishSession();
        } else {
          // Restart recognition smoothly so it does NOT shut down in 1 second!
          try {
            if (enabled) {
              setTimeout(() => {
                if (enabled && !engine) {
                  connect();
                }
              }, 250);
            }
          } catch {}
        }
      }
    };

    try {
      current.start();
    } catch (err) {
      console.warn('[VOICE] Start error:', err);
    }
  }

  function disconnect() {
    clearSilenceTimer();
    stopAudioVisualizer();
    const prev = engine;
    engine = null;
    if (prev) {
      try {
        prev.abort();
      } catch {}
    }
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    connect();
  }

  function disable() {
    enabled = false;
    clearSilenceTimer();
    stopAudioVisualizer();
    const prev = engine;
    engine = null;
    if (prev) {
      try {
        prev.stop();
      } catch {}
    }
    onState('off', 'Microphone off. Click to speak again.');
  }

  return {
    get enabled() {
      return enabled;
    },
    enable,
    disable,
    finishSession,
    pause() {
      disable();
    },
    resume() {}
  };
}
