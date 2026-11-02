function getDefaultCenter() {
    return [6.2115, 1.1829];
}

function createMap(targetId, center = getDefaultCenter(), zoom = 12) {
    const container = document.getElementById(targetId);
    if (!container) {
        console.warn('Carte : conteneur introuvable (' + targetId + ')');
        return null;
    }

    const map = L.map(targetId, { zoomControl: true }).setView(center, zoom);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    // Leaflet mesure son conteneur à la création. Dans une mise en page
    // flex ou collante, cette mesure est parfois faite avant que le
    // navigateur ait fini de calculer la hauteur : la carte reste alors
    // grise ou invisible. On force donc un recalcul une fois la page posée.
    const recalculer = () => map.invalidateSize();
    setTimeout(recalculer, 120);
    setTimeout(recalculer, 600);
    window.addEventListener('resize', recalculer);
    window.addEventListener('load', recalculer);
    if (window.ResizeObserver) new ResizeObserver(recalculer).observe(container);

    return map;
}

function createDivIcon(className, html = '', size = [24, 24], anchor = null) {
    const iconAnchor = anchor || [Math.round(size[0] / 2), Math.round(size[1] / 2)];
    return L.divIcon({
        className: '',
        html: `<div class="${className}">${html}</div>`,
        iconSize: size,
        iconAnchor
    });
}

function createPharmacyIcon(item) {
    const isGuard = Boolean(item.is_on_guard);
    const shape = isGuard
        ? '<div class="pharmacy-marker-star">★</div>'
        : '<div class="pharmacy-marker-circle">+</div>';
    return createDivIcon(`pharmacy-marker ${isGuard ? 'guard' : 'standard'}`, `<div class="pharmacy-marker-halo"></div>${shape}`, [26, 26]);
}

function markerPopup(item) {
    const badge = item.is_on_guard ? '<span class="badge text-bg-success mb-2">De garde</span><br>' : '';
    return `${badge}<strong>${item.name}</strong><br>${item.quartier || 'Agoè-Nyivé 1'}<br>${item.address || 'Adresse à compléter'}${item.id ? `<br><a class="btn btn-sm btn-outline-primary mt-2" href="/pharmacies/${item.id}">Voir la fiche</a>` : ''}`;
}

function formatDistanceKm(km) {
    if (!Number.isFinite(km)) return '—';
    if (km < 1) return `${Math.round(km * 1000)} m`;
    return `${km.toFixed(2)} km`;
}

function formatDurationMin(mins) {
    if (!Number.isFinite(mins)) return '—';
    const rounded = Math.max(1, Math.round(mins));
    if (rounded < 60) return `${rounded} min`;
    const h = Math.floor(rounded / 60);
    const m = rounded % 60;
    return `${h} h ${m} min`;
}

function formatSpeedKmh(mps) {
    if (!Number.isFinite(mps)) return '—';
    return `${(mps * 3.6).toFixed(1)} km/h`;
}

function haversineMeters(a, b) {
    const R = 6371000;
    const toRad = deg => deg * Math.PI / 180;
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const lat1 = toRad(a.lat);
    const lat2 = toRad(b.lat);
    const x = Math.sin(dLat / 2) ** 2 + Math.sin(dLng / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
    return 2 * R * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

function interpolateLatLng(a, b, factor) {
    return {
        lat: a.lat + (b.lat - a.lat) * factor,
        lng: a.lng + (b.lng - a.lng) * factor,
    };
}

function coordinatesToLatLngs(geometryCoords) {
    return geometryCoords.map(([lng, lat]) => ({ lat, lng }));
}

function totalDistanceMeters(points) {
    if (!points || points.length < 2) return 0;
    let total = 0;
    for (let i = 1; i < points.length; i += 1) total += haversineMeters(points[i - 1], points[i]);
    return total;
}

function projectOnSegment(point, a, b) {
    const ax = a.lng;
    const ay = a.lat;
    const bx = b.lng;
    const by = b.lat;
    const px = point.lng;
    const py = point.lat;
    const abx = bx - ax;
    const aby = by - ay;
    const ab2 = abx * abx + aby * aby;
    const t = ab2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * abx + (py - ay) * aby) / ab2));
    return { lat: ay + aby * t, lng: ax + abx * t, t };
}

function computeProgressOnRoute(routePoints, point) {
    if (!routePoints || routePoints.length < 2) {
        return { nearestIndex: 0, nearestPoint: point, distanceMeters: 0, traveledMeters: 0 };
    }
    let best = { nearestIndex: 0, nearestPoint: routePoints[0], distanceMeters: Infinity, traveledMeters: 0 };
    let cumulative = 0;
    for (let i = 0; i < routePoints.length - 1; i += 1) {
        const a = routePoints[i];
        const b = routePoints[i + 1];
        const segmentMeters = haversineMeters(a, b);
        const projected = projectOnSegment(point, a, b);
        const distance = haversineMeters(point, projected);
        const traveledMeters = cumulative + segmentMeters * projected.t;
        if (distance < best.distanceMeters) {
            best = { nearestIndex: i, nearestPoint: projected, distanceMeters: distance, traveledMeters };
        }
        cumulative += segmentMeters;
    }
    return best;
}

function remainingRoutePoints(routePoints, progress) {
    if (!routePoints || !routePoints.length) return [];
    const tail = [progress.nearestPoint];
    for (let i = progress.nearestIndex + 1; i < routePoints.length; i += 1) tail.push(routePoints[i]);
    return tail;
}

function detectHeading(prev, next) {
    if (!prev || !next) return 0;
    const dy = next.lat - prev.lat;
    const dx = next.lng - prev.lng;
    const angle = Math.atan2(dx, dy) * 180 / Math.PI;
    return angle;
}

async function fetchJson(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Erreur serveur');
    return data;
}

async function fetchOsrmRoute(startLat, startLon, endLat, endLon) {
    const url = `https://router.project-osrm.org/route/v1/driving/${startLon},${startLat};${endLon},${endLat}?overview=full&geometries=geojson&steps=true&annotations=distance,duration`;
    const data = await fetchJson(url);
    if (!data.routes || !data.routes.length) throw new Error('Aucun itinéraire trouvé.');
    return data.routes[0];
}

function buildStepList(route) {
    const steps = [];
    (route.legs || []).forEach(leg => {
        (leg.steps || []).forEach(step => {
            const maneuver = step.maneuver || {};
            const action = maneuver.type === 'depart' ? 'Démarrez' : maneuver.type === 'arrive' ? 'Vous arrivez' : 'Continuez';
            const modifier = maneuver.modifier ? ` ${maneuver.modifier}` : '';
            const road = step.name ? ` sur ${step.name}` : '';
            steps.push({
                text: `${action}${modifier}${road}`,
                distanceMeters: step.distance || 0,
                location: maneuver.location ? { lat: maneuver.location[1], lng: maneuver.location[0] } : null,
            });
        });
    });
    return steps;
}

function placePharmacyMarkers(map, data) {
    const markers = [];
    data.forEach(item => {
        if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) return;
        const marker = L.marker([item.latitude, item.longitude], { icon: createPharmacyIcon(item), keyboard: false })
            .addTo(map)
            .bindPopup(markerPopup(item));
        markers.push(marker);
    });
    return markers;
}

function renderOverviewMap() {
    if (!window.MAP_TARGET || !window.PHARMACIES_DATA) return;
    if (window.MAP_MODE === 'navigation') return;
    const data = window.PHARMACIES_DATA;
    if (!Array.isArray(data) || !data.length) return;
    const first = data[0];
    const map = createMap(window.MAP_TARGET, [first.latitude, first.longitude], window.MAP_MODE === 'single' ? 16 : 12);
    if (!map) return;
    const markers = placePharmacyMarkers(map, data);
    if (window.MAP_MODE !== 'single' && markers.length) {
        const featureGroup = L.featureGroup(markers);
        map.fitBounds(featureGroup.getBounds().pad(0.15));
    }
}

function initNavigationPage() {
    if (window.MAP_MODE !== 'navigation') return;

    const data = Array.isArray(window.PHARMACIES_DATA) ? window.PHARMACIES_DATA : [];
    const map = createMap(window.MAP_TARGET, getDefaultCenter(), 13);
    if (!map) return;
    const routeResult = document.getElementById('routeResult');
    const nextInstructionEl = document.getElementById('nextInstruction');
    const remainingDistanceEl = document.getElementById('remainingDistance');
    const remainingDurationEl = document.getElementById('remainingDuration');
    const currentSpeedEl = document.getElementById('currentSpeed');
    const gpsAccuracyEl = document.getElementById('gpsAccuracy');
    const progressValueEl = document.getElementById('progressValue');
    const routeProgressBar = document.getElementById('routeProgressBar');
    const pharmacySelect = document.getElementById('pharmacySelect');
    const selectedWrap = document.getElementById('selectedPharmacyWrap');
    const followUserToggle = document.getElementById('followUserToggle');
    const soundToggle = document.getElementById('soundToggle');
    const startGuidanceBtn = document.getElementById('startGuidanceBtn');
    const stopGuidanceBtn = document.getElementById('stopGuidanceBtn');
    const recenterBtn = document.getElementById('recenterBtn');
    const showAllBtn = document.getElementById('showAllBtn');
    const modeButtons = Array.from(document.querySelectorAll('[data-mode-value]'));

    const state = {
        mode: window.PRESELECTED_ROUTE_MODE || 'guard-nearest',
        watchId: null,
        followUser: true,
        rerouteCooldownMs: 8000,
        lastRerouteAt: 0,
        lastPosition: null,
        currentPosition: null,
        activeDestination: null,
        route: null,
        routePoints: [],
        totalRouteMeters: 0,
        traveledLatLngs: [],
        offRouteAlerted: false,
        isRouting: false,
        userHeading: 0,
        staticMarkers: placePharmacyMarkers(map, data),
        userMarker: null,
        userAccuracyCircle: null,
        destinationMarker: null,
        remainingLineGlow: null,
        remainingLine: null,
        remainingLineDash: null,
        traveledLineGlow: null,
        traveledLine: null,
        runnerMarker: null,
    };

    function setResult(html) {
        routeResult.innerHTML = html;
    }

    function setMode(mode) {
        state.mode = mode;
        modeButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.modeValue === mode));
        selectedWrap.style.display = mode === 'selected' ? 'block' : 'none';
    }

    function fitAllMarkers() {
        if (!state.staticMarkers.length) return;
        const group = L.featureGroup(state.staticMarkers);
        map.fitBounds(group.getBounds().pad(0.15));
    }

    function updateMetricElements(progress, coords) {
        currentSpeedEl.textContent = formatSpeedKmh(coords.speed);
        gpsAccuracyEl.textContent = Number.isFinite(coords.accuracy) ? `${Math.round(coords.accuracy)} m` : '—';
        if (!state.route || !progress) return;
        const remainingMeters = Math.max(0, state.totalRouteMeters - progress.traveledMeters);
        const fraction = state.totalRouteMeters > 0 ? Math.min(1, progress.traveledMeters / state.totalRouteMeters) : 0;
        const remainingMinutes = (state.route.duration / 60) * (1 - fraction);
        remainingDistanceEl.textContent = formatDistanceKm(remainingMeters / 1000);
        remainingDurationEl.textContent = formatDurationMin(remainingMinutes);
        const pct = Math.round(fraction * 100);
        progressValueEl.textContent = `${pct}%`;
        routeProgressBar.style.width = `${pct}%`;
    }

    function updateInstruction(position) {
        if (!state.route || !position) return;
        const steps = state.route.stepList || [];
        let best = steps[0];
        let bestDistance = Infinity;
        steps.forEach(step => {
            if (!step.location) return;
            const distance = haversineMeters(position, step.location);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = step;
            }
        });
        if (best) {
            nextInstructionEl.textContent = `${best.text} · ${formatDistanceKm((best.distanceMeters || bestDistance) / 1000)}`;
        }
    }

    function maybeBeep() {
        if (!soundToggle.checked) return;
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const audioCtx = new AudioCtx();
        const oscillator = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        oscillator.connect(gain);
        gain.connect(audioCtx.destination);
        oscillator.type = 'sine';
        oscillator.frequency.value = 620;
        gain.gain.value = 0.03;
        oscillator.start();
        oscillator.stop(audioCtx.currentTime + 0.15);
    }

    function ensureUserLayers() {
        if (!state.userMarker) {
            state.userMarker = L.marker(getDefaultCenter(), { icon: createDivIcon('user-puck', '<div class="user-puck-glow"></div><div class="user-puck-core"></div><div class="user-puck-arrow"></div>', [28, 28]) }).addTo(map);
        }
        if (!state.userAccuracyCircle) {
            state.userAccuracyCircle = L.circle(getDefaultCenter(), { radius: 0, color: '#2563eb', weight: 1, fillColor: '#60a5fa', fillOpacity: 0.08 }).addTo(map);
        }
        if (!state.traveledLineGlow) {
            state.traveledLineGlow = L.polyline([], { color: '#fb7185', weight: 12, opacity: 0.20, lineCap: 'round', className: 'route-line-glow' }).addTo(map);
        }
        if (!state.traveledLine) {
            state.traveledLine = L.polyline([], { color: '#f59e0b', weight: 6, opacity: 0.92, lineCap: 'round' }).addTo(map);
        }
        if (!state.remainingLineGlow) {
            state.remainingLineGlow = L.polyline([], { color: '#22c55e', weight: 14, opacity: 0.20, lineCap: 'round', className: 'route-line-glow' }).addTo(map);
        }
        if (!state.remainingLine) {
            state.remainingLine = L.polyline([], { color: '#0f766e', weight: 8, opacity: 0.96, lineCap: 'round' }).addTo(map);
        }
        if (!state.remainingLineDash) {
            state.remainingLineDash = L.polyline([], { color: '#ecfeff', weight: 3, opacity: 0.95, lineCap: 'round', dashArray: '12 18', className: 'route-line-dash' }).addTo(map);
        }
        if (!state.runnerMarker) {
            state.runnerMarker = L.marker(getDefaultCenter(), { icon: createDivIcon('route-runner', '', [18, 18]) }).addTo(map);
        }
    }

    function animateMarker(targetLatLng) {
        ensureUserLayers();
        const marker = state.userMarker;
        const start = marker.getLatLng();
        const end = L.latLng(targetLatLng.lat, targetLatLng.lng);
        const startTs = performance.now();
        const duration = 700;
        function frame(now) {
            const t = Math.min(1, (now - startTs) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            const lat = start.lat + (end.lat - start.lat) * eased;
            const lng = start.lng + (end.lng - start.lng) * eased;
            marker.setLatLng([lat, lng]);
            if (t < 1) requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
    }

    function renderDestination(destination) {
        if (state.destinationMarker) map.removeLayer(state.destinationMarker);
        state.destinationMarker = L.marker([destination.latitude, destination.longitude], { icon: createDivIcon('destination-puck', '<div class="destination-ring"></div><div class="destination-core"></div>', [28, 28]) })
            .addTo(map)
            .bindPopup(markerPopup(destination));
    }

    function setRouteLayers(remainingPoints, traveledPoints) {
        ensureUserLayers();
        const safeRemaining = remainingPoints || [];
        const safeTraveled = traveledPoints || [];
        state.remainingLineGlow.setLatLngs(safeRemaining);
        state.remainingLine.setLatLngs(safeRemaining);
        state.remainingLineDash.setLatLngs(safeRemaining);
        state.traveledLineGlow.setLatLngs(safeTraveled);
        state.traveledLine.setLatLngs(safeTraveled);
        const runnerPoint = safeRemaining.length ? safeRemaining[0] : (safeTraveled.length ? safeTraveled[safeTraveled.length - 1] : null);
        if (runnerPoint) state.runnerMarker.setLatLng([runnerPoint.lat, runnerPoint.lng]);
    }

    function renderRouteSummary(destination) {
        const guardLabel = destination.is_on_guard ? 'Pharmacie de garde' : 'Pharmacie standard';
        setResult(`
            <div class="d-flex justify-content-between align-items-start gap-3 flex-wrap mb-3">
                <div>
                    <span class="badge text-bg-success mb-2">Destination active</span>
                    <h3>${destination.name}</h3>
                    <p class="mb-1 text-muted">${destination.address || 'Adresse à compléter'}</p>
                    <p class="mb-0 text-muted">${destination.quartier || 'Quartier non précisé'} · ${guardLabel}</p>
                </div>
                <a class="btn btn-sm btn-outline-primary" href="/pharmacies/${destination.id}">Fiche</a>
            </div>
            <ol class="route-steps" id="stepListPanel"></ol>
        `);
        const stepListPanel = document.getElementById('stepListPanel');
        if (stepListPanel && state.route && state.route.stepList) {
            stepListPanel.innerHTML = state.route.stepList.slice(0, 8).map(step => `<li>${step.text}</li>`).join('');
        }
    }

    async function resolveDestination(position) {
        if (state.mode === 'nearest') {
            return fetchJson(`/api/nearest?lat=${position.lat}&lon=${position.lng}`);
        }
        if (state.mode === 'guard-nearest') {
            return fetchJson(`/api/nearest?lat=${position.lat}&lon=${position.lng}&guard_only=1`);
        }
        const pharmacyId = pharmacySelect.value;
        if (!pharmacyId) throw new Error('Veuillez choisir une pharmacie.');
        return fetchJson(`/api/pharmacy/${pharmacyId}`);
    }

    async function computeRoute(position, destination, force = false) {
        const now = Date.now();
        if (!force && now - state.lastRerouteAt < state.rerouteCooldownMs) return;
        if (state.isRouting) return;
        state.isRouting = true;
        nextInstructionEl.textContent = 'Calcul du meilleur trajet en cours…';
        try {
            const route = await fetchOsrmRoute(position.lat, position.lng, destination.latitude, destination.longitude);
            state.route = route;
            state.route.stepList = buildStepList(route);
            state.routePoints = coordinatesToLatLngs(route.geometry.coordinates);
            state.totalRouteMeters = totalDistanceMeters(state.routePoints);
            state.traveledLatLngs = [{ lat: position.lat, lng: position.lng }];
            renderDestination(destination);
            renderRouteSummary(destination);
            const progress = computeProgressOnRoute(state.routePoints, position);
            setRouteLayers(remainingRoutePoints(state.routePoints, progress), [progress.nearestPoint]);
            state.lastRerouteAt = now;
            state.offRouteAlerted = false;
            updateMetricElements(progress, state.currentPosition || {});
            updateInstruction(position);
            if (state.followUser && followUserToggle.checked) {
                map.flyTo([position.lat, position.lng], 17, { duration: 0.8 });
            } else {
                const bounds = L.latLngBounds(state.remainingLine.getLatLngs());
                bounds.extend([position.lat, position.lng]);
                map.fitBounds(bounds.pad(0.12));
            }
        } finally {
            state.isRouting = false;
        }
    }

    async function updateDestinationAndRoute(position, force = false) {
        const destination = await resolveDestination(position);
        const destinationChanged = !state.activeDestination || state.activeDestination.id !== destination.id;
        state.activeDestination = destination;
        await computeRoute(position, destination, force || destinationChanged);
    }

    function updateRouteProgress(position) {
        if (!state.routePoints.length) return;
        const progress = computeProgressOnRoute(state.routePoints, position);
        const remainingPoints = remainingRoutePoints(state.routePoints, progress);
        const trail = state.traveledLatLngs.concat([progress.nearestPoint]);
        setRouteLayers(remainingPoints, trail);
        updateMetricElements(progress, state.currentPosition || {});
        updateInstruction(position);
        if (progress.distanceMeters > 45 && !state.offRouteAlerted) {
            nextInstructionEl.textContent = 'Vous vous éloignez du trajet. Recalcul en cours…';
            maybeBeep();
            state.offRouteAlerted = true;
        }
        if (progress.distanceMeters > 45 || Date.now() - state.lastRerouteAt > 15000) {
            updateDestinationAndRoute(position).catch(err => setResult(`<p class="text-danger mb-0">${err.message}</p>`));
        }
        if (state.followUser && followUserToggle.checked) {
            map.panTo([position.lat, position.lng], { animate: true, duration: 0.5 });
        }
    }

    function onPositionUpdate(coords) {
        const position = { lat: coords.latitude, lng: coords.longitude };
        const speed = Number.isFinite(coords.speed) ? coords.speed : state.lastPosition ? haversineMeters(state.lastPosition, position) / Math.max(1, (Date.now() - state.lastPosition.ts) / 1000) : NaN;
        state.currentPosition = { ...position, accuracy: coords.accuracy, speed };
        ensureUserLayers();
        animateMarker(position);
        state.userAccuracyCircle.setLatLng([position.lat, position.lng]);
        state.userAccuracyCircle.setRadius(Number.isFinite(coords.accuracy) ? coords.accuracy : 0);
        if (state.lastPosition) {
            state.userHeading = detectHeading(state.lastPosition, position);
            const puck = state.userMarker.getElement();
            puck?.querySelector('.user-puck-arrow')?.style.setProperty('transform', `translate(-50%, -120%) rotate(${state.userHeading}deg)`);
        }
        state.traveledLatLngs.push(position);
        state.lastPosition = { ...position, ts: Date.now() };
        if (!state.route) {
            updateDestinationAndRoute(position, true).catch(err => setResult(`<p class="text-danger mb-0">${err.message}</p>`));
        } else {
            updateRouteProgress(position);
        }
    }

    function startGuidance() {
        if (!navigator.geolocation) {
            setResult('<p class="text-danger mb-0">La géolocalisation n’est pas disponible sur ce navigateur.</p>');
            return;
        }
        stopGuidance(false);
        setResult('<p class="text-muted mb-0">Acquisition GPS en cours… Le guidage live va démarrer dès la première position.</p>');
        state.watchId = navigator.geolocation.watchPosition(
            pos => onPositionUpdate(pos.coords),
            err => setResult(`<p class="text-danger mb-0">Impossible d’obtenir votre position : ${err.message}</p>`),
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 1000 }
        );
        nextInstructionEl.textContent = 'Connexion au GPS en cours…';
    }

    function stopGuidance(resetPanel = true) {
        if (state.watchId !== null) {
            navigator.geolocation.clearWatch(state.watchId);
            state.watchId = null;
        }
        state.route = null;
        state.routePoints = [];
        state.totalRouteMeters = 0;
        state.lastRerouteAt = 0;
        if (state.remainingLineGlow) state.remainingLineGlow.setLatLngs([]);
        if (state.remainingLine) state.remainingLine.setLatLngs([]);
        if (state.remainingLineDash) state.remainingLineDash.setLatLngs([]);
        if (state.traveledLineGlow) state.traveledLineGlow.setLatLngs([]);
        if (state.traveledLine) state.traveledLine.setLatLngs([]);
        if (state.runnerMarker) state.runnerMarker.setLatLng(getDefaultCenter());
        if (resetPanel) {
            nextInstructionEl.textContent = 'Guidage arrêté.';
            remainingDistanceEl.textContent = '—';
            remainingDurationEl.textContent = '—';
            currentSpeedEl.textContent = '—';
            gpsAccuracyEl.textContent = '—';
            progressValueEl.textContent = '0%';
            routeProgressBar.style.width = '0%';
        }
    }

    setMode(state.mode);
    fitAllMarkers();
    followUserToggle.addEventListener('change', () => { state.followUser = followUserToggle.checked; });
    modeButtons.forEach(btn => btn.addEventListener('click', () => setMode(btn.dataset.modeValue)));
    startGuidanceBtn.addEventListener('click', startGuidance);
    stopGuidanceBtn.addEventListener('click', () => stopGuidance(true));
    recenterBtn.addEventListener('click', () => {
        if (state.currentPosition) map.flyTo([state.currentPosition.lat, state.currentPosition.lng], 17, { duration: 0.8 });
    });
    showAllBtn.addEventListener('click', fitAllMarkers);
    if (window.PRESELECTED_PHARMACY_ID) pharmacySelect.value = String(window.PRESELECTED_PHARMACY_ID);
}

document.addEventListener('DOMContentLoaded', () => {
    try { renderOverviewMap(); } catch (e) { console.error('Carte d\'aperçu :', e); }
    try { initNavigationPage(); } catch (e) { console.error('Carte de navigation :', e); }
});
