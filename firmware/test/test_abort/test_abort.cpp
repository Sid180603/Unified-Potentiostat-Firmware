/**
 * test_abort.cpp — Tests for checkAbort() peek-before-consume logic
 *
 * Uses SerialStub (FakeSerial) to simulate the Arduino Serial buffer.
 * Verifies:
 * - '!' returns true and is consumed
 * - Empty buffer returns false
 * - Non-'!' chars return false and are preserved
 *
 * Run with: pio test -e native
 */

#include <unity.h>
#include "../stubs/SerialStub.h"

// ============================================================
// checkAbort() — reimplemented using FakeSerial for testing
// This mirrors the exact logic in src/main.cpp but uses the stub.
// ============================================================

static bool checkAbort(void) {
    if (FakeSerial.available() && FakeSerial.peek() == '!') {
        FakeSerial.read();
        return true;
    }
    return false;
}

// ============================================================
// Tests
// ============================================================

void test_abort_true_on_bang(void) {
    FakeSerial.clear();
    FakeSerial.push('!');
    TEST_ASSERT_TRUE(checkAbort());
}

void test_abort_false_when_empty(void) {
    FakeSerial.clear();
    TEST_ASSERT_FALSE(checkAbort());
}

void test_abort_false_on_other_char(void) {
    FakeSerial.clear();
    FakeSerial.push('D');
    TEST_ASSERT_FALSE(checkAbort());
}

void test_abort_preserves_other_char(void) {
    FakeSerial.clear();
    FakeSerial.push('D');
    checkAbort();  // should NOT consume 'D'
    TEST_ASSERT_EQUAL_INT('D', FakeSerial.peek());
    TEST_ASSERT_EQUAL_INT(1, FakeSerial.available());
}

void test_abort_consumes_bang(void) {
    FakeSerial.clear();
    FakeSerial.push('!');
    checkAbort();
    TEST_ASSERT_EQUAL_INT(0, FakeSerial.available());
}

void test_abort_multiple_chars_bang_first(void) {
    FakeSerial.clear();
    FakeSerial.push('!');
    FakeSerial.push('C');
    TEST_ASSERT_TRUE(checkAbort());
    // 'C' remains in buffer
    TEST_ASSERT_EQUAL_INT(1, FakeSerial.available());
    TEST_ASSERT_EQUAL_INT('C', FakeSerial.peek());
}

void test_abort_multiple_chars_bang_second(void) {
    FakeSerial.clear();
    FakeSerial.push('C');
    FakeSerial.push('!');
    // peek sees 'C' first, so abort is false
    TEST_ASSERT_FALSE(checkAbort());
    // Both chars remain
    TEST_ASSERT_EQUAL_INT(2, FakeSerial.available());
}

// ============================================================
// Test Runner
// ============================================================

void setUp(void) { FakeSerial.clear(); }
void tearDown(void) {}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    UNITY_BEGIN();

    RUN_TEST(test_abort_true_on_bang);
    RUN_TEST(test_abort_false_when_empty);
    RUN_TEST(test_abort_false_on_other_char);
    RUN_TEST(test_abort_preserves_other_char);
    RUN_TEST(test_abort_consumes_bang);
    RUN_TEST(test_abort_multiple_chars_bang_first);
    RUN_TEST(test_abort_multiple_chars_bang_second);

    return UNITY_END();
}
