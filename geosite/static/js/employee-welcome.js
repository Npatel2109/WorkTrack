(() => {
  const dialog = document.getElementById("employee-welcome");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const skip = document.getElementById("robot-skip");
  const play = document.getElementById("robot-play-voice");
  const status = document.getElementById("robot-voice-status");
  const key = `worktrack-welcome-${dialog.dataset.employee}`;
  let storageAvailable = true;
  try {
    if (localStorage.getItem(key) === dialog.dataset.day) return;
  } catch (error) {
    storageAvailable = false;
    console.warn("Daily welcome preference could not be read.", error.name);
  }
  const previousFocus = document.activeElement;
  const synth = window.speechSynthesis;
  let utterance = null;
  let closed = false;
  let started = false;
  let timer;
  let voiceTimer;
  const finish = () => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    clearTimeout(voiceTimer);
    if (utterance && synth) synth.cancel();
    if (dialog.open) dialog.close();
    const target = previousFocus && previousFocus !== document.body
      ? previousFocus : document.getElementById("main-content");
    target?.focus({ preventScroll: true });
  };
  const speak = () => {
    if (closed) return;
    if (!synth || !window.SpeechSynthesisUtterance) {
      play.disabled = true;
      status.textContent = "Voice is unavailable on this device. Enjoy your day!";
      return;
    }
    if (utterance) {
      utterance.onstart = null;
      utterance.onend = null;
      utterance.onerror = null;
      synth.cancel();
    }
    clearTimeout(timer);
    timer = setTimeout(finish, 4000);
    utterance = new window.SpeechSynthesisUtterance(
      `${document.getElementById("robot-welcome-title").textContent} ${document.getElementById("robot-welcome-message").textContent}`
    );
    utterance.lang = "en-IN";
    utterance.rate = 1.1;
    started = false;
    utterance.onstart = () => {
      if (closed) return;
      started = true;
      clearTimeout(timer);
      timer = setTimeout(finish, 20000);
      status.textContent = "Your dashboard is ready. Have a great day!";
    };
    utterance.onend = finish;
    utterance.onerror = () => {
      if (closed) return;
      clearTimeout(timer);
      timer = setTimeout(finish, 4000);
      status.textContent = "Voice could not play. Tap Play voice to retry, or continue to your dashboard.";
    };
    try {
      synth.speak(utterance);
      clearTimeout(voiceTimer);
      voiceTimer = setTimeout(() => {
        if (!closed && !started) status.textContent = "Tap Play voice if your browser blocks automatic audio.";
      }, 700);
    } catch (error) {
      status.textContent = "Voice could not play. Tap Play voice to retry.";
      console.warn("Employee welcome voice could not start.", error.name);
    }
  };
  dialog.showModal();
  try {
    if (storageAvailable) localStorage.setItem(key, dialog.dataset.day);
  } catch (error) {
    storageAvailable = false;
    console.warn("Daily welcome preference could not be saved.", error.name);
  }
  skip.focus();
  timer = setTimeout(finish, 4000);
  skip.addEventListener("click", finish);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish();
  });
  dialog.addEventListener("close", finish);
  window.addEventListener("pagehide", finish);
  play.addEventListener("click", speak);
  speak();
  if (!storageAvailable) status.textContent = "Daily greeting preference cannot be saved on this device; this may appear again after login.";
})();
