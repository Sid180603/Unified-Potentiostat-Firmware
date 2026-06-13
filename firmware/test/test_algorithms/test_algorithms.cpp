/**
 * test_algorithms.cpp — Tests for CV/DPV algorithm structure via dependency injection
 *
 * Uses mock callbacks to verify:
 * - Step count correctness (DPV ≈124, CV ≈1248 for defaults)
 * - Abort stops within 1 extra point
 * - CV forward/reverse ordering
 * - DPV baseline→pulse sequencing
 *
 * All tests fail at Phase 0 (algorithms are stubs). Go green at Phase 3-4.
 * Run with: pio test -e native
 */

#include <unity.h>
#include "electrochemistry.h"

// ============================================================
// Mock state
// ============================================================

static int   g_stepsCalled = 0;
static int   g_lastDac = 0;
static int   g_abortAfterN = -1;  // -1 = never abort
static int   g_dacSequence[2000]; // record DAC calls
static int   g_dacSeqIdx = 0;

static void mockSetVoltage(int dacValue) {
    g_lastDac = dacValue;
    if (g_dacSeqIdx < 2000) {
        g_dacSequence[g_dacSeqIdx++] = dacValue;
    }
}

static float mockReadCurrent(void) {
    return 1.0f;  // constant — we're testing structure, not values
}

static void mockEmitPoint(float voltage, float current) {
    (void)voltage;
    (void)current;
    g_stepsCalled++;
}

static bool mockAbortCheck(void) {
    if (g_abortAfterN >= 0 && g_stepsCalled >= g_abortAfterN) {
        return true;
    }
    return false;
}

static void mockDelay(unsigned long ms) {
    (void)ms;  // no-op — tests run at full speed
}

static void resetMocks(void) {
    g_stepsCalled = 0;
    g_lastDac = 0;
    g_abortAfterN = -1;
    g_dacSeqIdx = 0;
}

// ============================================================
// DPV Algorithm Tests
// ============================================================

void test_dpv_step_count_defaults(void) {
    resetMocks();
    DPVParams p = {-1.0f, 1.0f, -1.0f, 0, 15.0f, 90.0f, 100, 25};
    runDPVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // 2000mV / 16.1mV per step ≈ 124 steps
    TEST_ASSERT_INT_WITHIN(3, 124, g_stepsCalled);
}

void test_dpv_step_count_narrow_range(void) {
    resetMocks();
    DPVParams p = {-0.5f, 0.5f, -0.5f, 0, 25.0f, 90.0f, 100, 25};
    runDPVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // 1000mV / (8*3.22=25.8mV) ≈ 39 steps
    TEST_ASSERT_INT_WITHIN(2, 39, g_stepsCalled);
}

void test_dpv_abort_stops_early(void) {
    resetMocks();
    g_abortAfterN = 10;
    DPVParams p = {-1.0f, 1.0f, -1.0f, 0, 15.0f, 90.0f, 100, 25};
    runDPVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // Should stop at most 1 point after abort trigger
    TEST_ASSERT_TRUE(g_stepsCalled <= 11);
}

void test_dpv_abort_returns_to_zero(void) {
    resetMocks();
    g_abortAfterN = 5;
    DPVParams p = {-1.0f, 1.0f, -1.0f, 0, 15.0f, 90.0f, 100, 25};
    runDPVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // Last DAC value set should be voltToDAC(0) = 512
    TEST_ASSERT_EQUAL_INT(voltToDAC(0.0f), g_lastDac);
}

void test_dpv_completion_returns_to_zero(void) {
    resetMocks();
    DPVParams p = {-1.0f, 1.0f, -1.0f, 0, 15.0f, 90.0f, 100, 25};
    runDPVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // After normal completion, DAC should be at 0V
    TEST_ASSERT_EQUAL_INT(voltToDAC(0.0f), g_lastDac);
}

// ============================================================
// CV Algorithm Tests
// ============================================================

void test_cv_step_count_one_cycle(void) {
    resetMocks();
    CVParams p = {-1.0f, 1.0f, 1, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // voltToDAC(1.0) - voltToDAC(-1.0) = 824 - 200 = 624 steps fwd + 624 rev = 1248
    TEST_ASSERT_INT_WITHIN(5, 1248, g_stepsCalled);
}

void test_cv_step_count_two_cycles(void) {
    resetMocks();
    CVParams p = {-1.0f, 1.0f, 2, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    // 2 cycles × 1248 = 2496
    TEST_ASSERT_INT_WITHIN(10, 2496, g_stepsCalled);
}

void test_cv_forward_then_reverse(void) {
    resetMocks();
    CVParams p = {-1.0f, 1.0f, 1, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);

    // First few DAC values should be increasing (forward scan)
    if (g_dacSeqIdx > 10) {
        TEST_ASSERT_TRUE(g_dacSequence[5] > g_dacSequence[0]);
    }
    // After midpoint, DAC values should decrease (reverse scan)
    if (g_dacSeqIdx > 1300) {
        TEST_ASSERT_TRUE(g_dacSequence[g_dacSeqIdx - 5] < g_dacSequence[g_dacSeqIdx / 2]);
    }
}

void test_cv_abort_stops_early(void) {
    resetMocks();
    g_abortAfterN = 10;
    CVParams p = {-1.0f, 1.0f, 1, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    TEST_ASSERT_TRUE(g_stepsCalled <= 11);
}

void test_cv_abort_returns_to_zero(void) {
    resetMocks();
    g_abortAfterN = 10;
    CVParams p = {-1.0f, 1.0f, 1, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    TEST_ASSERT_EQUAL_INT(voltToDAC(0.0f), g_lastDac);
}

void test_cv_completion_returns_to_zero(void) {
    resetMocks();
    CVParams p = {-1.0f, 1.0f, 1, 30};
    runCVAlgorithm(p, mockSetVoltage, mockReadCurrent, mockEmitPoint, mockAbortCheck, mockDelay);
    TEST_ASSERT_EQUAL_INT(voltToDAC(0.0f), g_lastDac);
}

// ============================================================
// Test Runner
// ============================================================

void setUp(void) { resetMocks(); }
void tearDown(void) {}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    UNITY_BEGIN();

    // DPV
    RUN_TEST(test_dpv_step_count_defaults);
    RUN_TEST(test_dpv_step_count_narrow_range);
    RUN_TEST(test_dpv_abort_stops_early);
    RUN_TEST(test_dpv_abort_returns_to_zero);
    RUN_TEST(test_dpv_completion_returns_to_zero);

    // CV
    RUN_TEST(test_cv_step_count_one_cycle);
    RUN_TEST(test_cv_step_count_two_cycles);
    RUN_TEST(test_cv_forward_then_reverse);
    RUN_TEST(test_cv_abort_stops_early);
    RUN_TEST(test_cv_abort_returns_to_zero);
    RUN_TEST(test_cv_completion_returns_to_zero);

    return UNITY_END();
}
