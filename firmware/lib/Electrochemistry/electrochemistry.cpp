/**
 * Electrochemistry Library — Portable Implementations
 *
 * Pure C/C++ — NO Arduino.h, NO hardware calls.
 * Uses only: <math.h>, <string.h>, <stdlib.h>
 *
 * All functions here are testable natively on PC via PlatformIO [env:native].
 * Real implementations will be filled in during Phases 1–5.
 */

#include "electrochemistry.h"
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// --- Pure Math Functions ---

int voltToDAC(float voltage) {
    int dac = (int)roundf(DAC_MID + voltage * DAC_SCALE);
    if (dac < 0)        dac = 0;
    if (dac > DAC_MAX)  dac = DAC_MAX;
    return dac;
}

float dacToVolt(int dac) {
    return (dac - DAC_MID) / DAC_SCALE;
}

int parseStepDAC(float stepE_mV) {
    int counts = (int)roundf(stepE_mV / 3.22f);
    return counts < 1 ? 1 : counts;
}

// --- Command Parsers ---

bool parseCV(const char* line, CVParams* params) {
    if (!line || !params) return false;

    // Always set defaults first
    params->Vstart      = -1.0f;
    params->Vstop       =  1.0f;
    params->numCycles   =  1;
    params->scanRate_ms =  30;

    // Try to parse parameters after the command character
    const char* args = line + 1;
    int matched = sscanf(args, " %f,%f,%d,%d",
                         &params->Vstart, &params->Vstop,
                         &params->numCycles, &params->scanRate_ms);

    // Partial parse is invalid
    if (matched > 0 && matched < 4) return false;

    return true;
}

bool parseDPV(const char* line, DPVParams* params) {
    if (!line || !params) return false;

    // Always set defaults first
    params->Vstart         = -1.0f;
    params->Vstop          =  1.0f;
    params->Veq            = -1.0f;
    params->teq            =  5.0f;
    params->stepE_mV       = 15.0f;
    params->pulseAmp_mV    = 90.0f;
    params->pulsePeriod_ms = 100;
    params->pulseWidth_ms  =  25;

    // Try to parse parameters after the command character
    const char* args = line + 1;
    int matched = sscanf(args, " %f,%f,%f,%f,%f,%f,%d,%d",
                         &params->Vstart, &params->Vstop,
                         &params->Veq, &params->teq,
                         &params->stepE_mV, &params->pulseAmp_mV,
                         &params->pulsePeriod_ms, &params->pulseWidth_ms);

    // Partial parse is invalid
    if (matched > 0 && matched < 8) return false;

    return true;
}

// --- Validation ---

const char* validateCVParams(const CVParams* p) {
    if (!p) return "null params";
    if (p->Vstart < -V_MAX || p->Vstart > V_MAX) return "Vstart exceeds +/-1.65V";
    if (p->Vstop < -V_MAX || p->Vstop > V_MAX) return "Vstop exceeds +/-1.65V";
    if (p->numCycles < 1) return "numCycles must be >= 1";
    if (p->scanRate_ms < 1) return "scanRate must be >= 1ms";
    return NULL;  // valid
}

const char* validateDPVParams(const DPVParams* p) {
    if (!p) return "null params";
    if (p->Vstart < -V_MAX || p->Vstart > V_MAX) return "Vstart exceeds +/-1.65V";
    if (p->Vstop < -V_MAX || p->Vstop > V_MAX) return "Vstop exceeds +/-1.65V";
    if (p->Veq < -V_MAX || p->Veq > V_MAX) return "Veq exceeds +/-1.65V";
    if (p->stepE_mV <= 0) return "stepE must be > 0";
    if (p->pulseAmp_mV <= 0) return "pulseAmp must be > 0";
    if (p->pulseWidth_ms < READ_TIME_MS)
        return "pulseWidth must be >= READ_TIME_MS for accurate sampling";
    if (p->pulsePeriod_ms - p->pulseWidth_ms < READ_TIME_MS)
        return "pulsePeriod must exceed pulseWidth by >= READ_TIME_MS for baseline sampling";
    return NULL;  // valid
}

// --- Algorithm Functions ---

void runCVAlgorithm(CVParams p, SetVoltageFn setV, ReadCurrentFn readI,
                    EmitPointFn emit, AbortCheckFn abortCheck, DelayFn delayMs) {

    int dacStart = voltToDAC(p.Vstart);
    int dacStop  = voltToDAC(p.Vstop);

    for (int cycle = 0; cycle < p.numCycles; cycle++) {

        // Forward scan: Vstart → Vstop
        for (int dac = dacStart; dac <= dacStop; dac++) {
            setV(dac);
            delayMs((unsigned long)p.scanRate_ms);
            emit(dacToVolt(dac), readI());
            if (abortCheck()) {
                setV(voltToDAC(0.0f));
                return;
            }
        }

        // Reverse scan: Vstop → Vstart
        for (int dac = dacStop; dac >= dacStart; dac--) {
            setV(dac);
            delayMs((unsigned long)p.scanRate_ms);
            emit(dacToVolt(dac), readI());
            if (abortCheck()) {
                setV(voltToDAC(0.0f));
                return;
            }
        }
    }

    // Normal completion
    setV(voltToDAC(0.0f));
}

void runDPVAlgorithm(DPVParams p, SetVoltageFn setV, ReadCurrentFn readI,
                     EmitPointFn emit, AbortCheckFn abortCheck, DelayFn delayMs) {

    int dacStart = voltToDAC(p.Vstart);
    int dacEnd   = voltToDAC(p.Vstop);
    int stepDAC  = parseStepDAC(p.stepE_mV);
    int pulseDAC = (int)roundf(p.pulseAmp_mV / 3.22f);
    if (pulseDAC < 1) pulseDAC = 1;

    for (int dac = dacStart; dac <= dacEnd; dac += stepDAC) {

        // Baseline phase: set base potential, wait, read
        setV(dac);
        delayMs((unsigned long)(p.pulsePeriod_ms - p.pulseWidth_ms - READ_TIME_MS));
        float I_base = readI();

        // Pulse phase: apply pulse, wait, read (clamp to DAC_MAX)
        setV(dac + pulseDAC > DAC_MAX ? DAC_MAX : dac + pulseDAC);
        delayMs((unsigned long)(p.pulseWidth_ms - READ_TIME_MS));
        float I_pulse = readI();

        // Emit differential current at base voltage
        emit(dacToVolt(dac), I_pulse - I_base);

        // Check abort after each complete step
        if (abortCheck()) {
            setV(voltToDAC(0.0f));
            return;
        }
    }

    // Normal completion
    setV(voltToDAC(0.0f));
}
