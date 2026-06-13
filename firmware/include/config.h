#ifndef CONFIG_H
#define CONFIG_H

// --- Pin Definitions ---
#define DAC_PIN       0       // XIAO D0/A0 — 10-bit DAC output
#define RE_PIN        1       // XIAO D1/A1 — RE buffer output (U3.2 OUTB), 12-bit ADC input

// --- I2C ---
#define ADS_ADDR      0x48    // ADS1115 default address (ADDR pin to GND)

// --- DAC Constants ---
#define DAC_MID       512     // DAC value for 0V applied (midpoint of 10-bit range)
#define DAC_SCALE     312.0f  // DAC counts per volt: ~1024/3.3 ≈ 310, calibrated to 312
#define DAC_MAX       1023    // 10-bit maximum

// --- ADC / Measurement ---
#define N_SAMPLES     5       // Number of ADS1115 samples per read
#define READ_TIME_MS  6       // Time for one N_SAMPLES read (5 × 1.2ms = 6ms)
                              // Used in DPV delay math: delay = phase_time - READ_TIME_MS

// --- Serial ---
#define BAUD          115200  // Unified baud rate for all communication

// --- Voltage Limits ---
#define V_MAX         1.65f   // Maximum applied voltage (±1.65V, limited by supply rails)

// --- Hardware Characterisation (L = linearity sweep, T = step response) ---
#define VIN_CHANNEL    1      // ADS1115 AIN1 = Vin (U3.1 level-shifter output, H1 pin 9)
#define LIN_STEP       1      // Default DAC increment per linearity step
#define LIN_SETTLE_MS  2      // DAC + level-shifter settle time per step (ms)
#define STEP_SETTLE_MS 50     // Baseline settle before applying a step (ms)
#define STEP_SAMPLES   64     // Default samples captured after a step
#define STEP_DAC_DELTA 62     // Default DAC step magnitude (~200mV at 312 counts/V)

#endif // CONFIG_H
