(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const verifyButton = document.getElementById("verify-location");
  let nextUrl = null;

  verifyButton?.addEventListener("click", () => {
    if (nextUrl) {
      window.location.assign(nextUrl);
      return;
    }
    const feedback = document.getElementById("gps-status");
    verifyButton.disabled = true;
    verifyButton.textContent = "Requesting GPS...";
    feedback.className = "gps-feedback mt-4";
    feedback.textContent = "Waiting for a precise location fix.";
    if (!navigator.geolocation) {
      feedback.classList.add("error");
      feedback.textContent = "This browser does not support location services.";
      verifyButton.disabled = false;
      verifyButton.textContent = "Try again";
      return;
    }
    navigator.geolocation.getCurrentPosition(async (position) => {
      try {
        const response = await fetch(verifyButton.dataset.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Location verification failed.");
        if (!result.verified) {
          feedback.className = "gps-feedback mt-4 error";
          feedback.textContent = `Not at the assigned site. Distance ${Number(result.distance).toFixed(1)} m; allowed ${Number(result.allowed).toFixed(0)} m.`;
          verifyButton.disabled = false;
          verifyButton.textContent = "Check location again";
          return;
        }
        feedback.className = "gps-feedback mt-4 success";
        feedback.textContent = `Location verified. Distance ${Number(result.distance).toFixed(1)} m.`;
        nextUrl = result.next;
        verifyButton.disabled = false;
        verifyButton.textContent = "Continue";
      } catch (error) {
        feedback.className = "gps-feedback mt-4 error";
        feedback.textContent = error.message;
        verifyButton.disabled = false;
        verifyButton.textContent = "Try again";
      }
    }, (error) => {
      feedback.className = "gps-feedback mt-4 error";
      feedback.textContent = error.code === 1
        ? "Location permission is required. Enable it in browser settings."
        : "Could not determine your location. Move to an open area and retry.";
      verifyButton.disabled = false;
      verifyButton.textContent = "Try again";
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  });

  const video = document.getElementById("camera-video");
  if (video) {
    const start = document.getElementById("start-camera");
    const capture = document.getElementById("capture-photo");
    const retake = document.getElementById("retake-photo");
    const canvas = document.getElementById("camera-canvas");
    const preview = document.getElementById("captured-preview");
    const placeholder = document.getElementById("camera-placeholder");
    const errorBox = document.getElementById("camera-error");
    const submit = document.getElementById("submit-checkin");
    let stream;
    start.addEventListener("click", async () => {
      errorBox.classList.add("d-none");
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } },
        });
        video.srcObject = stream;
        await video.play();
        placeholder.classList.add("d-none");
        capture.disabled = false;
        start.classList.add("d-none");
      } catch (error) {
        errorBox.textContent = error.name === "NotAllowedError"
          ? "Allow camera access in your browser settings."
          : "Could not open the camera. Use a secure browser connection.";
        errorBox.classList.remove("d-none");
      }
    });
    capture.addEventListener("click", () => {
      if (!video.videoWidth) return;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      const data = canvas.toDataURL("image/jpeg", 0.88);
      document.getElementById("selfie-data").value = data;
      preview.src = data;
      preview.classList.remove("d-none");
      video.classList.add("d-none");
      capture.classList.add("d-none");
      retake.classList.remove("d-none");
      submit.disabled = false;
      stream?.getTracks().forEach((track) => track.stop());
    });
    retake.addEventListener("click", () => {
      preview.classList.add("d-none");
      video.classList.remove("d-none");
      document.getElementById("selfie-data").value = "";
      submit.disabled = true;
      capture.classList.remove("d-none");
      retake.classList.add("d-none");
      start.classList.remove("d-none");
    });
    window.addEventListener("pagehide", () => stream?.getTracks().forEach((track) => track.stop()));
    document.getElementById("checkin-form").addEventListener("submit", (event) => {
      if (!document.getElementById("selfie-data").value) {
        event.preventDefault();
        errorBox.textContent = "Capture a live selfie before checking in.";
        errorBox.classList.remove("d-none");
      }
    });
  }

  const pads = document.querySelectorAll(".signature-pad");
  if (pads.length) {
    const signatures = new Map();
    pads.forEach((element) => {
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      const bounds = element.getBoundingClientRect();
      element.width = bounds.width * ratio;
      element.height = bounds.height * ratio;
      const context = element.getContext("2d");
      context.scale(ratio, ratio);
      context.lineWidth = 2.3;
      context.lineCap = "round";
      context.lineJoin = "round";
      context.strokeStyle = "#17243a";
      const state = { context, drawing: false, drawn: false, input: element.dataset.input };
      signatures.set(element, state);
      const point = (event) => {
        const rect = element.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
      };
      element.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        element.setPointerCapture(event.pointerId);
        const p = point(event);
        context.beginPath();
        context.moveTo(p.x, p.y);
        state.drawing = true;
        state.drawn = true;
      });
      element.addEventListener("pointermove", (event) => {
        if (!state.drawing) return;
        const p = point(event);
        context.lineTo(p.x, p.y);
        context.stroke();
      });
      const stop = () => { state.drawing = false; };
      element.addEventListener("pointerup", stop);
      element.addEventListener("pointercancel", stop);
      element.closest(".col-md-6").querySelector(".clear-signature").addEventListener("click", () => {
        context.clearRect(0, 0, bounds.width, bounds.height);
        state.drawn = false;
        document.getElementById(state.input).value = "";
      });
    });
    document.getElementById("site-report-form").addEventListener("submit", (event) => {
      if ([...signatures.values()].some((state) => !state.drawn)) {
        event.preventDefault();
        document.getElementById("signature-error").textContent = "Both signatures are required.";
        return;
      }
      signatures.forEach((state, element) => {
        document.getElementById(state.input).value = element.toDataURL("image/png");
      });
      const button = document.getElementById("complete-visit");
      button.disabled = true;
      button.textContent = "Submitting report...";
    });
  }
})();
