/**
 * potentiostat-core.js
 *
 * Pure JS utilities shared between index.html (Flask/WebSocket, Phase 8)
 * and webserial.html (Web Serial, Phase 9).
 *
 * Zero DOM, Plotly, or Socket.IO dependencies — include in both pages via
 *   <script src="/static/potentiostat-core.js"></script>
 *
 * Exports (globals):
 *   SPECIES, PRESETS
 *   detectPeaksSimple(voltages, currents, ...)
 *   annotateSpecies(peaks)
 *   parseImportCSV(text)
 *   buildExportCSV(scanData)
 *   buildLinearityCSV(linData)
 *   buildStepCSV(stepData)
 *   downloadBlob(content, filename, [mime])
 */

'use strict';

// ---------------------------------------------------------------------------
// Species reference table
// ---------------------------------------------------------------------------

/**
 * Known electroactive species: peak potentials vs. Ag/AgCl in HCl-KCl pH 2.
 * These are HINTS — potentials shift with reference electrode and pH.
 * Used for labelling peaks, not definitive identification.
 * Tolerance: peak must be within ±tol V of v to be labelled.
 */
const SPECIES = [
  { name: 'Cd\u00b2\u207a', v: -0.80, tol: 0.10, color: '#818cf8' },
  { name: 'Pb\u00b2\u207a', v: -0.40, tol: 0.10, color: '#fb923c' },
  { name: 'Cu\u00b2\u207a', v:  0.00, tol: 0.10, color: '#34d399' },
  { name: 'Hg\u00b2\u207a', v:  0.35, tol: 0.10, color: '#f87171' },
];

// ---------------------------------------------------------------------------
// Scan parameter presets
// ---------------------------------------------------------------------------

/**
 * Preset configurations — pre-fill form fields only (do NOT load/replay CSV data).
 * See Phase 10 design decisions: "presets = OPTION 1 ONLY (param prefill, no CSV replay)".
 */
const PRESETS = {
  cv_ferricyanide: {
    label: 'Ferricyanide CV',
    mode: 'cv',
    cv: { vstart: '-1.0', vstop: '1.0', cycles: '1', rate: '30' },
  },
  dpv_ferricyanide: {
    label: 'Ferricyanide DPV',
    mode: 'dpv',
    dpv: {
      vstart: '-1.0', vstop: '1.0', veq: '-1.0', teq: '5',
      stepe: '15', pulse: '90', period: '100', width: '25',
    },
  },
  dpv_heavy_metals: {
    label: 'Heavy Metals DPV (standard)',
    mode: 'dpv',
    dpv: {
      vstart: '-1.0', vstop: '0.5', veq: '-1.0', teq: '5',
      stepe: '15', pulse: '90', period: '100', width: '25',
    },
  },
};

// ---------------------------------------------------------------------------
// Peak detection (JS, no scipy)
// ---------------------------------------------------------------------------

/**
 * Simple local-maximum peak finder used in the Web Serial path (Phase 9) and
 * as a client-side fallback. The Flask path uses scipy.signal.find_peaks via
 * the 'peaks_detected' event instead.
 *
 * Algorithm: find local maxima → filter by minimum prominence and minimum
 * inter-peak distance (in index units).
 *
 * @param {number[]} voltages
 * @param {number[]} currents        µA values
 * @param {number}   minProminence   µA minimum prominence (default 0.05)
 * @param {number}   minDistPts      minimum index gap between peaks (default 8)
 * @returns {{ voltage: number, current: number }[]}
 */
function detectPeaksSimple(voltages, currents, minProminence = 0.05, minDistPts = 8) {
  if (voltages.length < 3) return [];
  const n = currents.length;

  // 1) Find all local maxima
  const candidates = [];
  for (let i = 1; i < n - 1; i++) {
    if (currents[i] > currents[i - 1] && currents[i] >= currents[i + 1]) {
      candidates.push(i);
    }
  }

  // 2) Filter by prominence (height above lowest surrounding valley)
  const accepted = [];
  for (const idx of candidates) {
    let leftMin = currents[idx];
    for (let j = idx - 1; j >= 0; j--) leftMin = Math.min(leftMin, currents[j]);
    let rightMin = currents[idx];
    for (let j = idx + 1; j < n; j++) rightMin = Math.min(rightMin, currents[j]);
    const prominence = currents[idx] - Math.max(leftMin, rightMin);
    if (prominence < minProminence) continue;
    // 3) Enforce minimum inter-peak distance
    if (accepted.some(p => Math.abs(p._i - idx) < minDistPts)) continue;
    accepted.push({ voltage: voltages[idx], current: currents[idx], _i: idx });
  }

  return accepted.map(({ voltage, current }) => ({ voltage, current }));
}

// ---------------------------------------------------------------------------
// Species annotation
// ---------------------------------------------------------------------------

/**
 * Label each peak with the nearest known species within its tolerance window.
 * Always returns a hint label — never a definitive identification.
 *
 * @param {{ voltage: number, current: number }[]} peaks
 * @returns {{ voltage: number, current: number, species: string|null, color: string }[]}
 */
function annotateSpecies(peaks) {
  return peaks.map(pk => {
    const match = SPECIES.find(s => Math.abs(pk.voltage - s.v) <= s.tol);
    return {
      voltage: pk.voltage,
      current: pk.current,
      species: match ? match.name : null,
      color:   match ? match.color : '#94a3b8',
    };
  });
}

// ---------------------------------------------------------------------------
// CSV import (tolerant parser for commercial instrument exports)
// ---------------------------------------------------------------------------

/**
 * Parse a CSV file exported by a commercial potentiostat.
 * Handles comma, semicolon, and tab delimiters. Skips header/unit rows
 * (any row where the first two columns are not both parseable as numbers).
 * Only the first two numeric columns are used: voltage (col 0), current (col 1).
 *
 * @param {string} text   Raw file content (UTF-8)
 * @returns {{ voltages: number[], currents: number[] } | null}
 *          null if no numeric data rows were found
 */
function parseImportCSV(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim() !== '');
  if (lines.length === 0) return null;

  // Sniff delimiter: pick whichever produces the most splits on the first few lines
  const DELIMITERS = [',', ';', '\t'];
  let delim = ',';
  let bestScore = 0;
  for (const d of DELIMITERS) {
    const score = lines.slice(0, Math.min(5, lines.length))
      .reduce((s, l) => s + (l.split(d).length - 1), 0);
    if (score > bestScore) { bestScore = score; delim = d; }
  }

  const voltages = [], currents = [];
  for (const line of lines) {
    const cols = line.split(delim).map(c => c.trim().replace(/["']/g, ''));
    if (cols.length < 2) continue;
    const v = parseFloat(cols[0]);
    const i = parseFloat(cols[1]);
    if (isNaN(v) || isNaN(i)) continue;   // skip header / unit rows
    voltages.push(v);
    currents.push(i);
  }

  return voltages.length > 0 ? { voltages, currents } : null;
}

// ---------------------------------------------------------------------------
// CSV export formatters
// ---------------------------------------------------------------------------

/**
 * Build CSV string for voltammogram export.
 * Three columns: voltage_V, current_uA, re_voltage_V.
 * RE column is included so captures are fully reanalyzable offline.
 * RE is empty string when re === null (legacy 2-column firmware output).
 *
 * @param {{ voltage: number, current: number, re: number|null }[]} scanData
 * @returns {string}
 */
function buildExportCSV(scanData) {
  const rows = ['voltage_V,current_uA,re_voltage_V'];
  for (const pt of scanData) {
    const re = pt.re != null ? pt.re.toFixed(5) : '';
    rows.push(`${pt.voltage.toFixed(5)},${pt.current.toFixed(5)},${re}`);
  }
  return rows.join('\r\n');
}

/**
 * Build CSV for DAC linearity sweep (L command output).
 * Two columns: dac_count (0–1023), measured_voltage_V.
 *
 * @param {{ dac: number, voltage: number }[]} linData
 * @returns {string}
 */
function buildLinearityCSV(linData) {
  const rows = ['dac_count,measured_voltage_V'];
  for (const pt of linData) {
    rows.push(`${pt.dac},${pt.voltage.toFixed(5)}`);
  }
  return rows.join('\r\n');
}

/**
 * Build CSV for step-response data (T command output).
 * Two columns: elapsed_us, current_uA.
 *
 * @param {{ elapsed_us: number, current: number }[]} stepData
 * @returns {string}
 */
function buildStepCSV(stepData) {
  const rows = ['elapsed_us,current_uA'];
  for (const pt of stepData) {
    rows.push(`${pt.elapsed_us},${pt.current.toFixed(5)}`);
  }
  return rows.join('\r\n');
}

// ---------------------------------------------------------------------------
// Download helper
// ---------------------------------------------------------------------------

/**
 * Trigger a browser file download from a text string.
 *
 * @param {string} content   File content
 * @param {string} filename  Suggested filename for the download dialog
 * @param {string} [mime]    MIME type (default: text/csv;charset=utf-8)
 */
function downloadBlob(content, filename, mime = 'text/csv;charset=utf-8;') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
