/**
 * test_math.cpp — Unity tests for pure math functions
 *
 * Tests voltToDAC, dacToVolt, parseStepDAC, parseCV, parseDPV, validation.
 * These are written TDD-style: all tests fail at Phase 0, go green at Phase 1-2.
 *
 * Run with: pio test -e native
 */

#include <unity.h>
#include "electrochemistry.h"
#include <math.h>

// ============================================================
// voltToDAC tests
// ============================================================

void test_voltToDAC_zero_gives_512(void) {
    TEST_ASSERT_EQUAL_INT(512, voltToDAC(0.0f));
}

void test_voltToDAC_pos1V_gives_824(void) {
    TEST_ASSERT_EQUAL_INT(824, voltToDAC(1.0f));
}

void test_voltToDAC_neg1V_gives_200(void) {
    TEST_ASSERT_EQUAL_INT(200, voltToDAC(-1.0f));
}

void test_voltToDAC_clamps_high(void) {
    // 2.0V would give 512 + 624 = 1136, clamped to 1023
    TEST_ASSERT_EQUAL_INT(1023, voltToDAC(2.0f));
}

void test_voltToDAC_clamps_low(void) {
    // -2.0V would give 512 - 624 = -112, clamped to 0
    TEST_ASSERT_EQUAL_INT(0, voltToDAC(-2.0f));
}

void test_voltToDAC_small_positive(void) {
    // 0.5V → 512 + 156 = 668
    TEST_ASSERT_EQUAL_INT(668, voltToDAC(0.5f));
}

void test_voltToDAC_small_negative(void) {
    // -0.5V → 512 - 156 = 356
    TEST_ASSERT_EQUAL_INT(356, voltToDAC(-0.5f));
}

// ============================================================
// dacToVolt tests
// ============================================================

void test_dacToVolt_512_gives_zero(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, dacToVolt(512));
}

void test_dacToVolt_200_gives_neg1(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.005f, -1.0f, dacToVolt(200));
}

void test_dacToVolt_824_gives_pos1(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.005f, 1.0f, dacToVolt(824));
}

void test_dacToVolt_0_gives_neg1_64(void) {
    // 0 → (0-512)/312 = -1.641
    TEST_ASSERT_FLOAT_WITHIN(0.01f, -1.641f, dacToVolt(0));
}

void test_dacToVolt_1023_gives_pos1_64(void) {
    // 1023 → (1023-512)/312 = 1.638
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 1.638f, dacToVolt(1023));
}

// ============================================================
// parseStepDAC tests
// ============================================================

void test_parseStepDAC_15mV_gives_5(void) {
    // 15.0 / 3.22 = 4.66 → round = 5
    TEST_ASSERT_EQUAL_INT(5, parseStepDAC(15.0f));
}

void test_parseStepDAC_25mV_gives_8(void) {
    // 25.0 / 3.22 = 7.76 → round = 8
    TEST_ASSERT_EQUAL_INT(8, parseStepDAC(25.0f));
}

void test_parseStepDAC_tiny_gives_minimum_1(void) {
    // 0.5 / 3.22 = 0.155 → round = 0 → clamped to 1
    TEST_ASSERT_EQUAL_INT(1, parseStepDAC(0.5f));
}

void test_parseStepDAC_10mV_gives_3(void) {
    // 10.0 / 3.22 = 3.11 → round = 3
    TEST_ASSERT_EQUAL_INT(3, parseStepDAC(10.0f));
}

void test_parseStepDAC_50mV_gives_16(void) {
    // 50.0 / 3.22 = 15.53 → round = 16
    TEST_ASSERT_EQUAL_INT(16, parseStepDAC(50.0f));
}

// ============================================================
// parseCV tests
// ============================================================

void test_parseCV_bare_command_uses_defaults(void) {
    CVParams p;
    bool ok = parseCV("C", &p);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -1.0f, p.Vstart);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, p.Vstop);
    TEST_ASSERT_EQUAL_INT(1, p.numCycles);
    TEST_ASSERT_EQUAL_INT(30, p.scanRate_ms);
}

void test_parseCV_with_params(void) {
    CVParams p;
    bool ok = parseCV("C -0.5,0.5,3,50", &p);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -0.5f, p.Vstart);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.5f, p.Vstop);
    TEST_ASSERT_EQUAL_INT(3, p.numCycles);
    TEST_ASSERT_EQUAL_INT(50, p.scanRate_ms);
}

void test_parseCV_null_returns_false(void) {
    CVParams p;
    TEST_ASSERT_FALSE(parseCV(NULL, &p));
}

// ============================================================
// parseDPV tests
// ============================================================

void test_parseDPV_bare_command_uses_defaults(void) {
    DPVParams p;
    bool ok = parseDPV("D", &p);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -1.0f, p.Vstart);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, p.Vstop);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -1.0f, p.Veq);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 5.0f, p.teq);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 15.0f, p.stepE_mV);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 90.0f, p.pulseAmp_mV);
    TEST_ASSERT_EQUAL_INT(100, p.pulsePeriod_ms);
    TEST_ASSERT_EQUAL_INT(25, p.pulseWidth_ms);
}

void test_parseDPV_with_params(void) {
    DPVParams p;
    bool ok = parseDPV("D -0.5,0.5,-0.5,2,25,90,100,25", &p);
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -0.5f, p.Vstart);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.5f, p.Vstop);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -0.5f, p.Veq);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 2.0f, p.teq);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 25.0f, p.stepE_mV);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 90.0f, p.pulseAmp_mV);
    TEST_ASSERT_EQUAL_INT(100, p.pulsePeriod_ms);
    TEST_ASSERT_EQUAL_INT(25, p.pulseWidth_ms);
}

void test_parseDPV_null_returns_false(void) {
    DPVParams p;
    TEST_ASSERT_FALSE(parseDPV(NULL, &p));
}

// ============================================================
// Validation tests
// ============================================================

void test_validateCV_valid_params(void) {
    CVParams p = {-1.0f, 1.0f, 1, 30};
    TEST_ASSERT_NULL(validateCVParams(&p));
}

void test_validateCV_rejects_Vstop_too_high(void) {
    CVParams p = {-1.0f, 2.0f, 1, 30};
    TEST_ASSERT_NOT_NULL(validateCVParams(&p));
}

void test_validateCV_rejects_Vstart_too_low(void) {
    CVParams p = {-2.0f, 1.0f, 1, 30};
    TEST_ASSERT_NOT_NULL(validateCVParams(&p));
}

void test_validateCV_rejects_zero_cycles(void) {
    CVParams p = {-1.0f, 1.0f, 0, 30};
    TEST_ASSERT_NOT_NULL(validateCVParams(&p));
}

void test_validateCV_rejects_zero_scanrate(void) {
    CVParams p = {-1.0f, 1.0f, 1, 0};
    TEST_ASSERT_NOT_NULL(validateCVParams(&p));
}

void test_validateDPV_valid_params(void) {
    DPVParams p = {-1.0f, 1.0f, -1.0f, 5.0f, 15.0f, 90.0f, 100, 25};
    TEST_ASSERT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_Vstop_too_high(void) {
    DPVParams p = {-1.0f, 2.0f, -1.0f, 5.0f, 15.0f, 90.0f, 100, 25};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_zero_step(void) {
    DPVParams p = {-1.0f, 1.0f, -1.0f, 5.0f, 0.0f, 90.0f, 100, 25};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_pulseWidth_ge_period(void) {
    DPVParams p = {-1.0f, 1.0f, -1.0f, 5.0f, 15.0f, 90.0f, 100, 100};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_Veq_too_high(void) {
    DPVParams p = {-1.0f, 1.0f, 2.0f, 5.0f, 15.0f, 90.0f, 100, 25};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_small_pulseWidth(void) {
    DPVParams p = {-1.0f, 1.0f, -1.0f, 5.0f, 15.0f, 90.0f, 100, 4};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

void test_validateDPV_rejects_insufficient_period_margin(void) {
    DPVParams p = {-1.0f, 1.0f, -1.0f, 5.0f, 15.0f, 90.0f, 20, 15};
    TEST_ASSERT_NOT_NULL(validateDPVParams(&p));
}

// ============================================================
// Test Runner
// ============================================================

void setUp(void) {}
void tearDown(void) {}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    UNITY_BEGIN();

    // voltToDAC
    RUN_TEST(test_voltToDAC_zero_gives_512);
    RUN_TEST(test_voltToDAC_pos1V_gives_824);
    RUN_TEST(test_voltToDAC_neg1V_gives_200);
    RUN_TEST(test_voltToDAC_clamps_high);
    RUN_TEST(test_voltToDAC_clamps_low);
    RUN_TEST(test_voltToDAC_small_positive);
    RUN_TEST(test_voltToDAC_small_negative);

    // dacToVolt
    RUN_TEST(test_dacToVolt_512_gives_zero);
    RUN_TEST(test_dacToVolt_200_gives_neg1);
    RUN_TEST(test_dacToVolt_824_gives_pos1);
    RUN_TEST(test_dacToVolt_0_gives_neg1_64);
    RUN_TEST(test_dacToVolt_1023_gives_pos1_64);

    // parseStepDAC
    RUN_TEST(test_parseStepDAC_15mV_gives_5);
    RUN_TEST(test_parseStepDAC_25mV_gives_8);
    RUN_TEST(test_parseStepDAC_tiny_gives_minimum_1);
    RUN_TEST(test_parseStepDAC_10mV_gives_3);
    RUN_TEST(test_parseStepDAC_50mV_gives_16);

    // parseCV
    RUN_TEST(test_parseCV_bare_command_uses_defaults);
    RUN_TEST(test_parseCV_with_params);
    RUN_TEST(test_parseCV_null_returns_false);

    // parseDPV
    RUN_TEST(test_parseDPV_bare_command_uses_defaults);
    RUN_TEST(test_parseDPV_with_params);
    RUN_TEST(test_parseDPV_null_returns_false);

    // Validation
    RUN_TEST(test_validateCV_valid_params);
    RUN_TEST(test_validateCV_rejects_Vstop_too_high);
    RUN_TEST(test_validateCV_rejects_Vstart_too_low);
    RUN_TEST(test_validateCV_rejects_zero_cycles);
    RUN_TEST(test_validateCV_rejects_zero_scanrate);
    RUN_TEST(test_validateDPV_valid_params);
    RUN_TEST(test_validateDPV_rejects_Vstop_too_high);
    RUN_TEST(test_validateDPV_rejects_zero_step);
    RUN_TEST(test_validateDPV_rejects_pulseWidth_ge_period);
    RUN_TEST(test_validateDPV_rejects_Veq_too_high);
    RUN_TEST(test_validateDPV_rejects_small_pulseWidth);
    RUN_TEST(test_validateDPV_rejects_insufficient_period_margin);

    return UNITY_END();
}
