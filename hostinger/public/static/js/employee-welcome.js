(() => {
  const dialog = document.getElementById("employee-welcome");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const skip = document.getElementById("robot-skip");
  const play = document.getElementById("robot-play-voice");
  const status = document.getElementById("robot-voice-status");
  const previousFocus = document.activeElement;
  const synth = window.speechSynthesis;
  let utterance = null;
  let nativeId = null;
  let requestNumber = 0;
  let closed = false;
  let started = false;
  let timer;
  let voiceTimer;
  const retryMessage = "Voice could not play. Turn up media volume and tap Play voice, or Skip robot.";
  const stopSpeech = () => {
    if (utterance && synth) {
      utterance.onstart = null;
      utterance.onend = null;
      utterance.onerror = null;
      synth.cancel();
      utterance = null;
    }
    if (nativeId !== null) {
      window.location.href = "worktrack-robot://stop";
      nativeId = null;
    }
  };
  const failed = () => {
    if (closed) return;
    clearTimeout(timer);
    clearTimeout(voiceTimer);
    stopSpeech();
    status.textContent = retryMessage;
  };
  const speechStarted = () => {
    if (closed) return;
    started = true;
    clearTimeout(timer);
    clearTimeout(voiceTimer);
    timer = setTimeout(failed, 20000);
    status.textContent = "Robot is speaking. Turn up media volume if you cannot hear it.";
  };
  const finish = () => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    clearTimeout(voiceTimer);
    stopSpeech();
    if (dialog.open) dialog.close();
    const target = previousFocus && previousFocus !== document.body
      ? previousFocus : document.getElementById("main-content");
    target?.focus({ preventScroll: true });
  };
  const speak = () => {
    if (closed) return;
    stopSpeech();
    clearTimeout(timer);
    clearTimeout(voiceTimer);
    started = false;
    const text = `${document.getElementById("robot-welcome-title").textContent} ${document.getElementById("robot-welcome-message").textContent}`;
    status.textContent = "Starting robot voice...";
    timer = setTimeout(failed, 20000);
    voiceTimer = setTimeout(() => {
      if (!closed && !started) status.textContent = "Tap Play voice to allow sound on mobile, or Skip robot.";
    }, 700);
    if (window.worktrackNativeRobot === true) {
      nativeId = String(++requestNumber);
      window.location.href = `worktrack-robot://speak?id=${nativeId}&text=${encodeURIComponent(text)}`;
      return;
    }
    if (!synth || !window.SpeechSynthesisUtterance) {
      clearTimeout(timer);
      clearTimeout(voiceTimer);
      status.textContent = "Voice is unavailable here. Update the Android app or use a supported browser, or Skip robot.";
      return;
    }
    utterance = new window.SpeechSynthesisUtterance(text);
    utterance.lang = "en-IN";
    utterance.rate = 1.1;
    utterance.volume = 1;
    const voices = synth.getVoices();
    utterance.voice = voices.find((voice) => /^en[-_]IN$/i.test(voice.lang))
      || voices.find((voice) => /^en[-_]/i.test(voice.lang)) || null;
    utterance.onstart = speechStarted;
    utterance.onend = finish;
    utterance.onerror = failed;
    try {
      synth.resume();
      synth.speak(utterance);
    } catch (error) {
      failed();
      console.warn("Employee welcome voice could not start.", error.name);
    }
  };
  dialog.showModal();
  play.focus();
  skip.addEventListener("click", finish);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish();
  });
  dialog.addEventListener("close", finish);
  window.addEventListener("pagehide", finish);
  window.addEventListener("worktrack-robot-ready", () => {
    if (!closed) speak();
  });
  window.addEventListener("worktrack-robot-result", (event) => {
    if (closed || nativeId === null || event.detail?.id !== nativeId) return;
    if (event.detail.state === "start") speechStarted();
    else if (event.detail.state === "done") finish();
    else failed();
  });
  play.addEventListener("click", speak);
  speak();
})();
