(() => {
  const status = document.getElementById("visit-location-status");
  if (!status) return;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  let busy = false;
  let stopped = false;
  const showOutsideSiteWarning = (distance, radius) => {
    const message = `You are outside the assigned site${distance != null ? ` by approximately ${Math.round(distance)} metres` : ""}. Reference radius: ${Math.round(radius || 0)} metres.`;
    status.textContent = `WARNING: ${message}`;
    status.className = "alert alert-danger small";
  };
  const capture = () => {
    if (busy || stopped || document.hidden) return;
    if (!window.isSecureContext || !navigator.geolocation) {
      status.textContent = "Live location unavailable. The last captured location will be used if the visit is auto-stopped.";
      return;
    }
    busy = true;
    navigator.geolocation.getCurrentPosition(async (position) => {
      try {
        const response = await fetch(status.dataset.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
        });
        const result = await response.json();
        if (response.status === 409 && result.active === false) {
          stopped = true;
          status.textContent = "This visit has stopped. Refresh your dashboard to complete any pending report.";
          return;
        }
        if (!response.ok) throw new Error(result.error || "Location could not be saved.");
        if (result.within_radius === false) {
          showOutsideSiteWarning(result.distance, result.radius_m);
        } else {
          status.textContent = `Last location saved at ${new Date().toLocaleTimeString()}. Updates run every minute while this page is visible.`;
          status.className = "alert alert-info small";
        }
      } catch (error) {
        status.textContent = `Location update failed: ${error.message} The previous captured location is retained.`;
      } finally {
        busy = false;
      }
    }, () => {
      busy = false;
      status.textContent = "Current GPS unavailable or permission off. The last captured location is retained for checkout.";
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  };
  capture();
  const timer = window.setInterval(capture, 60000);
  document.addEventListener("visibilitychange", capture);
  window.addEventListener("pagehide", () => window.clearInterval(timer));
})();
