#ifndef ELECTROCHEMISTRY_H
#define ELECTROCHEMISTRY_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>

// --- Configuration (duplicated for portability — no Arduino.h dependency) ---
#ifndef DAC_MID
#define DAC_MID     512
#endif
#ifndef DAC_SCALE
#define DAC_SCALE   312.0f
#endif
#ifndef DAC_MAX
#define DAC_MAX     1023
#endif
#ifndef READ_TIME_MS
#define READ_TIME_MS 6
#endif
#ifndef V_MAX
#define V_MAX       1.65f
#endif

// --- Parameter Structures ---

typedef struct {
    float Vstart;       // Start voltage (V), default -1.0
    float Vstop;        // Stop voltage (V), default 1.0
    int   numCycles;    // Number of full cycles, default 1
    int   scanRate_ms;  // Delay per DAC step (ms), default 30
} CVParams;

typedef struct {
    float Vstart;           // Start voltage (V), default -1.0
    float Vstop;            // Stop voltage (V), default 1.0
    float Veq;              // Equilibration voltage (V), default -1.0
    float teq;              // Equilibration time (seconds), default 5
    float stepE_mV;         // Step potential (millivolts), default 15
    float pulseAmp_mV;      // Pulse amplitude (millivolts), default 90
    int   pulsePeriod_ms;   // Pulse period (ms), default 100
    int   pulseWidth_ms;    // Pulse width (ms), default 25
} DPVParams;

// --- Callback Typedefs (Dependency Injection for testability) ---

typedef void  (*SetVoltageFn)(int dacValue);
typedef float (*ReadCurrentFn)(void);
typedef void  (*EmitPointFn)(float voltage, float current);
typedef bool  (*AbortCheckFn)(void);
typedef void  (*DelayFn)(unsigned long ms);

// --- Pure Math Functions ---

/**
 * Convert applied voltage (V) to DAC value.
 * V_applied = (DAC - 512) / 312.0  →  DAC = 512 + V × 312
 * Result clamped to [0, 1023].
 */
int voltToDAC(float voltage);

/**
 * Convert DAC value to applied voltage (V).
 * V = (DAC - 512) / 312.0
 */
float dacToVolt(int dac);

/**
 * Convert step potential (mV) to DAC step count.
 * Returns max(1, round(stepE_mV / 3.22))
 */
int parseStepDAC(float stepE_mV);

// --- Command Parsers ---

/**
 * Parse a CV command string. Bare "C" uses defaults.
 * Format: "C [Vstart,Vstop,numCycles,scanRate_ms]"
 * Returns true on success, false on parse error.
 */
bool parseCV(const char* line, CVParams* params);

/**
 * Parse a DPV command string. Bare "D" uses defaults.
 * Format: "D [Vstart,Vstop,Veq,teq_s,stepE_mV,pulseAmp_mV,pulsePeriod_ms,pulseWidth_ms]"
 * Returns true on success, false on parse error.
 */
bool parseDPV(const char* line, DPVParams* params);

// --- Validation ---

/**
 * Validate CV parameters. Returns NULL if valid, or error message string.
 */
const char* validateCVParams(const CVParams* p);

/**
 * Validate DPV parameters. Returns NULL if valid, or error message string.
 */
const char* validateDPVParams(const DPVParams* p);

// --- Algorithm Functions (Dependency-Injected) ---

/**
 * Run cyclic voltammetry algorithm.
 * Forward scan (Vstart→Vstop) then reverse (Vstop→Vstart), repeated numCycles times.
 * Emits data via emitFn. Checks abort after each point.
 * Does NOT print protocol markers (* / #) — caller (wrapper) handles those.
 */
void runCVAlgorithm(CVParams p, SetVoltageFn setV, ReadCurrentFn readI,
                    EmitPointFn emit, AbortCheckFn abortCheck, DelayFn delayMs);

/**
 * Run differential pulse voltammetry algorithm.
 * Staircase from Vstart to Vstop with baseline→pulse→ΔI measurement.
 * Assumes equilibration already done by caller.
 * Does NOT print protocol markers (* / $) — caller (wrapper) handles those.
 */
void runDPVAlgorithm(DPVParams p, SetVoltageFn setV, ReadCurrentFn readI,
                     EmitPointFn emit, AbortCheckFn abortCheck, DelayFn delayMs);

#ifdef __cplusplus
}
#endif

#endif // ELECTROCHEMISTRY_H
