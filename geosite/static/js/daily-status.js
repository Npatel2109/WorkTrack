(function () {
  const form = document.getElementById("daily-status-form");
  if (!form) return;
  const status = document.getElementById("work-type");
  const site = document.getElementById("daily-site");
  const submit = document.getElementById("daily-submit");
  function fields(id, visible) {
    const section = document.getElementById(id);
    section.hidden = !visible;
    for (const control of section.querySelectorAll("input, select, textarea, button")) {
      control.disabled = !visible;
    }
  }
  function update() {
    const onsite = status.value === "onsite";
    const comment = ["wfh", "leave", "training", "travelling"].includes(status.value);
    fields("daily-site-fields", onsite);
    fields("daily-leave-fields", status.value === "leave");
    fields("daily-comment-fields", comment);
    fields("daily-submit-fields", onsite ? Boolean(site.value) : comment);
    submit.textContent = onsite ? "Start Site" : "Submit for approval";
  }
  status.addEventListener("change", update);
  site.addEventListener("change", update);
  update();
})();
