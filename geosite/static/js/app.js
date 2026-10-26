(() => {
  const themeButton = document.getElementById("theme-toggle");
  if (localStorage.getItem("geosite-theme") === "dark") {
    document.documentElement.dataset.theme = "dark";
  }
  themeButton?.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("geosite-theme", dark ? "dark" : "light");
  });

  window.GeoSite = {
    renderDashboardChart() {
      const canvas = document.getElementById("completion-chart");
      if (!canvas || !window.Chart) return;
      const labels = JSON.parse(document.getElementById("chart-labels").textContent);
      const values = JSON.parse(document.getElementById("chart-values").textContent);
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: labels.map((value) =>
            new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" })
          ),
          datasets: [{
            label: "Completed visits",
            data: values,
            backgroundColor: "#3868ee",
            borderRadius: 7,
            maxBarThickness: 32,
          }],
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      });
    },
    renderSiteMap() {
      const element = document.getElementById("live-map");
      if (!element || !window.L) return;
      const latitudes = JSON.parse(document.getElementById("map-sites").textContent);
      const longitudes = JSON.parse(document.getElementById("map-data").textContent);
      const labels = JSON.parse(document.getElementById("map-labels").textContent);
      const map = L.map(element);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);
      if (!latitudes.length) {
        map.setView([20, 0], 2);
        return;
      }
      const points = latitudes.map((lat, index) => {
        const point = [lat, longitudes[index]];
        L.marker(point).addTo(map).bindPopup(labels[index]);
        return point;
      });
      if (points.length === 1) map.setView(points[0], 13);
      else map.fitBounds(points, { padding: [24, 24] });
    },
    initSiteMapPicker() {
      const element = document.getElementById("site-location-map");
      if (!element || !window.L) return;
      const latitude = document.getElementById("site-latitude");
      const longitude = document.getElementById("site-longitude");
      const address = document.getElementById("site-address");
      const status = document.getElementById("site-map-status");
      const searchButton = document.getElementById("find-site-address");
      const map = L.map(element).setView([20, 0], 2);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);
      let marker;
      const placePin = (lat, lon) => {
        const point = [Number(lat), Number(lon)];
        latitude.value = point[0].toFixed(7);
        longitude.value = point[1].toFixed(7);
        if (!marker) {
          marker = L.marker(point, { draggable: true }).addTo(map);
          marker.on("dragend", () => {
            const position = marker.getLatLng();
            latitude.value = position.lat.toFixed(7);
            longitude.value = position.lng.toFixed(7);
            status.textContent = `Pin: ${latitude.value}, ${longitude.value}`;
          });
        } else {
          marker.setLatLng(point);
        }
        map.setView(point, 16);
        status.textContent = `Pin: ${latitude.value}, ${longitude.value}`;
      };
      map.on("click", (event) => placePin(event.latlng.lat, event.latlng.lng));
      const updateMarker = () => {
        if (!latitude.value || !longitude.value) return;
        const lat = Number(latitude.value);
        const lon = Number(longitude.value);
        if (Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
          placePin(lat, lon);
        }
      };
      latitude.addEventListener("change", updateMarker);
      longitude.addEventListener("change", updateMarker);
      searchButton.addEventListener("click", async () => {
        const query = address.value.trim();
        if (!query) {
          status.textContent = "Enter an address or click the map to place a pin.";
          address.focus();
          return;
        }
        searchButton.disabled = true;
        status.textContent = "Looking up the address…";
        try {
          const params = new URLSearchParams({ q: query, format: "jsonv2", limit: "1" });
          const response = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, {
            headers: { Accept: "application/json" },
          });
          if (!response.ok) throw new Error(`Address lookup returned ${response.status}.`);
          const results = await response.json();
          if (!results.length) {
            status.textContent = "Address not found. Click the map to place a pin manually.";
            return;
          }
          placePin(results[0].lat, results[0].lon);
          status.textContent = `Address located. Confirm the pin: ${latitude.value}, ${longitude.value}`;
        } catch (error) {
          status.textContent = `${error.message} You can still place a pin on the map.`;
        } finally {
          searchButton.disabled = false;
        }
      });
      window.setTimeout(() => map.invalidateSize(), 0);
    },
    startLiveStatusUpdates() {
      const body = document.getElementById("live-status-body");
      if (!body) return;
      const refresh = async () => {
        try {
          const response = await fetch("/api/admin/live-status", {
            headers: { Accept: "application/json" },
            cache: "no-store",
          });
          if (!response.ok) throw new Error(`Live status returned ${response.status}.`);
          const data = await response.json();
          const fragment = document.createDocumentFragment();
          for (const employee of data.employees) {
            const row = document.createElement("tr");
            const employeeCell = document.createElement("td");
            const name = document.createElement("div");
            name.className = "fw-semibold";
            name.textContent = employee.name;
            const designation = document.createElement("div");
            designation.className = "small text-secondary";
            designation.textContent = employee.designation;
            employeeCell.append(name, designation);
            const siteCell = document.createElement("td");
            siteCell.textContent = employee.site || "No site assigned";
            if (employee.customer) {
              const customer = document.createElement("div");
              customer.className = "small text-secondary";
              customer.textContent = employee.customer;
              siteCell.append(customer);
            }
            const time = document.createElement("td");
            time.textContent = employee.checkin_time || "—";
            const state = document.createElement("td");
            const pill = document.createElement("span");
            pill.className = `status-pill ${employee.status}`;
            const label = employee.status.replaceAll("-", " ");
            pill.textContent = label[0].toUpperCase() + label.slice(1);
            state.append(pill);
            row.append(employeeCell, siteCell, time, state);
            fragment.append(row);
          }
          if (!data.employees.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 4;
            cell.className = "empty-state";
            cell.textContent = "No employees have been added yet.";
            row.append(cell);
            fragment.append(row);
          }
          body.replaceChildren(fragment);
          const onsite = document.getElementById("onsite-stat");
          if (onsite) onsite.textContent = data.onsite_count;
          const pendingReports = document.getElementById("pending-report-stat");
          if (pendingReports) pendingReports.textContent = data.pending_report_count;
          const pendingLeave = document.getElementById("pending-leave-stat");
          if (pendingLeave) pendingLeave.textContent = data.pending_leave_count;
          const onLeave = document.getElementById("on-leave-stat");
          if (onLeave) onLeave.textContent = data.on_leave_count;
        } catch (error) {
          console.error("Unable to refresh employee status.", error);
        }
      };
      window.setInterval(refresh, 15000);
    },
  };
})();
