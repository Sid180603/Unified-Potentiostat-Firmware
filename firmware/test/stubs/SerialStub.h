#ifndef SERIAL_STUB_H
#define SERIAL_STUB_H

/**
 * SerialStub.h — Fake Serial object for native testing
 *
 * Provides a minimal Serial API (available, peek, read, push)
 * backed by a std::deque<char>. Used to test checkAbort()
 * peek-before-consume logic without requiring <Arduino.h>.
 */

#include <deque>

struct FakeSerialT {
    std::deque<char> buf;

    void push(char c) {
        buf.push_back(c);
    }

    int available() {
        return (int)buf.size();
    }

    int peek() {
        if (buf.empty()) return -1;
        return (int)(unsigned char)buf.front();
    }

    int read() {
        if (buf.empty()) return -1;
        char c = buf.front();
        buf.pop_front();
        return (int)(unsigned char)c;
    }

    void clear() {
        buf.clear();
    }
};

// Global instance used by test code
static FakeSerialT FakeSerial;

#endif // SERIAL_STUB_H
