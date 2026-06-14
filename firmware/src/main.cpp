/**
 * Unified Potentiostat Firmware — Hardware Layer
 *
 * This file contains all hardware-specific code: setup(), loop(),
 * analogWrite(), ADS1115 calls. All testable logic lives in
 * lib/Electrochemistry/ and is accessed via #include "electrochemistry.h".
 *
 * Target: Seeeduino XIAO (SAMD21G18A)
 * ADC: ADS1115 via I2C at 0x48
 * DAC: 10-bit on pin D0/A0
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include "config.h"
#include "electrochemistry.h"

// --- Global State ---
Adafruit_ADS1115 ads;
float zeroOffset = 0.0f;

// --- Hardware Functions ---

void setDAC(int value) {
    analogWrite(DAC_PIN, constrain(value, 0, 1023));
}

float readRE(void) {
    // Read buffered RE electrode voltage via XIAO D1/A1 (U3.2 OUTB).
    // SAMD21 12-bit ADC, single-supply 0–3.3 V. ~10 µs, negligible timing cost.
    return analogRead(RE_PIN) * (3.3f / 4095.0f);
}

float readCurrentContinuous(void) {
    float sum = 0.0f;
    for (uint8_t i = 0; i < N_SAMPLES; i++) {
        sum += ads.getLastConversionResults();
        delayMicroseconds(1200);  // 1/860 SPS = 1.16ms between samples
    }
    float voltage = (sum / N_SAMPLES) * (4.096f / 32768.0f);
    return (voltage - zeroOffset) * 100.0f;  // µA
}

void emitPoint(float voltage, float current) {
    Serial.print(voltage, 4);
    Serial.print(',');
    Serial.print(current, 4);
    Serial.print(',');
    Serial.println(readRE(), 4);  // third column: measured RE voltage (V)
}

bool checkAbort(void) {
    if (Serial.available() && Serial.peek() == '!') {
        Serial.read();
        return true;
    }
    return false;
}

void autoZero(void) {
    setDAC(DAC_MID);
    delay(100);  // settle time
    float sum = 0.0f;
    for (int i = 0; i < 20; i++) {
        sum += ads.getLastConversionResults();
        delayMicroseconds(1200);
    }
    zeroOffset = (sum / 20.0f) * (4.096f / 32768.0f);
    Serial.print("Z: offset=");
    Serial.print(zeroOffset * 1000.0f, 2);
    Serial.println("mV");
}

// --- Scan Wrappers ---

void runCV(CVParams p) {
    Serial.println("*");
    runCVAlgorithm(p, setDAC, readCurrentContinuous, emitPoint, checkAbort, delay);
    setDAC(voltToDAC(0.0f));
    Serial.println("#");
}

void runDPV(DPVParams p) {
    // Equilibration in wrapper (uses Arduino delay)
    setDAC(voltToDAC(p.Veq));
    delay((unsigned long)(p.teq * 1000));
    Serial.println("*");
    float actualStepMV = parseStepDAC(p.stepE_mV) * (3300.0f / 1024.0f);
    Serial.print("# stepE_actual=");
    Serial.print(actualStepMV, 1);
    Serial.println("mV");
    runDPVAlgorithm(p, setDAC, readCurrentContinuous, emitPoint, checkAbort, delay);
    setDAC(voltToDAC(0.0f));
    Serial.println("$");
}

// --- Hardware Characterisation Routines ---

// L — DAC linearity sweep. Standalone dry-bench test (no electrodes needed).
// Parks the ADS1115 mux on AIN1 (Vin = U3.1 level-shifter output, H1 pin 9),
// sweeps the DAC across its full range, and reports the measured output voltage.
// Output: "L*" ... "dac_count,measured_volts" per step ... "L#".
// Restores AIN0 continuous mode on exit so current measurement keeps working.
void runLinearity(int stepSize) {
    if (stepSize < 1) stepSize = LIN_STEP;
    Serial.println("L*");
    for (int dac = 0; dac <= DAC_MAX; dac += stepSize) {
        if (checkAbort()) break;
        setDAC(dac);
        delay(LIN_SETTLE_MS);  // let DAC + level shifter settle
        // readADC_SingleEnded switches the mux to AIN1, runs one conversion, returns it
        int16_t raw = ads.readADC_SingleEnded(VIN_CHANNEL);
        float volts = raw * (4.096f / 32768.0f);
        Serial.print(dac);
        Serial.print(',');
        Serial.println(volts, 5);
    }
    setDAC(DAC_MID);
    // Restore continuous-mode current measurement on AIN0
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_SINGLE_0, true);
    Serial.println("L#");
}

// T — step-response capture. Applies a voltage step and streams the current
// channel (ADS1115 AIN0) as fast as possible to reveal the analog chain's
// RC settling behaviour. Output: "T*" ... "elapsed_us,current_uA" ... "T#".
void runStep(int dacBefore, int dacAfter, int nSamples) {
    // Ensure continuous mode on AIN0 (current channel)
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_SINGLE_0, true);
    setDAC(dacBefore);
    delay(STEP_SETTLE_MS);  // establish baseline
    Serial.println("T*");
    unsigned long t0 = micros();
    setDAC(dacAfter);       // apply the step at t = 0
    for (int i = 0; i < nSamples; i++) {
        if (checkAbort()) break;
        int16_t raw = ads.getLastConversionResults();
        unsigned long elapsed = micros() - t0;
        float volts = raw * (4.096f / 32768.0f);
        float current = (volts - zeroOffset) * 100.0f;  // µA
        Serial.print(elapsed);
        Serial.print(',');
        Serial.println(current, 4);
    }
    setDAC(voltToDAC(0.0f));
    Serial.println("T#");
}

// Q — channel-scan diagnostic. Sets the DAC to a fixed value, then reports ALL
// ADS1115 single-ended channels (AIN0-3) plus the four differential pairs.
// Run twice at separated DAC values (e.g. "Q 900" then "Q 100") and look for
// which channel CHANGES between them — that channel carries Vin. Differential
// pairs reveal Vin even when single-ended clips a negative swing to 0.
// Output: "Q*" ... "Q dac=... Vin_theoretical=..." ... per-channel lines ... "Q#".
// Restores AIN0 continuous mode on exit so normal CV/DPV keeps working.
void runChannelQuery(int dacVal) {
    setDAC(dacVal);
    delay(10);  // generous settle for DAC + level shifter
    const float LSB = 4.096f / 32768.0f;

    Serial.println("Q*");
    Serial.print("Q dac=");
    Serial.print(dacVal);
    Serial.print(" Vin_theoretical=");
    Serial.println(dacToVolt(dacVal), 4);

    // Single-ended channels (0 to +4.096V, clips negatives to 0)
    for (int ch = 0; ch < 4; ch++) {
        int16_t raw = ads.readADC_SingleEnded(ch);
        Serial.print("AIN");
        Serial.print(ch);
        Serial.print('=');
        Serial.println(raw * LSB, 5);
    }

    // Differential pairs (bipolar +/-4.096V — can report negative Vin)
    Serial.print("DIFF_0_1=");
    Serial.println(ads.readADC_Differential_0_1() * LSB, 5);
    Serial.print("DIFF_0_3=");
    Serial.println(ads.readADC_Differential_0_3() * LSB, 5);
    Serial.print("DIFF_1_3=");
    Serial.println(ads.readADC_Differential_1_3() * LSB, 5);
    Serial.print("DIFF_2_3=");
    Serial.println(ads.readADC_Differential_2_3() * LSB, 5);

    setDAC(DAC_MID);
    // Restore continuous-mode current measurement on AIN0
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_SINGLE_0, true);
    Serial.println("Q#");
}

// --- Setup & Loop ---

void setup() {
    Serial.begin(BAUD);
    Wire.begin();

    if (!ads.begin(ADS_ADDR)) {
        Serial.println("E: ADS1115 not found");
        while (1) { delay(1000); }  // halt
    }
    ads.setGain(GAIN_ONE);
    ads.setDataRate(RATE_ADS1115_860SPS);
    ads.startADCReading(ADS1X15_REG_CONFIG_MUX_SINGLE_0, true);  // continuous mode

    analogWriteResolution(10);  // SAMD21 supports 10-bit DAC
    analogReadResolution(12);   // SAMD21 supports 12-bit ADC (RE_PIN)
    setDAC(DAC_MID);            // start at 0V applied
}

void loop() {
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.length() == 0) return;

        char cmd = line.charAt(0);
        switch (cmd) {
            case 'C': case 'c': {
                CVParams p;
                if (parseCV(line.c_str(), &p)) {
                    const char* err = validateCVParams(&p);
                    if (err) { Serial.print("E: "); Serial.println(err); }
                    else { runCV(p); }
                } else {
                    Serial.println("E: Bad CV params");
                }
                break;
            }
            case 'D': case 'd': {
                DPVParams p;
                if (parseDPV(line.c_str(), &p)) {
                    const char* err = validateDPVParams(&p);
                    if (err) { Serial.print("E: "); Serial.println(err); }
                    else { runDPV(p); }
                } else {
                    Serial.println("E: Bad DPV params");
                }
                break;
            }
            case '!':
                // Abort handled inline by checkAbort() during scans
                break;
            case 'Z': case 'z':
                autoZero();
                break;
            case 'I': case 'i':
                Serial.println("POTENTIOSTAT v1.0 SAMD21 ADS1115");
                break;
            case 'L': case 'l': {
                // L [stepSize] — DAC linearity sweep via Vin readback (AIN1)
                int stepSize = LIN_STEP;
                sscanf(line.c_str() + 1, "%d", &stepSize);
                runLinearity(stepSize);
                break;
            }
            case 'T': case 't': {
                // T [dacBefore,dacAfter,nSamples] — step-response capture (AIN0)
                int dacBefore = DAC_MID;
                int dacAfter  = DAC_MID + STEP_DAC_DELTA;
                int nSamples  = STEP_SAMPLES;
                sscanf(line.c_str() + 1, "%d,%d,%d", &dacBefore, &dacAfter, &nSamples);
                dacBefore = constrain(dacBefore, 0, DAC_MAX);
                dacAfter  = constrain(dacAfter, 0, DAC_MAX);
                nSamples  = constrain(nSamples, 1, 2000);
                runStep(dacBefore, dacAfter, nSamples);
                break;
            }
            case 'Q': case 'q': {
                // Q [dac] — channel-scan diagnostic (AIN0-3 + diff pairs at one DAC value)
                int dacVal = DAC_MID;
                sscanf(line.c_str() + 1, "%d", &dacVal);
                dacVal = constrain(dacVal, 0, DAC_MAX);
                runChannelQuery(dacVal);
                break;
            }
            default:
                Serial.print("E: Unknown command '");
                Serial.print(cmd);
                Serial.println("'");
                break;
        }
    }
}
