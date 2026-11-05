(function () {
    'use strict';

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        return node;
    }

    function setupMap() {
        const container = document.getElementById('admin-site-map');
        const message = document.getElementById('admin-map-status');
        if (!container || !message) return;
        try {
            if (!window.L) throw new Error('Map library unavailable');
            const sites = JSON.parse(document.getElementById('admin-map-sites').textContent);
            const map = window.L.map(container).setView([20.5937, 78.9629], 5);
            const tiles = window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(map);
            tiles.on('tileerror', function () {
                message.textContent = 'Map tiles could not load. Active site locations remain available in the text list below.';
                message.classList.add('text-danger');
            });
            const bounds = [];
            sites.forEach(function (site) {
                const latitude = Number(site.latitude);
                const longitude = Number(site.longitude);
                if (!Number.isFinite(latitude) || !Number.isFinite(longitude) ||
                    latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return;
                const popup = element('div');
                popup.appendChild(element('strong', site.site_name));
                popup.appendChild(element('div', site.customer_name));
                popup.appendChild(element('div', site.address));
                popup.appendChild(element('div', 'Allowed radius: ' + site.allowed_radius + ' m'));
                window.L.marker([latitude, longitude], { title: String(site.site_name), alt: String(site.site_name) })
                    .addTo(map).bindPopup(popup);
                const radius = Number(site.allowed_radius);
                if (Number.isFinite(radius) && radius > 0) {
                    window.L.circle([latitude, longitude], { radius: radius }).addTo(map);
                }
                bounds.push([latitude, longitude]);
            });
            if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
            message.textContent = bounds.length ? bounds.length + ' active site(s). Select a marker for details.' : 'No active sites.';
        } catch (error) {
            message.textContent = 'Map unavailable. Active site locations remain available in the text list below.';
            message.classList.add('text-danger');
        }
    }

    function setupPolling() {
        const panel = document.getElementById('admin-live-panel');
        const body = document.getElementById('admin-live-rows');
        const message = document.getElementById('admin-live-status');
        if (!panel || !body || !message) return;
        let timer = null;
        let pending = false;

        function rows(payload) {
            if (!payload || !Array.isArray(payload.employees) ||
                !['onsite_count', 'pending_leave_count', 'pending_work_count'].every(function (key) {
                    return Number.isInteger(payload[key]) && payload[key] >= 0;
                })) throw new Error('Invalid live status');
            const fragment = document.createDocumentFragment();
            payload.employees.forEach(function (employee) {
                if (!employee || typeof employee.name !== 'string' || typeof employee.status !== 'string' ||
                    !Array.isArray(employee.daily_statuses)) throw new Error('Invalid employee status');
                const row = element('tr');
                const name = element('td', employee.name);
                name.appendChild(element('br'));
                name.appendChild(element('small', (employee.employee_code || '') + (employee.is_active === false ? ' · Disabled' : '')));
                const status = element('td', employee.status);
                status.appendChild(element('br'));
                status.appendChild(element('small', employee.onsite_hours || '—'));
                const details = element('td', employee.site || '—');
                employee.daily_statuses.forEach(function (daily) {
                    if (!daily || typeof daily.label !== 'string' || typeof daily.status !== 'string' ||
                        typeof daily.detail !== 'string') throw new Error('Invalid daily status');
                    const item = element('div');
                    item.appendChild(element('strong', daily.label));
                    item.appendChild(document.createTextNode(' · ' + daily.status));
                    item.appendChild(element('br'));
                    item.appendChild(element('small', daily.detail));
                    details.appendChild(item);
                });
                row.append(name, status, details);
                fragment.appendChild(row);
            });
            if (!payload.employees.length) {
                const row = element('tr');
                const cell = element('td', 'No employees.');
                cell.colSpan = 3;
                row.appendChild(cell);
                fragment.appendChild(row);
            }
            return fragment;
        }

        function schedule() {
            clearTimeout(timer);
            if (!document.hidden) timer = setTimeout(refresh, 30000);
        }

        async function refresh() {
            if (document.hidden || pending) return;
            pending = true;
            const controller = new AbortController();
            const deadline = setTimeout(function () { controller.abort(); }, 15000);
            try {
                const response = await fetch(panel.dataset.liveUrl, {
                    credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                    headers: { Accept: 'application/json' }
                });
                if (!response.ok || response.redirected) throw new Error('Live status request failed');
                const payload = await response.json();
                const fragment = rows(payload);
                body.replaceChildren(fragment);
                document.querySelectorAll('[data-live-count]').forEach(function (node) {
                    const value = payload[node.dataset.liveCount];
                    if (Number.isInteger(value)) node.textContent = String(value);
                });
                message.textContent = 'Last updated ' + new Date().toLocaleTimeString('en-IN', {
                    timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit'
                }) + ' IST. Updates every 30 seconds.';
                message.classList.remove('text-danger');
            } catch (error) {
                message.textContent = 'Live update failed. Displayed values have been retained and may be out of date. Retrying while this page is visible; check your connection or sign in again.';
                message.classList.add('text-danger');
            } finally {
                clearTimeout(deadline);
                pending = false;
                schedule();
            }
        }

        document.addEventListener('visibilitychange', function () {
            clearTimeout(timer);
            if (!document.hidden) refresh();
        });
        schedule();
    }

    function initialize() {
        setupMap();
        setupPolling();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
    else initialize();
}());
